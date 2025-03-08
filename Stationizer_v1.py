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
    return output_fc_path

def update_handholes_stationing(handholes_fc, route_segments_fc, search_radius="10 Feet"):
    """
    Update the STATIONING field in handholes_fc using the stationing from the route segments.
    """
    # Create a feature layer for handholes
    handholes_layer = "handholes_layer"
    arcpy.MakeFeatureLayer_management(handholes_fc, handholes_layer)

    # Create a feature layer for route segments
    route_segments_layer = "route_segments_layer"
    arcpy.MakeFeatureLayer_management(route_segments_fc, route_segments_layer)

    # Add a new field "STATIONING" to the handholes feature class if it doesn't exist
    fields = [f.name for f in arcpy.ListFields(handholes_fc)]
    if "STATIONING" not in fields:
        arcpy.AddField_management(handholes_fc, "STATIONING", "TEXT", field_length=20)

    # Update the STATIONING field in handholes using the stationing from the route segments
    with arcpy.da.UpdateCursor(handholes_layer, ["SHAPE@", "STATIONING"]) as cursor:
        for row in cursor:
            handhole_geom = row[0]
            # Find the nearest route segment within the search radius
            arcpy.SelectLayerByLocation_management(route_segments_layer, "WITHIN_A_DISTANCE", handhole_geom, search_radius)
            with arcpy.da.SearchCursor(route_segments_layer, ["STATIONING"]) as route_cursor:
                for route_row in route_cursor:
                    row[1] = route_row[0]  # Update the STATIONING field
                    cursor.updateRow(row)
                    break  # Only update with the first matching route segment

    arcpy.AddMessage("Updated STATIONING field in handholes feature class.")

def stationing_migration_management(point_fc, connection_lines_fc, route_segments_fc, snap_distance="1 Feet", handholes_join_name="handholes_conn_lines_join", handholes_join_radius="10 Feet"):
    """
    1. Adds a new text field "STATIONING" to both the point_fc and the connection_lines_fc feature classes if absent.
    2. Creates a spatial join between connection_lines_fc (target) and route_segments_fc (join features) so that only when a 
       Connection_Lines geometry snaps to the end of a RouteSegments geometry (within snap_distance), 
       the STATIONING field from the route segment is joined.
    3. Updates the STATIONING field in connection_lines_fc using the joined results.
    4. Performs a spatial join between the handholes (point_fc) and the joined connection lines (conn_lines_join) so that
       each handhole gets the nearest connection line’s STATIONING value.
       
    Returns the path to the handholes spatial join feature class.
    """
    # Step 1: Add "STATIONING" field to point_fc if needed.
    point_fields = [f.name for f in arcpy.ListFields(point_fc)]
    if "STATIONING" not in point_fields:
        arcpy.AddField_management(point_fc, "STATIONING", "TEXT", field_length=20)
        arcpy.AddMessage("Added STATIONING field to point_fc.")
    else:
        arcpy.AddMessage("STATIONING field already exists in point_fc.")

    # Step 1: Add "STATIONING" field to connection_lines_fc if needed.
    connection_fields = [f.name for f in arcpy.ListFields(connection_lines_fc)]
    if "STATIONING" not in connection_fields:
        arcpy.AddField_management(connection_lines_fc, "STATIONING", "TEXT", field_length=20)
        arcpy.AddMessage("Added STATIONING field to connection_lines feature class.")
    else:
        arcpy.AddMessage("STATIONING field already exists in connection_lines feature class.")

    # Step 2: Spatial join between connection_lines_fc (target) and route_segments_fc (join features).
    # Create an output for the join. (Using in_memory workspace is recommended if available.)
    conn_lines_join = os.path.join(arcpy.env.workspace, "conn_lines_join")
    arcpy.SpatialJoin_analysis(
        target_features=connection_lines_fc,
        join_features=route_segments_fc,
        out_feature_class=conn_lines_join,
        join_operation="JOIN_ONE_TO_ONE",
        join_type="KEEP_COMMON",
        match_option="CLOSEST",
        search_radius=snap_distance
    )
    arcpy.AddMessage("Spatial join between Connection_Lines and RouteSegments completed.")

    # Step 3: Update the STATIONING field in connection_lines_fc using the joined results.
    # The spatial join creates a field "TARGET_FID" containing the OBJECTID from connection_lines_fc.
    # Join back the "STATIONING" field from the spatial join output.
    arcpy.JoinField_management(
        in_data=connection_lines_fc,
        in_field="OBJECTID",
        join_table=conn_lines_join,
        join_field="TARGET_FID",
        fields=["STATIONING"]
    )
    arcpy.AddMessage("Updated STATIONING field in Connection_Lines using spatial join with RouteSegments.")

    # Step 4: Perform a spatial join between handholes (point_fc) and the conn_lines_join to transfer the STATIONING value.
    out_join_fc = os.path.join(arcpy.env.workspace, handholes_join_name)
    arcpy.SpatialJoin_analysis(
        target_features=point_fc,
        join_features=conn_lines_join,
        out_feature_class=out_join_fc,
        join_operation="JOIN_ONE_TO_ONE",
        join_type="KEEP_ALL",
        match_option="CLOSEST",
        search_radius=handholes_join_radius
    )
    arcpy.AddMessage("Spatial join between Handholes and Conn_Lines_Join completed.")
    
    # Optionally delete the temporary conn_lines_join feature class.
    # arcpy.Delete_management(conn_lines_join)
    
    return out_join_fc

def transfer_attributes_from_small_to_large(map_name, small_layer_name, large_layer_name):
    """
    Transfers the STATIONING field from features in a small feature class (source) to 
    intersecting features in a larger feature class (target) within an ArcGIS Pro map.
    
    The function now uses both the geometry ("SHAPE@") and the STATIONING field from the source layer.
    Before reading the source features, the function sorts them ascending by the STATIONING field.
    """
    # Access the current ArcGIS project and map
    aprx = arcpy.mp.ArcGISProject("CURRENT")
    the_map = aprx.listMaps(map_name)[0]
    
    # Get layer objects for the small (source) and large (target) feature classes
    small_layer = the_map.listLayers(small_layer_name)[0]
    large_layer = the_map.listLayers(large_layer_name)[0]
    
    # Define fields: include geometry and the attribute we want to transfer
    source_fields = ["SHAPE@", "STATIONING"]
    large_field = "STATIONING"  # Field to update in the large layer
    
    # Specify SQL clause to sort ascending by STATIONING field.
    sql_clause = (None, "ORDER BY STATIONING ASC")
    
    # Use a SearchCursor to iterate over each feature in the small layer (source data)
    with arcpy.da.SearchCursor(small_layer, source_fields, sql_clause=sql_clause) as s_cursor:
        for s_row in s_cursor:
            small_geom = s_row[0]         # Geometry of the small feature
            small_value = s_row[1]          # Sorted STATIONING value from the small feature
            
            # Select features in the large layer that spatially intersect the current small feature
            arcpy.management.SelectLayerByLocation(
                in_layer=large_layer, 
                overlap_type="INTERSECT", 
                select_features=small_geom,  # using geometry of small feature as selecting feature
                selection_type="NEW_SELECTION"
            )
            
            # Only proceed if the selection is not empty
            count_selected = int(arcpy.management.GetCount(large_layer).getOutput(0))
            if count_selected == 0:
                continue  # no large features intersect this small feature, skip to next
            
            # Use an UpdateCursor on the large layer to update only the selected features
            with arcpy.da.UpdateCursor(large_layer, [large_field]) as u_cursor:
                for u_row in u_cursor:
                    u_row[0] = small_value
                    u_cursor.updateRow(u_row)
            
            # Clear the selection on the large layer to reset for next iteration
            arcpy.management.SelectLayerByAttribute(large_layer, "CLEAR_SELECTION")
    
    arcpy.AddMessage("Transferred STATIONING values from {} to {}.".format(small_layer_name, large_layer_name))

def clear_temp_feature_classes(workspace):
    """
    Deletes temporary feature classes: Append_Points, Connection_Lines, 
    Line_Points, and RouteSegments from the given workspace.
    """
    temp_fcs = ["Append_Points", "Connection_Lines", "Line_Points", "RouteSegments"]
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
    route_segments = generate_segments(line_fc, line_points, "RouteSegments")
    
    stationing_migration_management(point_fc, connection_lines_fc, route_segments, snap_distance="1 Feet", handholes_join_name="handholes_conn_lines_join", handholes_join_radius="10 Feet")

    # Update point_fc (Handholes) using an update cursor that writes sorted stationing values.
    # Here, for station_writer, we use route_segments as the source.
    transfer_attributes_from_small_to_large(map_name, small_layer_name="handholes_conn_lines_join", large_layer_name="Handholes")
    
    # Delete temporary feature classes.
    clear_temp_feature_classes(workspace)
    
    arcpy.AddMessage("Workflow complete. All feature classes updated with STATIONING values and temporary data deleted.")
    print("Workflow complete. All feature classes updated with STATIONING values and temporary data deleted.")

if __name__ == "__main__":
    main()
