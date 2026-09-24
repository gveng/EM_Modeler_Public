"""Capture genuine PySide6 UI screenshots for the help documentation.

Run from the repository root with ``python scripts/capture_help_screenshots.py``.
The utility only constructs UI widgets; it never exports or runs an EMERGE job.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "snapshots"


def demo_project_settings() -> dict:
    """Return a fresh, deterministic project-tree demo configuration."""
    return {
        "boundaries": {
            "Xmin": "PEC", "Xmax": "PML",
            "Ymin": "PML", "Ymax": "PML",
            "Zmin": "Radiation", "Zmax": "Radiation",
        },
        "ports": [{
            "number": 1, "type": "WaveguidePort",
            "x": 0, "y": 0, "z": 24,
            "params": {"Mode": "TE10", "Impedance_Ohm": 50.0},
        }],
        "simulations": [
            {
                "name": "Frequency_Sweep", "type": "Sweep", "enabled": True,
                "Fmin_GHz": 1.0, "Fmax_GHz": 12.0, "Fstep_GHz": 0.1,
                "NumberOfPoints": 111, "LogVerbosity": "Info",
            },
            {
                "name": "Resonant_Modes", "type": "Eigenmode", "enabled": True,
                "EigenmodeCount": 6, "NumberOfPoints": 100, "LogVerbosity": "Info",
            },
        ],
        "outputs": [
            {"name": "S_Parameters", "simulation": "Frequency_Sweep", "plot_type": "plot_sp", "enabled": True},
            {"name": "VSWR", "simulation": "Frequency_Sweep", "plot_type": "plot_vswr", "enabled": True},
        ],
        "mesh": {
            "default_fraction": 0.3,
            "object_resolutions": {"Radiator": 0.2},
            "local_refinements": [{
                "object": "Radiator", "enabled": True, "mode": "boundary",
                "faces": ["-z", "+z"], "size_mm": 0.25,
                "growth_rate": 3.0, "max_size_mm": None,
            }],
        },
    }


def capture_widget(widget, output_path: Path, app) -> None:
    """Show, render, and save one real Qt widget/window as a PNG."""
    widget.ensurePolished()
    widget.show()
    app.processEvents()
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(output_path), "PNG"):
        raise RuntimeError(f"Qt could not save screenshot: {output_path}")
    widget.hide()
    app.processEvents()


def capture_toolbar_crop(output_dir: Path) -> Path | None:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    source_path = output_dir / "Main_window.png"
    if not source_path.is_file():
        source_path = DEFAULT_OUTPUT_DIR / "Main_window.png"
    source = QImage(str(source_path))
    if source.isNull():
        return None
    crop = source.copy(QRect(0, 62, min(982, source.width()), min(82, source.height() - 62)))
    output_path = output_dir / "toolbar-icons.png"
    return output_path if crop.save(str(output_path), "PNG") else None


def capture_properties_and_parameters(app, output_dir: Path) -> list[Path]:
    from em3d_modeler.scene.em_objects import BoxObject
    from em3d_modeler.scene.param_expr import evaluate_expression
    from em3d_modeler.ui.body_properties_widget import BodyPropertiesWidget
    from em3d_modeler.ui.parameters_dialog import ParametersDialog

    variables = {"body_length": 40.0, "wall": 2.0}
    box = BoxObject("Parametric_Box", 0, 0, 0, 40, 20, 12, "MET_NICHROME")
    box.param_formulas = {"X2": "2 * body_length"}
    properties = BodyPropertiesWidget()
    properties.set_materials(["PEC", "MET_NICHROME"])
    properties.set_formula_resolver(lambda text: evaluate_expression(text, variables))
    properties.set_object(box)
    properties.resize(780, 430)
    properties_path = output_dir / "panel-body-properties.png"
    capture_widget(properties, properties_path, app)
    properties.close()

    parameters = ParametersDialog([
        {"name": "body_length", "value": 20.0, "unit": "mm"},
        {"name": "wall", "value": 2.0, "unit": "mm"},
    ])
    parameters.resize(620, 400)
    parameters_path = output_dir / "dialog-project-parameters.png"
    capture_widget(parameters, parameters_path, app)
    parameters.close()
    return [properties_path, parameters_path]


def capture_project_tree_sections(app, output_dir: Path) -> list[Path]:
    from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget

    tree_widget = ProjectTreeWidget()
    tree_widget.set_project_name("Dipole_Antenna")
    tree_widget.load_settings(demo_project_settings())
    tree = tree_widget._tree
    tree.setColumnWidth(0, 700)
    tree.setColumnWidth(1, 160)
    root = tree.topLevelItem(0)
    sections = [
        ("boundaries", tree_widget._b_node),
        ("ports", tree_widget._p_node),
        ("simulation", tree_widget._s_node),
        ("outputs", tree_widget._o_node),
        ("mesh", tree_widget._m_node),
    ]
    saved = []

    toolbar_path = capture_toolbar_crop(output_dir)
    if toolbar_path is not None:
        saved.append(toolbar_path)

    def count_visible_rows(item) -> int:
        count = 1
        if item.isExpanded():
            count += sum(count_visible_rows(item.child(index)) for index in range(item.childCount()))
        return count

    for name, section in sections:
        tree.collapseAll()
        root.setExpanded(True)
        section.setExpanded(True)
        for index in range(section.childCount()):
            child = section.child(index)
            child.setExpanded(True)
            for nested_index in range(child.childCount()):
                child.child(nested_index).setExpanded(True)
        row_count = count_visible_rows(root)
        tree_widget.resize(900, max(220, min(700, row_count * 27 + 56)))
        app.processEvents()
        path = output_dir / f"panel-project-tree-{name}.png"
        capture_widget(tree_widget, path, app)
        saved.append(path)
    tree_widget.close()
    return saved


def capture_object_materials_tree(app, output_dir: Path, project_file: Path | None = None) -> Path:
    from em3d_modeler.scene.em_objects import BoxObject, CylinderObject
    from em3d_modeler.ui.materials_widget import MaterialsWidget

    if project_file is not None:
        project_data = json.loads(project_file.read_text(encoding="utf-8"))
        project_objects = project_data.get("objects")
        if not isinstance(project_objects, list):
            raise ValueError(f"Project has no objects list: {project_file}")
        model_data = [item for item in project_objects if isinstance(item, dict) and item.get("is_model", True)][:3]
        non_model_data = [item for item in project_objects if isinstance(item, dict) and not item.get("is_model", True)]
        selected = model_data + non_model_data
        if not model_data or not non_model_data:
            raise ValueError(f"Project must contain both MODEL and NON MODEL objects: {project_file}")

        by_material = {}
        for item in selected:
            params = item.get("params", {})
            params = params if isinstance(params, dict) else {}
            visible = bool(item.get("visible", True))
            obj = SimpleNamespace(
                name=str(item.get("name", "Object")),
                material=str(params.get("Material", "PEC")),
                is_model=bool(item.get("is_model", True)),
                is_visible=lambda visible=visible: visible,
                actor=None,
                creation_reference_error="",
                pattern_definition=None,
            )
            by_material.setdefault(obj.material, []).append(obj)
    else:
        radiator = BoxObject("Radiator", -20, -2, 0, 20, 2, 42, "Copper")
        feed = CylinderObject("Feed", 0, 0, -4, 1.2, 8, "Z", "Copper")
        enclosure = BoxObject("Enclosure", -24, -6, -3, 24, 6, 45, "Aluminum")
        construction = BoxObject("Construction_Helper", 28, -3, 0, 34, 3, 12, "Copper")
        construction.is_model = False
        by_material = {
            "Copper": [radiator, feed],
            "Aluminum": [enclosure, construction],
        }

    widget = MaterialsWidget()
    widget.refresh(by_material)
    for index in range(widget._tree.topLevelItemCount()):
        item = widget._tree.topLevelItem(index)
        if item.text(0).startswith("NON MODEL"):
            item.setExpanded(True)
    rows = sum(len(objects) for objects in by_material.values()) + len(by_material) + 1
    widget.resize(480, min(900, max(360, rows * 34)))
    path = output_dir / "panel-object-materials.png"
    capture_widget(widget, path, app)
    widget.close()
    return path


def capture_dialogs(app, output_dir: Path) -> list[Path]:
    from em3d_modeler.emerge.material_store import MaterialStore
    from em3d_modeler.ui.material_library_dialog import MaterialLibraryDialog, _MaterialEditorDialog
    from em3d_modeler.ui.reference_plane_dialog import ReferencePlaneDialog
    from em3d_modeler.ui.settings_dialog import SettingsDialog
    from em3d_modeler.ui.sketch_widget import SketchDialog

    saved = []

    settings = SettingsDialog()
    settings._workspace_spin.setValue(200.0)
    settings._grid_spin.setValue(10.0)
    settings._adaptive_grid_margin_spin.setValue(20.0)
    settings._triad_size_spin.setValue(25.0)
    settings.resize(560, 420)
    for tab_name, tab_widget in (
        ("display", settings._display_tab),
        ("simulation", settings._simulation_tab),
        ("mesh", settings._mesh_tab),
    ):
        settings._tabs.setCurrentWidget(tab_widget)
        path = output_dir / f"dialog-settings-{tab_name}.png"
        capture_widget(settings, path, app)
        saved.append(path)
    settings.close()

    sketch = SketchDialog(plane_origin=(0.0, 0.0, 0.0), plane_normal=(0.0, 0.0, 1.0))
    sketch._canvas._elements = [
        ("rect", [(-25.0, -12.5), (25.0, 12.5)]),
        ("circle", [(0.0, 0.0), 6.0]),
    ]
    sketch._canvas.update()
    path = output_dir / "dialog-sketch.png"
    capture_widget(sketch, path, app)
    saved.append(path)
    sketch.close()

    store = MaterialStore()
    record = store.add_project_material(
        "Low-loss dielectric", family="Dielectrics", er=2.2,
        tan_d=0.0009, color="#4a9b8e", opacity=0.92,
    )
    store.add_project_material(
        "Copper", family="Metals", er=1.0, sigma=59600000.0,
        color="#c97845", opacity=1.0,
    )
    library = MaterialLibraryDialog(None, store, lambda: None)
    library.resize(980, 600)
    library._search.setText("Low-loss dielectric")
    if library._list.count():
        library._list.setCurrentRow(0)
    path = output_dir / "dialog-material-library.png"
    capture_widget(library, path, app)
    saved.append(path)
    library.close()

    editor = _MaterialEditorDialog(None, record)
    editor.resize(440, 380)
    path = output_dir / "dialog-material-editor.png"
    capture_widget(editor, path, app)
    saved.append(path)
    editor.close()

    plane = ReferencePlaneDialog(
        None, current_origin=(0.0, 0.0, 12.5),
        current_normal=(0.0, 0.0, 1.0), viewport=None,
    )
    path = output_dir / "dialog-reference-plane.png"
    capture_widget(plane, path, app)
    saved.append(path)
    plane.close()

    return saved


def capture_main_window(app, output_dir: Path) -> Path:
    from em3d_modeler.scene.em_objects import BoxObject
    from em3d_modeler.ui.main_window import MainWindow

    window = MainWindow()
    window._project_name = "Dipole Antenna"
    window._project_tree.set_project_name(window._project_name)
    window._project_tree.load_settings(demo_project_settings())
    window.setWindowTitle("EM 3D Modeler - Dipole Antenna")
    radiator = BoxObject("Radiator", -20, -1, 0, 20, 1, 42, "Copper")
    radiator.custom_color = (0.78, 0.39, 0.18)
    radiator.refresh_appearance()
    window._viewport.scene.add_object(radiator)
    helper = BoxObject("Construction_Helper", 28, -3, 0, 34, 3, 12, "Copper")
    helper.is_model = False
    helper.custom_color = (0.45, 0.55, 0.62)
    helper.refresh_appearance()
    window._viewport.scene.add_object(helper)
    window._viewport._reset_camera()
    window._refresh_materials()
    for index in range(window._materials._tree.topLevelItemCount()):
        item = window._materials._tree.topLevelItem(index)
        if item.text(0).startswith("NON MODEL"):
            item.setExpanded(True)
    window.resize(1440, 900)
    path = output_dir / "window-main.png"
    capture_widget(window, path, app)
    window.close()
    return path


def configure_capture_font(app) -> None:
    from PySide6.QtGui import QFont, QFontDatabase

    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
    family = "Segoe UI"
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
    app.setFont(QFont(family, 9))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="PNG destination (default: docs/snapshots)",
    )
    parser.add_argument(
        "--offscreen", action="store_true",
        help="use Qt's offscreen platform (VTK viewport capture may not work)",
    )
    parser.add_argument(
        "--skip-main-window", action="store_true",
        help="capture dialogs and project-tree panels only",
    )
    parser.add_argument(
        "--project-file", type=Path,
        help="populate the Object / Materials screenshot from a saved .em3d project",
    )
    parser.add_argument("--main-window-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(ROOT / "src"))

    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication

    QLocale.setDefault(QLocale.c())
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("EM 3D Modeler Screenshot Capture")
    configure_capture_font(app)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.main_window_only:
        try:
            path = capture_main_window(app, output_dir)
            print(path)
            return 0
        except Exception as exc:
            print(f"Main-window capture failed: {exc}", file=sys.stderr)
            return 1

    saved = []
    for capture in (
        lambda: capture_dialogs(app, output_dir),
        lambda: capture_project_tree_sections(app, output_dir),
        lambda: capture_properties_and_parameters(app, output_dir),
        lambda: [capture_object_materials_tree(app, output_dir, args.project_file)],
    ):
        try:
            saved.extend(capture())
        except Exception as exc:
            print(f"Capture group failed: {exc}", file=sys.stderr)

    if not args.skip_main_window:
        command = [
            sys.executable, str(Path(__file__).resolve()), "--main-window-only",
            "--output-dir", str(output_dir),
        ]
        if args.offscreen:
            command.append("--offscreen")
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=45)
        except subprocess.TimeoutExpired:
            print(
                "Main-window capture timed out; run the default command in a "
                "desktop session with a working OpenGL context.",
                file=sys.stderr,
            )
            result = None
        main_image = output_dir / "window-main.png"
        if result is not None and result.returncode == 0:
            saved.append(main_image)
        else:
            if result is not None:
                detail = result.stderr.strip().splitlines()
                message = detail[-1] if detail else f"child process exited {result.returncode}"
                print(f"Main-window capture unavailable: {message}", file=sys.stderr)

    if not saved:
        print("No screenshots were captured.", file=sys.stderr)
        return 1
    print(f"Saved {len(saved)} screenshots to {args.output_dir.resolve()}")
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())