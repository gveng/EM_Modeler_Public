# EM 3D Modeler

EM 3D Modeler is a desktop 3D CAD workspace for preparing electromagnetic
geometries and exporting them to the EMERGE simulation workflow. It combines a
VTK viewport, parametric primitives, CAD import, material assignment, boolean
modeling, and EMERGE-oriented project export in one application.

Current version: `1.4.5`

## What You Can Do

### Build and Edit Geometry

- Draw boxes, cylinders, cones, spheres, planar structures, and other supported
	primitives directly in the 3D viewport.
- Create parametric sketches, then extrude or revolve them into 3D geometry.
- Work on the XY, XZ, YZ, or a custom reference plane with a configurable grid
	and snap support for vertices, edges, and surfaces.
- Move, rotate, scale, duplicate, and delete selected objects.
- Create linear or circular patterns from one object or from a multi-object
	selection while preserving the group layout.

### Work with CAD and Boolean Solids

- Import STEP files (`.step` and `.stp`) as individual solids.
- Combine objects with Boolean Cut, Fuse, and Common operations.
- Use one base object with multiple tools in a single Boolean operation.
- Dissolve a Boolean result to recover its source objects.
- Preserve CAD face identity for Boolean results so surface selection acts on
	the complete resulting face instead of one display triangle.

### Select, Inspect, and Organize

- Select whole bodies, individual surfaces, edges, or vertices in the viewport.
- Select multiple objects from the Objects/Materials tree with `Ctrl` or `Shift`.
- Inspect and edit body parameters, material, color, opacity, and transforms.
- Group objects by material and apply a material to the current selection.
- Manage local project materials as well as a reusable global material database.

### Prepare EMERGE Simulations

- Save and reopen complete `.em3d` projects, including geometry, view settings,
	materials, reference planes, and Boolean-source metadata.
- Export EMERGE scripts and STEP bundles from the current model.
- Open the simulation panel to generate and run EMERGE Python workflows and
	review their output.

## Typical Workflow

1. Start a project and set the units, grid, and active reference plane.
2. Draw primitives, create sketches, or import STEP solids.
3. Assign materials and adjust object properties.
4. Use multi-selection for Boolean operations or pattern creation.
5. Inspect the result with body, surface, edge, and vertex selection modes.
6. Save the model as an `.em3d` project.
7. Export an EMERGE script or run the simulation workflow.

For the illustrated, linked UI reference, see [docs/HELP.html](docs/HELP.html).

## Installation

Requirements:

- Python 3.8 or newer
- Windows is the primary development and packaging target

Create and activate a virtual environment, then install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

STEP import and exact solid booleans use an OpenCascade-compatible backend when
available. Install CadQuery when that capability is not already present in your
Python environment:

```powershell
pip install cadquery
```

## Run the Application

From the repository root:

```powershell
python src/em3d_modeler/main.py
```

After editable installation, the command-line entry point is also available:

```powershell
em3d-modeler
```

## Build a Portable Distribution

The repository includes a PyInstaller build script for Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\Dist\Build-Portable.ps1
```

The generated portable folder is placed under `Dist/`, for example:

```text
Dist/EM3D_Modeler_1.3.4_Beta/
```

Run the executable inside that folder without installing the package into the
target user's Python environment.

## Publish a Public Snapshot

To publish the current committed version to a separate public repository, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\publish_public.ps1 `
	-RepositoryUrl https://github.com/your-account/your-public-repository.git `
	-Branch main
```

The script publishes only the committed `HEAD` tree, requires tracked changes to
be committed first, and uses a temporary clone. The public repository keeps its
own history; this repository's remotes and Git configuration are not changed.
Only files tracked by Git are included, so review the tracked files before the
first public release. Git's configured credential helper is used for access.

## Project Layout

```text
src/em3d_modeler/       Application source code
src/em3d_modeler/ui/    Qt user interface and viewport tools
src/em3d_modeler/scene/ Geometry, scene management, and Boolean operations
src/em3d_modeler/emerge/ EMERGE, materials, STEP, and export integration
docs/                   User documentation
tests/                  Example projects and test assets
scripts/                Build and maintenance scripts
```

## Shortcuts

| Command | Shortcut |
| --- | --- |
| New Project | `Ctrl+N` |
| Open Project | `Ctrl+O` |
| Save Project | `Ctrl+S` |
| Close Project | `Ctrl+W` |
| Delete Selected | `Delete` |
| Cancel Drawing | `Esc` |
