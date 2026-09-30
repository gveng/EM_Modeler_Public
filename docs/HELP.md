# EM 3D Modeler Help

**Version 1.4.0b2** · Release date 2026-09-30

The complete, illustrated user guide is [HELP.html](HELP.html). It contains a linked contents panel, workflow instructions, troubleshooting, and genuine screenshots captured from the application's PySide6 widgets.

## Guide sections

- Workspace, menus, viewport, selection modes including Grid, project tree, and the complete toolbar including Measure and Parameters
- Geometry creation, the embedded Sketch editor (alpha version) and its icon-by-icon toolbar guide, transforms including vertex-to-vertex copying, patterns, STEP, and Boolean operations
- Materials, object properties, formulas, and reference planes
- Settings, project parameters, and mesh controls
- Sweep, Eigenmode, and Parametric simulation jobs, ports, domains, boundaries, and local refinements
- Outputs, progressive S-parameter plots, PyQtGraph chart controls, S-parameter fitting and raw/fitted Touchstone export, simulation checks, script generation, and EMERGE execution
- Keyboard shortcuts, troubleshooting, and units

## Important simulation limitations

- Global Periodic boundaries and PlaneWave ports are shown by the UI but are rejected by the current simulation validator/export workflow.
- A selected global PML boundary requires an enabled, explicitly configured PML shell. The default project tree can show PML before such a shell exists; run **Check Simulation** before export/run.
- A LumpedPort zero direction is rejected by preflight even though the editor tooltip describes inferring the Plate normal. Enter a non-zero direction.
- **Check Simulation** is a local preflight, not an EMERGE run and not a guarantee that a requested solver backend is installed.
- Far-field outputs save field data; use **Generate Plot** on saved results. They are not rendered by the ordinary automatic S-parameter plotting step.
- If S-parameter fitting fails after a successful solve, the raw Touchstone samples are retained and automatic plots fall back to those samples; a fitted Touchstone file is not produced.
- Touchstone exports are stored in the simulation bundle's `Touchstone` folder. Use **Load Touchstone** in an S-parameter chart to open current or previous `.sNp` results; legacy files in the bundle root are organized into that folder when the picker opens.
- **Generate Plot** uses the latest matching saved Touchstone export when available, avoiding a reload of `simdata.emerge`; exports identify the simulation in their filename. Older project-only Touchstone names remain supported. If no usable Touchstone export exists, plots fall back to EMERGE result data.
- Cartesian S-parameter, VSWR, and Touchstone charts use PyQtGraph for interactive zoom/pan, X/Y linear or logarithmic scales, automatic/manual ranges, and movable measurement markers. Smith and far-field charts retain the EMERGE/Matplotlib renderer.
- Progressive S-parameter plotting is enabled per Sweep or Parametric job with **Enable progressive S-parameter plotting**; **Update every N points** sets the chunk size (default 10). Sweep jobs stream chunks while reusing one mesh; Parametric jobs stream the nested frequency sweep separately for each parameter value. To see live results, set the linked Output's **Plot timing** to **Live (progressive S-parameters)**. At least one port and EMERGE 3.0.0a16 are required; other versions fail closed when this option is enabled.
- Cartesian S-parameter, VSWR, and Touchstone plots use PyQtGraph with display transforms, linear/log axes, manual/automatic ranges, zoom/pan, and movable frequency markers. Smith and far-field plots use the EMERGE/Matplotlib renderer; the renderer is selected by plot type rather than a global graphics-mode switch.
- For a closed waveguide, an AIR containment error can indicate that open-region boundaries are enabled for an internal-air/PEC-wall model. Review the domain and boundary setup before changing geometry.

To regenerate the embedded sketch toolbar screenshot by itself, run `python scripts/capture_help_screenshots.py --sketch-toolbar-only`. Other screenshot instructions are in [SNAPSHOT_CAPTURE.md](SNAPSHOT_CAPTURE.md).

## Utility settings and Boolean mesh simplification

In **Settings → Utility**, **Export complete scene STEP** enables a separate full-scene STEP export. Enabling it marks that export for the next simulation preparation; it does not force a re-export of the ordinary per-object STEP bundle when that bundle is already current.

**Ask for mesh decimation on Fuse, Cut and Intersect** is off by default. When off, Boolean commands do not prompt and use 0% reduction. When on, each Fuse, Cut or Common (intersection) command asks for a target triangle reduction from 0% to 80% in 5% steps. A non-zero value simplifies the resulting mesh, is saved with the Boolean result, and is reapplied when that result is recomputed. STEP export uses the stored simplified mesh rather than rebuilding the exact source BREP. Simplification can change dimensions and small features; inspect clearances, via connections, and port contact before simulation. The target is a reduction request, so topology-preserving decimation may achieve less on difficult meshes.
