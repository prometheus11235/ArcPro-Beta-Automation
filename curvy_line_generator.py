import arcpy, os

def generate_segments(main_line_fc, snapped_points_fc, output_fc_name):
    """Generate polylines from the start of a main polyline to each snapped point along it."""
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
    
    # Read all polyline geometries into a dictionary (OID -> geometry) for quick access
    polyline_geoms = {}
    with arcpy.da.SearchCursor(main_line_fc, ["OID@", "SHAPE@"]) as line_cursor:
        for oid, shape in line_cursor:
            polyline_geoms[oid] = shape
    
    # Prepare an insert cursor to add new polyline segments to the output feature class
    insert_fields = ["SHAPE@"]  # we are only inserting the geometry
    with arcpy.da.InsertCursor(output_fc_path, insert_fields) as insert_cursor:
        # Iterate through each snapped point
        with arcpy.da.SearchCursor(snapped_points_fc, ["SHAPE@"]) as point_cursor:
            for point_geom, in point_cursor:
                # Identify which polyline this point lies on. If only one line, use it directly.
                if len(polyline_geoms) == 1:
                    line_geom = next(iter(polyline_geoms.values()))
                else:
                    # Find the polyline geometry that contains this point (distance == 0)
                    line_geom = None
                    for geom in polyline_geoms.values():
                        if geom.distanceTo(point_geom) == 0:
                            line_geom = geom
                            break
                    if line_geom is None:
                        continue  # skip point if it’s not on any polyline (should not happen if snapped)
                
                # Measure distance along the line from the start to the point’s position
                dist_along = line_geom.measureOnLine(point_geom, use_percentage=False)  
                # Create a polyline segment from the start (0) to this distance along the line
                segment = line_geom.segmentAlongLine(0, dist_along, use_percentage=False)
                
                # Insert the new segment geometry as a feature
                insert_cursor.insertRow([segment])
    # Return the path of the output feature class
    return output_fc_path

def main():
    # Set valid paths for your environment.
    main_line = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb\CENTERLINE_TEST"
    snapped_pts = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb\Line_Points"
    output_fc = "RouteSegments"
    result = generate_segments(main_line, snapped_pts, output_fc)
    print("Generated route segments at:", result)

if __name__ == "__main__":
    main()
