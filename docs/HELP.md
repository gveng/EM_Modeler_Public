# EM 3D Modeler Help

Version: 1.1.3
Release Date: 2026-05-18

## 1. Overview
EM 3D Modeler is a 3D CAD/EM environment for creating geometries, assigning materials, importing STEP files, performing boolean operations, and generating EMERGE scripts.

## 2. User Interface
- Left Panel: Project Tree and properties of the selected object.
- Center: 3D viewport and information bar.
- Right Panel: Objects/Materials, multi-selection, and quick actions.
- Toolbar: primitives, sketch, boolean operations, STEP import, grid, workspace, units, selection mode.

## 3. Creating Geometries
- Box, Cylinder, Cone, Sphere: click the icon and draw in the viewport.
- Sketch: opens parametric canvas for extrusion or revolution.

Tip:
- Set Plane and Grid first for more precise drawing.

## 4. Selection and Modification
- Single Selection: click on object in viewport or tree.
- Multi-Selection: use Ctrl/Shift in the materials tree.
- Delete Selected: removes the selected object.
- Body Properties: modify geometric parameters, material, color, and opacity.

## 5. Materials
- The Objects/Materials panel groups objects by material.
- Apply materials in bulk to multiple selected objects.
- Supported material databases:
  - Built-in
  - Project DB
  - Global DB
- File Menu:
  - Set Global Material DB
  - Reload Global Material DB

## 6. Boolean Operations
- Cut: subtracts Tool shapes from Base.
- Fuse: merges multiple objects.
- Common: keeps only the intersection.

Recommended workflow:
1. Select Base + Tool (or multiple Tools).
2. Confirm the preview window.
3. The result replaces the original objects.

Note on complex STEP geometry:
- For non-manifold or disjoint imported meshes, the system uses robust fallbacks to prevent crashes.

## 7. STEP Import
- File -> Import STEP or STEP Import button in toolbar.
- Supports .step and .stp files.
- Each imported solid is created as a separate MeshObject.

## 8. Reference Planes
- View -> Set Reference Plane to create custom planes.
- The active plane orients the grid and drawing tools.
- From the materials tree, you can rename, activate, or remove planes.

## 9. Saving and Export
- Save Project / Save Project As: saves project in .em3d format.
- Export EMERGE Script: generates an .em script compatible with EMERGE.

## 10. View and Navigation
- View Menu:
  - Reset Camera
  - Top (XY)
  - Front (XZ)
  - Right (YZ)
  - Isometric

## 11. Troubleshooting
- Boolean operation failed:
  - Verify that objects are valid with non-empty geometry.
  - For complex STEP files, use smaller and progressive selections.
- STEP import errors:
  - Check integrity of the CAD file.
  - Try re-exporting the STEP from the original CAD application.
- Material not visible in project:
  - Reload the Global DB and check for duplicate names.

## 12. Quick Commands
- New Project: Ctrl+N
- Open Project: Ctrl+O
- Save Project: Ctrl+S
- Quit: system shortcut (e.g., Alt+F4 on Windows)

## 13. About
In the Help -> About menu, you will find:
- Program name
- Version
- Release date
- Current date
