import arcpy

# Set the workspace (update the path to your geodatabase or folder)
arcpy.env.workspace = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb"

# Input feature classes (update these names/paths as needed)
point_fc = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb\Handholes"   # Input point feature class
line_fc = r"C:\Users\patri\Documents\PROJECTS\COTTONWOOD AREA 1\STO-C2-FDH47\GDBs\DEVELOPMENT.gdb\CENTERLINE_TEST"     # Input line feature class

# Output feature class names
append_points = "Append_Points"        # Copy of original points with connection number
line_points = "Line_Points"            # Feature class created from the XY Event Layer (points on the line)
connection_lines = "Connection_Lines"  # Final output: connection lines between each point and its nearest location on the line

# ---------------------------------------------------------------------
# STEP 1: Prepare the Point Data and Run Near Analysis
# ---------------------------------------------------------------------
# Ensure the point feature class has a unique connection identifier field.
fields = [f.name for f in arcpy.ListFields(point_fc)]
if "ConnectionNum" not in fields:
    arcpy.AddField_management(point_fc, "ConnectionNum", "SHORT")
    # Populate ConnectionNum with the ObjectID value.
    arcpy.CalculateField_management(point_fc, "ConnectionNum", "!OBJECTID!", "PYTHON3")
    arcpy.AddMessage("Added and calculated 'ConnectionNum' field on the input points.")

# Run the Near tool to calculate the nearest location on the line.
# This adds NEAR_DIST, NEAR_X, NEAR_Y, etc. to each point record.
arcpy.Near_analysis(point_fc, line_fc, search_radius="", location="LOCATION", angle="ANGLE")
arcpy.AddMessage("Executed Near analysis on the point feature class.")

# ---------------------------------------------------------------------
# STEP 2: Select Only Points Within 50 Feet of the Line
# ---------------------------------------------------------------------
# Create a feature layer from the point feature class with a SQL query to select points with NEAR_DIST <= 50.
where_clause = "NEAR_DIST <= 50"
selected_points_layer = "selected_points_layer"
arcpy.MakeFeatureLayer_management(point_fc, selected_points_layer, where_clause)
arcpy.AddMessage("Created a feature layer selecting points within 50 feet of the line.")

# ---------------------------------------------------------------------
# STEP 3: Export the Selected Points to a New Feature Class
# ---------------------------------------------------------------------
# This copy (append_points) will contain only the points that are within 50 feet of the line.
arcpy.FeatureClassToFeatureClass_conversion(selected_points_layer, arcpy.env.workspace, append_points)
arcpy.AddMessage(f"Exported selected points to feature class: {append_points}.")

# ---------------------------------------------------------------------
# STEP 4: Create an XY Event Layer from the Selected Points
# ---------------------------------------------------------------------
# The Near tool has added NEAR_X and NEAR_Y fields, so use these to create an event layer.
desc = arcpy.Describe(append_points)
sr = desc.spatialReference

xy_event_layer = "Line_Points_Layer"  # Name for the temporary event layer
arcpy.MakeXYEventLayer_management(append_points, "NEAR_X", "NEAR_Y", xy_event_layer, sr)
arcpy.AddMessage("Created XY Event Layer using NEAR_X and NEAR_Y from the selected points.")

# ---------------------------------------------------------------------
# STEP 5: Export the XY Event Layer to a Feature Class
# ---------------------------------------------------------------------
# Convert the temporary XY event layer (which represents the connection points on the line) to a permanent feature class.
arcpy.FeatureClassToFeatureClass_conversion(xy_event_layer, arcpy.env.workspace, line_points)
arcpy.AddMessage(f"Exported XY Event Layer to feature class: {line_points}.")

# ---------------------------------------------------------------------
# STEP 6: Append the Connection Points to the Selected Points
# ---------------------------------------------------------------------
# Append the projected (connection) points (line_points) to the filtered copy of original points (append_points).
arcpy.Append_management(line_points, append_points, "NO_TEST")
arcpy.AddMessage("Appended connection points to the selected points feature class.")

# ---------------------------------------------------------------------
# STEP 7: Create Connection Lines Using the Points To Line Tool
# ---------------------------------------------------------------------
# Using the ConnectionNum field, create a line connecting the original point and its corresponding projected point.
arcpy.PointsToLine_management(append_points, connection_lines, "ConnectionNum")
arcpy.AddMessage(f"Created connection lines: {connection_lines}")

print("Connection lines created successfully for points within 50 feet of the line.")
