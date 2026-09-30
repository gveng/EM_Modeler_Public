# Help Screenshot Capture

From the repository root, run:

```powershell
python scripts/capture_help_screenshots.py
```

The utility creates genuine screenshots from the application's PySide6 main
window and dialog/widget classes and writes PNG files to `docs/snapshots/`.
The current full-window capture is `window-main.png`; the guide's main toolbar
crop is regenerated from that image. The legacy `Main_window.png` is not
replaced by the utility.
The utility also captures the real Objects / Materials widget with material-
grouped MODEL objects and an expanded NON MODEL group containing a visual-only
construction helper. It captures Body Properties with a selected parametric
box, a resolved dimension formula, and the Project Parameters editor. When
`window-main.png` is present in the output (or default snapshots) it also saves
`toolbar-icons.png`, a crop of the actual toolbar, including the Tools group,
rather than a recreated icon graphic.
The embedded sketch-context toolbar is captured separately from the actual
main window while sketch mode is active. Run only this capture with:

```powershell
python scripts/capture_help_screenshots.py --sketch-toolbar-only
```

It writes `toolbar-sketch-context.png` without regenerating the other screenshots.
Use `--output-dir D:\path\to\folder` to choose another destination. PySide6
and the application's normal Python dependencies must be installed.

The demo content is defined in `scripts/capture_help_screenshots.py`, so the
same project-tree values and material records are used on each run. To populate
the Object / Materials tree screenshot from a saved project instead, pass its
path with `--project-file`:

```powershell
python scripts/capture_help_screenshots.py --offscreen --skip-main-window --project-file tests\GV_For_Sym\Rafale_Probes.em3d
```

That panel capture uses the first three MODEL objects and every NON MODEL
object from the project, preserving their material names and visibility state.
Other dialog and Project Tree captures continue to use deterministic demo
values. The Body Properties capture uses `X2 = 2 * body_length`, with the
variable set to 20, so the Resolved column shows 40. The utility does not edit
`HELP.html` or `HELP.md`, start an EMERGE
simulation, or export a project. Run the full capture in a normal desktop session with Qt's native
platform because the VTK viewport needs a working graphics context. The utility
honors `QT_QPA_PLATFORM` if it is already set. On machines without a usable
graphics context, the main-window capture is isolated and reported as
unavailable after a 45-second timeout while the other screenshots are still
saved. For a headless run that captures only dialogs and tree panels, add
`--offscreen --skip-main-window`.
Windows captures use the installed Segoe UI font when available.

The HTML guide references genuine PNG captures only. The older SVG files in
this directory are illustrative diagrams, not screenshots of the application.

To validate without overwriting documentation images, use a temporary folder:

```powershell
python scripts/capture_help_screenshots.py --output-dir $env:TEMP\em3d-screenshots --offscreen --skip-main-window
```

Validate all local links, section anchors and screenshot assets with:

```powershell
python scripts/validate_help.py
```