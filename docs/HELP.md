# EM 3D Modeler Help

**Version 1.2.10** · Release date 2026-09-24

The complete, illustrated user guide is [HELP.html](HELP.html). It contains a linked contents panel, workflow instructions, troubleshooting, and genuine screenshots captured from the application's PySide6 widgets.

## Guide sections

- Workspace, menus, viewport, selection, and project tree
- Geometry creation, sketches, transforms, patterns, STEP, and Boolean operations
- Materials, object properties, formulas, and reference planes
- Settings, project parameters, and mesh controls
- Simulation jobs, ports, domains, boundaries, and local refinements
- Outputs, simulation checks, script generation, and EMERGE execution
- Keyboard shortcuts, troubleshooting, and units

## Important simulation limitations

- Global Periodic boundaries and PlaneWave ports are shown by the UI but are rejected by the current simulation validator/export workflow.
- A selected global PML boundary requires an enabled, explicitly configured PML shell. The default project tree can show PML before such a shell exists; run **Check Simulation** before export/run.
- A LumpedPort zero direction is rejected by preflight even though the editor tooltip describes inferring the Plate normal. Enter a non-zero direction.
- **Check Simulation** is a local preflight, not an EMERGE run and not a guarantee that a requested solver backend is installed.
- Far-field outputs save field data; use **Generate Plot** on saved results. They are not rendered by the ordinary automatic S-parameter plotting step.

To regenerate genuine help screenshots, see [SNAPSHOT_CAPTURE.md](SNAPSHOT_CAPTURE.md).
