import arcpy, os
from shapely.geometry import shape, mapping
import fiona
from fiona.crs import from_epsg

def prepare_point_data_and_run_near(point_fc, line_fc):
    # Ensure the point feature class has a unique connection identifier field.
    fields = [f.name for f in arcpy.ListFields(point_fc)]
    if "ConnectionNum" not in fields:
        arcpy.AddField_management(point_fc, "ConnectionNum", "SHORT")
        # Populate ConnectionNum with the ObjectID value.
        arcpy.CalculateField_management(point_fc, "ConnectionNum", "!OBJECTID!", "PYTHON3")
        arcpy.AddMessage("Added and calculated 'ConnectionNum' field on the input points.")
    # Run the Near tool to calculate the nearest location on the line.
    arcpy.Near_analysis(point_fc, line_fc, search_radius="", location="LOCATION", angle="ANGLE")
    arcpy.AddMessage("Executed Near analysis on the point feature class.")

def select_points_within_50ft(point_fc):
    # Create a feature layer from the point feature class with a SQL query to select points with NEAR_DIST <= 50.
    where_clause = "NEAR_DIST <= 50"
    selected_points_layer = "selected_points_layer"
    arcpy.MakeFeatureLayer_management(point_fc, selected_points_layer, where_clause)
    arcpy.AddMessage("Created a feature layer selecting points within 50 feet of the line.")
    return selected_points_layer

def export_selected_points(selected_points_layer, workspace, output_fc):
    # Export the selected points to a new feature class.
    arcpy.FeatureClassToFeatureClass_conversion(selected_points_layer, workspace, output_fc)
    arcpy.AddMessage(f"Exported selected points to feature class: {output_fc}.")

def create_xy_event_layer(input_fc):
    # Create an XY Event Layer from the selected points using NEAR_X and NEAR_Y fields.
    desc = arcpy.Describe(input_fc)
    sr = desc.spatialReference
    xy_event_layer = "Line_Points_Layer"
    arcpy.MakeXYEventLayer_management(input_fc, "NEAR_X", "NEAR_Y", xy_event_layer, sr)
    arcpy.AddMessage("Created XY Event Layer using NEAR_X and NEAR_Y from the selected points.")
    return xy_event_layer

def export_xy_event_layer(xy_event_layer, workspace, output_fc):
    # Convert the XY Event Layer to a permanent feature class.
    arcpy.FeatureClassToFeatureClass_conversion(xy_event_layer, workspace, output_fc)
    arcpy.AddMessage(f"Exported XY Event Layer to feature class: {output_fc}.")

def append_connection_points(conn_points_fc, target_fc):
    # Append the projected (connection) points to the filtered copy of original points.
    arcpy.Append_management(conn_points_fc, target_fc, "NO_TEST")
    arcpy.AddMessage("Appended connection points to the selected points feature class.")

def create_connection_lines(input_fc, output_fc):
    # Create connection lines using the Points To Line tool by connecting points with the same ConnectionNum.
    arcpy.PointsToLine_management(input_fc, output_fc, "ConnectionNum")
    arcpy.AddMessage(f"Created connection lines: {output_fc}")

def create_shapely_buffer(line_fc, buffer_fc_path):
    """
    Creates a flat 50ft buffer from line_fc using Shapely and writes it to a shapefile.
    """
    # Remove existing buffer shapefile if it exists.
    if arcpy.Exists(buffer_fc_path) or os.path.exists(buffer_fc_path):
        arcpy.Delete_management(buffer_fc_path)
    
    # Get the first geometry from the line feature class.
    with arcpy.da.SearchCursor(line_fc, ["SHAPE@"]) as cursor:
        for row in cursor:
            line_geom = row[0]
            break

    # Convert to a Shapely geometry.
    shapely_line = shape(line_geom.__geo_interface__)
    # Create a 50ft flat buffer (cap_style=2 for flat).
    shapely_buffer = shapely_line.buffer(50, cap_style=2)

    # Get spatial reference factory code.
    desc = arcpy.Describe(line_fc)
    sr = desc.spatialReference
    try:
        epsg_code = sr.factoryCode
    except Exception:
        epsg_code = 4326  # Default to WGS84 if unavailable.
    
    crs = from_epsg(epsg_code)
    schema = { "geometry": "Polygon", "properties": {"id": "int"} }
    
    # Write the shapely buffer to a shapefile.
    with fiona.open(buffer_fc_path, "w", driver="ESRI Shapefile", crs=crs, schema=schema) as sink:
        sink.write({
            "geometry": mapping(shapely_buffer),
            "properties": {"id": 1}
        })
    
    arcpy.AddMessage(f"Created shapely buffer shapefile: {buffer_fc_path}")
    return buffer_fc_path

def delete_features_outside_buffer(line_fc, feature_classes, workspace):
    """
    1. Creates a flat 50ft buffer from line_fc using Shapely.
    2. For each provided feature class, selects features that are not completely within the buffer,
       including those along the edge.
    3. Deletes those feature geometries.
    """
    # Define the output path for the shapely buffer shapefile.
    # Adjust this path as needed.
    buffer_fc_path = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\SCRIPTS\EXPERIMENTS\Line_Buffer_50ft.shp"
    buffer_fc = create_shapely_buffer(line_fc, buffer_fc_path)

    # For each feature class, select features that are NOT completely within the buffer.
    for fc in feature_classes:
        layer_name = f"{fc}_layer"
        arcpy.MakeFeatureLayer_management(fc, layer_name)
        # Select features that are NOT completely within the buffer (including those touching its edge).
        arcpy.SelectLayerByLocation_management(layer_name, "COMPLETELY_WITHIN", buffer_fc,
                                                 selection_type="NEW_SELECTION",
                                                 invert_spatial_relationship=True)
        arcpy.AddMessage(f"Selected features in {fc} that are not completely within the buffer (including edge features).")
        arcpy.DeleteFeatures_management(layer_name)
        arcpy.AddMessage(f"Deleted features in {fc} that were outside or along the edge of the buffer.")

    # Clean up the temporary buffer shapefile.
    arcpy.Delete_management(buffer_fc)
    arcpy.AddMessage("Cleaned up temporary buffer shapefile.")

def format_station(distance_ft):
    """
    Convert a distance in feet (float) to a station string.
    Examples:
        91.521 ft  -> 00+92 (rounded)
        124.836 ft -> 01+25 (rounded)
    """
    dist_rounded = int(round(distance_ft))
    hundreds = dist_rounded // 100
    remainder = dist_rounded % 100
    # Format as XX+YY (leading zeros if needed)
    return f"{hundreds:02d}+{remainder:02d}"

def generate_segments(main_line_fc, snapped_points_fc, output_fc_name):
    """Generate polylines from the start of a main polyline to each snapped point along it.
       For each segment, calculate its length in feet, translate to station format,
       and write that value to a new "STATIONING" text field.
    """
    arcpy.env.overwriteOutput = True  # Overwrite output if it exists

    # Determine the geodatabase workspace and spatial reference from the main polyline feature class
    workspace = os.path.dirname(arcpy.Describe(main_line_fc).catalogPath)  # path to the .gdb
    spatial_ref = arcpy.Describe(main_line_fc).spatialReference

    # Create the output feature class in the same geodatabase
    output_fc_path = os.path.join(workspace, output_fc_name)
    if arcpy.Exists(output_fc_path):
        arcpy.management.Delete(output_fc_path)
    arcpy.management.CreateFeatureclass(workspace, output_fc_name, "POLYLINE", 
                                          spatial_reference=spatial_ref)

    # Add a new text field "STATIONING" to the output feature class.
    arcpy.AddField_management(output_fc_path, "STATIONING", "TEXT", field_length=20)
    arcpy.AddField_management(output_fc_path, "SEGMENT_ID", "LONG")

    # Read all polyline geometries into a dictionary (OID -> geometry) for quick access
    polyline_geoms = {}
    with arcpy.da.SearchCursor(main_line_fc, ["OID@", "SHAPE@"]) as line_cursor:
        for oid, geom in line_cursor:
            polyline_geoms[oid] = geom

    # Prepare an insert cursor to add new polyline segments to the output feature class
    # Now inserting both geometry and stationing string.
    insert_fields = ["SHAPE@", "STATIONING"]
    with arcpy.da.InsertCursor(output_fc_path, insert_fields) as insert_cursor:
        # Iterate through each snapped point
        with arcpy.da.SearchCursor(snapped_points_fc, ["SHAPE@"]) as point_cursor:
            for (point_geom,) in point_cursor:
                # Identify which polyline this point lies on; if only one, use it.
                if len(polyline_geoms) == 1:
                    line_geom = next(iter(polyline_geoms.values()))
                else:
                    line_geom = None
                    for geom in polyline_geoms.values():
                        if geom.distanceTo(point_geom) == 0:
                            line_geom = geom
                            break
                    if line_geom is None:
                        continue  # skip point if it’s not on any polyline

                # Measure distance along the line from the start to the point’s position
                dist_along = line_geom.measureOnLine(point_geom, use_percentage=False)  
                # Create a polyline segment from the start (0) to this distance along the line
                segment = line_geom.segmentAlongLine(0, dist_along, use_percentage=False)
                # Calculate the station string using the segment's length
                station_str = format_station(segment.length)
                # Insert the new segment geometry along with its stationing value
                insert_cursor.insertRow([segment, station_str])
    arcpy.AddMessage(f"Generated segments feature class: {output_fc_path}")
    
    # Sorting the segments by their length (Shape_Length) and sequentially updating SEGMENT_ID.
    rows = []
    with arcpy.da.SearchCursor(output_fc_path, ["OID@", "SHAPE@"]) as s_cursor:
        for row in s_cursor:
            rows.append((row[0], row[1].length))
    rows.sort(key=lambda x: x[1])
    
    sorted_ids = {}
    segment_id = 1
    for oid, _ in rows:
        sorted_ids[oid] = segment_id
        segment_id += 1
    
    with arcpy.da.UpdateCursor(output_fc_path, ["OID@", "SEGMENT_ID"]) as u_cursor:
        for row in u_cursor:
            row[1] = sorted_ids[row[0]]
            u_cursor.updateRow(row)
    
    return output_fc_path

def createEndPoints():

    # Set environment settings
    arcpy.env.overwriteOutput = True
    workspace = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb"
    arcpy.env.workspace = workspace

    # Input polyline feature class
    polyline_fc = "RouteSegments"  

    # Output feature class to store the last vertices as points
    output_points = "EndPoints"

    # Get spatial reference from input feature class
    desc = arcpy.Describe(polyline_fc)
    spatial_ref = desc.spatialReference

    # Delete output if it exists
    if arcpy.Exists(output_points):
        arcpy.Delete_management(output_points)

    # Create a new point feature class for storing last vertex points
    arcpy.CreateFeatureclass_management(
        out_path=workspace, 
        out_name=output_points, 
        geometry_type="POINT", 
        spatial_reference=spatial_ref
    )

    # Add the two additional fields to the output feature class:
    # SEGMENT_ID as an integer and STATIONING as a text field (length=50)
    arcpy.AddField_management(output_points, "SEGMENT_ID", "LONG")
    arcpy.AddField_management(output_points, "STATIONING", "TEXT", field_length=50)

    # Use a SearchCursor on the input polyline fc to retrieve the geometry and the two fields,
    # then an InsertCursor on the output fc to add the last vertex along with these values.
    with arcpy.da.SearchCursor(polyline_fc, ["SHAPE@", "SEGMENT_ID", "STATIONING"]) as sCursor, \
        arcpy.da.InsertCursor(output_points, ["SHAPE@", "SEGMENT_ID", "STATIONING"]) as iCursor:
        for row in sCursor:
            polyline = row[0]
            seg_id = row[1]
            stationing = row[2]
            if polyline:
                # Get the last vertex from the polyline
                last_vertex = polyline.lastPoint
                if last_vertex:
                    # Create a point geometry from the last vertex using the same spatial reference
                    last_pt_geom = arcpy.PointGeometry(last_vertex, spatial_ref)
                    # Insert a new row into the output with the last vertex and field values
                    iCursor.insertRow([last_pt_geom, seg_id, stationing])

def selectionpaluza():
    # Get the current ArcGIS project and the map by name.
    aprx = arcpy.mp.ArcGISProject("CURRENT")
    map_name = "DESIGN"
    the_map = aprx.listMaps(map_name)[0]

    # Retrieve the necessary layers from the map.
    handholes = the_map.listLayers("Handholes")[0]
    RouteSegments = the_map.listLayers("RouteSegments")[0]
    Connection_Lines = the_map.listLayers("Connection_Lines")[0]

    # Initialize a dictionary to store stationing values for each segment.
    # The dictionary key is SEGMENT_ID and the value is the corresponding STATIONING string.
    stationingDict = {}

    # Populate the dictionary by iterating through RouteSegments.
    # This ensures we map each segment's ID to its stationing value.
    with arcpy.da.SearchCursor(RouteSegments, ["SEGMENT_ID", "STATIONING"]) as cursor:
        for row in cursor:
            stationingDict[row[0]] = row[1]

    # Create the EndPoints feature class that stores the last vertices of RouteSegments.
    createEndPoints()

    # Retrieve the EndPoints layer from the map.
    EndPoints = the_map.listLayers("EndPoints")[0]

    # Loop through each segment (by SEGMENT_ID) to update the associated stationing values.
    for id in stationingDict.keys():
        # Build a selection query to isolate the endpoint corresponding to the current segment.
        query = "SEGMENT_ID = " + str(id)
        arcpy.SelectLayerByAttribute_management(EndPoints, "NEW_SELECTION", query)
        
        # Select connection lines that are within 1 foot of the selected endpoint.
        arcpy.SelectLayerByLocation_management(Connection_Lines, "WITHIN_A_DISTANCE", EndPoints, "1 foot", "ADD_TO_SELECTION")
        
        # Further select handholes that are within 1 foot of the connection lines.
        arcpy.SelectLayerByLocation_management(handholes, "WITHIN_A_DISTANCE", Connection_Lines, "1 foot", "ADD_TO_SELECTION")
        
        # Update the STATIONING field in the selected handholes to match the stationing for the current segment.
        with arcpy.da.UpdateCursor(handholes, ["STATIONING"]) as line_cursor:
            for row in line_cursor:
                row[0] = stationingDict[id]
                line_cursor.updateRow(row)
        
        # Clear selections on EndPoints, Connection_Lines, and handholes to prepare for the next iteration.
        arcpy.SelectLayerByAttribute_management(EndPoints, "CLEAR_SELECTION")
        arcpy.SelectLayerByAttribute_management(Connection_Lines, "CLEAR_SELECTION")
        arcpy.SelectLayerByAttribute_management(handholes, "CLEAR_SELECTION")

def clear_temp_feature_classes(workspace):
    """
    Deletes temporary feature classes: Append_Points, Connection_Lines, 
    Line_Points, and RouteSegments from the given workspace.
    """
    temp_fcs = ["Append_Points", "Connection_Lines", "Line_Points", "RouteSegments","EndPoints"]
    for fc in temp_fcs:
        fc_path = os.path.join(workspace, fc)
        if arcpy.Exists(fc_path):
            arcpy.Delete_management(fc_path)
            arcpy.AddMessage(f"Deleted temporary feature class: {fc_path}")
        else:
            arcpy.AddMessage(f"Temporary feature class not found: {fc_path}")

def main():
    # Set the workspace
    workspace = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb"
    arcpy.env.workspace = workspace

    # Define input and output feature classes.
    point_fc = os.path.join(workspace, "Handholes")
    line_fc = os.path.join(workspace, "CENTERLINE_TEST")
    append_points = "Append_Points"        # Points within 50ft with connection IDs.
    line_points = "Line_Points"            # Connection points on the line feature class.
    connection_lines = "Connection_Lines"  # Final output of connection lines.
    connection_lines_fc = os.path.join(workspace, "Connection_Lines")
    map_name = "DESIGN"  # Name of the map containing the layers.

    # Execute workflow steps.
    prepare_point_data_and_run_near(point_fc, line_fc)
    selected_points_layer = select_points_within_50ft(point_fc)
    export_selected_points(selected_points_layer, workspace, append_points)
    
    xy_event_layer = create_xy_event_layer(append_points)
    export_xy_event_layer(xy_event_layer, workspace, line_points)
    
    append_connection_points(line_points, append_points)
    create_connection_lines(append_points, connection_lines)
    
    # Delete features outside the 50ft buffer (including those along its edge)
    delete_features_outside_buffer(line_fc, [append_points, line_points, connection_lines], workspace)
    
    # Generate segments using the main line and the snapped points (Line_Points)
    generate_segments(line_fc, line_points, "RouteSegments")

    selectionpaluza()

    # Delete temporary feature classes.
    clear_temp_feature_classes(workspace)
    
    arcpy.AddMessage("Workflow complete. All feature classes updated with STATIONING values and temporary data deleted.")
    print("Workflow complete. All feature classes updated with STATIONING values and temporary data deleted.")

if __name__ == "__main__":
    main()
