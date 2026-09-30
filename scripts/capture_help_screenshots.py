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
                "name": "Length_Sweep", "type": "Parametric", "enabled": True,
                "ParamName": "antenna_length", "ParamValuesMode": "range",
                "ParamStart": "40", "ParamEnd": "60", "ParamStep": "10",
                "ParamValues": "40,50,60", "Fmin_GHz": 8.0,
                "Fmax_GHz": 10.0, "Fstep_GHz": 0.02,
                "NumberOfPoints": 101, "progressive_sparams_enabled": True,
                "progressive_sparams_chunk_size": 5, "LogVerbosity": "Info",
            },
            {
                "name": "Resonant_Modes", "type": "Eigenmode", "enabled": True,
                "EigenmodeCount": 6, "NumberOfPoints": 100, "LogVerbosity": "Info",
            },
        ],
        "parameters": [
            {"name": "antenna_length", "value": 50.0, "unit": "mm"},
            {"name": "wall", "value": 2.0, "unit": "mm"},
        ],
        "outputs": [
            {"name": "S_Parameters", "simulation": "Frequency_Sweep", "plot_type": "plot_sp", "enabled": True},
            {"name": "VSWR", "simulation": "Frequency_Sweep", "plot_type": "plot_vswr", "enabled": True},
            {
                "name": "Parametric_S11", "simulation": "Length_Sweep",
                "plot_type": "plot_sp", "plot_mode": "live", "enabled": True,
                "params": {"s_parameters": ["S11", "S21"]},
            },
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

    source_path = output_dir / "window-main.png"
    if not source_path.is_file():
        source_path = output_dir / "Main_window.png"
    if not source_path.is_file():
        source_path = DEFAULT_OUTPUT_DIR / "window-main.png"
    if not source_path.is_file():
        source_path = DEFAULT_OUTPUT_DIR / "Main_window.png"
    source = QImage(str(source_path))
    if source.isNull():
        return None
    crop = source.copy(QRect(0, 62, min(1200, source.width()), min(82, source.height() - 62)))
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
    from em3d_modeler.ui.material_assign_dialog import MaterialAssignDialog
    from em3d_modeler.ui.material_library_dialog import MaterialLibraryDialog, _MaterialEditorDialog
    from em3d_modeler.ui.reference_plane_dialog import ReferencePlaneDialog
    from em3d_modeler.ui.settings_dialog import SettingsDialog

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
        ("utility", settings._utility_tab),
    ):
        settings._tabs.setCurrentWidget(tab_widget)
        path = output_dir / f"dialog-settings-{tab_name}.png"
        capture_widget(settings, path, app)
        saved.append(path)
    settings.close()

    store = MaterialStore()
    record = store.add_project_material(
        "Low-loss dielectric", family="Dielectrics", er=2.2,
        tan_d=0.0009, color="#4a9b8e", opacity=0.92,
    )
    copper_record = store.add_project_material(
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

    assign = MaterialAssignDialog(
        None,
        project_records={record.name: record, copper_record.name: copper_record},
        selected_name=record.name,
    )
    assign_path = output_dir / "dialog-assign-material.png"
    capture_widget(assign, assign_path, app)
    saved.append(assign_path)
    assign.close()

    return saved


def capture_project_tree_dialogs(app, output_dir: Path) -> list[Path]:
        from PySide6.QtWidgets import QDialog, QWidget
        from em3d_modeler.ui.main_window import MainWindow
        from em3d_modeler.ui.project_tree_widget import ProjectTreeWidget

        tree = ProjectTreeWidget()
        tree.set_project_name("Dipole_Antenna")
        tree.load_settings(demo_project_settings())
        tree._scene_object_names = ["Air_Region", "Radiator"]
        captures = {
            "Parametric Simulation": ("dialog-simulation-parametric.png", (760, 760)),
            "Live S-parameter Output": ("dialog-output-live.png", (720, 620)),
            "Waveguide Port": ("dialog-port-waveguide.png", (620, 540)),
            "Lumped Port": ("dialog-port-lumped.png", (620, 540)),
            "Edit 2 Boundaries": ("dialog-boundaries.png", (520, 280)),
            "Select open-region domain": ("dialog-open-region-domain.png", (520, 240)),
            "Edit Mesh Refinement - Radiator": ("dialog-local-mesh-refinement.png", (560, 420)),
            "Assign Boundary Condition": ("dialog-object-boundary.png", (600, 520)),
            "Open Region / PML Wizard": ("dialog-open-region-pml.png", (620, 520)),
        }
        saved = []
        original_exec = QDialog.exec
        original_exec_ = getattr(QDialog, "exec_", None)

        def capture_exec(dialog) -> int:
            entry = captures.get(dialog.windowTitle())
            if entry is not None:
                filename, size = entry
                dialog.resize(*size)
                path = output_dir / filename
                capture_widget(dialog, path, app)
                saved.append(path)
            return QDialog.Rejected

        QDialog.exec = capture_exec
        if original_exec_ is not None:
            QDialog.exec_ = capture_exec
        try:
            tree._simulation_dialog_data({
                "name": "Length_Sweep",
                "type": "Parametric",
                "enabled": True,
                "ParamName": "antenna_length",
                "ParamValuesMode": "list",
                "ParamValues": "40, 50, 60",
                "Fmin_GHz": 8.0,
                "Fmax_GHz": 10.0,
                "Fstep_GHz": 0.02,
                "NumberOfPoints": 101,
                "progressive_sparams_enabled": True,
                "progressive_sparams_chunk_size": 5,
            }, "Parametric Simulation")
            tree._output_dialog_data({
                "name": "Parametric_S11",
                "simulation": "Length_Sweep",
                "plot_type": "plot_sp",
                "plot_mode": "live",
                "enabled": True,
                "params": {"s_parameters": ["S11", "S21"]},
            }, "Live S-parameter Output")
            tree._port_dialog_data({
                "number": 1,
                "name": "Input",
                "type": "WaveguidePort",
                "x": 0,
                "y": 0,
                "z": 24,
                "params": {"Mode": "TE10", "Impedance_Ohm": 50.0},
            }, "Radiator", "Waveguide Port")
            tree._port_dialog_data({
                "number": 2,
                "name": "Feed",
                "type": "LumpedPort",
                "params": {
                    "Resistance_Ohm": 50.0,
                    "Voltage_V": 1.0,
                    "Direction_X": 0.0,
                    "Direction_Y": 0.0,
                    "Direction_Z": -1.0,
                },
            }, "Feed", "Lumped Port")
            tree._edit_boundaries(["Xmin", "Xmax"])
            tree._edit_open_region_domain()
            tree._edit_local_mesh_refinement(0)
            tree._object_boundary_dialog_data({
                "name": "Radiator_BC",
                "type": "PEC",
                "params": {},
            }, "Radiator", "Assign Boundary Condition")

            model = SimpleNamespace(
                is_model=True,
                material="Copper",
                actor=SimpleNamespace(
                    GetBounds=lambda: (-20.0, 20.0, -2.0, 2.0, 0.0, 42.0)
                ),
            )
            wizard_host = QWidget()
            wizard_host._viewport = SimpleNamespace(
                scene=SimpleNamespace(objects=[model])
            )
            MainWindow._open_region_pml_wizard(wizard_host)
            wizard_host.close()
        finally:
            QDialog.exec = original_exec
            if original_exec_ is not None:
                QDialog.exec_ = original_exec_
            tree.close()
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


def capture_sketch_context_toolbar(app, output_dir: Path) -> Path:
    from PySide6.QtCore import QRect
    from em3d_modeler.ui.main_window import MainWindow

    window = MainWindow()
    window.setWindowTitle("Sketch toolbar screenshot")
    window.resize(1440, 760)
    window._start_embedded_sketch((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    engine = window._viewport._sketch_engine
    engine.add_rectangle((-30.0, -18.0), (30.0, 18.0))
    engine.add_circle((0.0, 0.0), 7.0)
    window._viewport._refresh_sketch_overlay()
    window.show()
    app.processEvents()

    toolbar = window._sketch_context_toolbar
    groups = [
        toolbar.widgetForAction(action)
        for action in toolbar.actions()
        if toolbar.widgetForAction(action) is not None
        and toolbar.widgetForAction(action).property("toolbarGroupTitle")
    ]
    if not groups:
        window.close()
        raise RuntimeError("The sketch context toolbar has no visible tool groups.")
    left = min(group.geometry().left() for group in groups)
    right = max(group.geometry().right() for group in groups)
    crop_left = max(0, left - 6)
    crop = QRect(crop_left, 0, right - crop_left + 7, toolbar.height())
    pixmap = toolbar.grab().copy(crop)
    path = output_dir / "toolbar-sketch-context.png"
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        window.close()
        raise RuntimeError(f"Qt could not save screenshot: {path}")
    window.close()
    return path


def capture_simulation_workspace(app, output_dir: Path) -> Path:
    from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget
    from em3d_modeler.ui.main_window import MainWindow

    host = QMainWindow()
    host.setWindowTitle("EM 3D Modeler - Simulation")
    host.resize(1440, 900)
    host._workspace_tabs = QTabWidget(host)
    host.setCentralWidget(host._workspace_tabs)
    host._workspace_tabs.addTab(QWidget(), "Model")
    host._sim_dlg = None
    host._sim_log_verbosity = "Info"
    host._sim_cached_script_bundle = {}
    host._generate_simulation_assets = lambda **_kwargs: "master script"
    host._write_cached_simulation_scripts = lambda: output_dir / "Dipole_Antenna_master.py"
    host._append_sim_log = lambda message: host._sim_log_view.appendPlainText(message)
    host._on_sim_option_changed = lambda *_args: None
    host._on_check_simulation = lambda *_args: None
    host._on_sim_generate = lambda *_args, **_kwargs: None
    host._on_sim_save_script = lambda *_args: None
    host._on_sim_log_level_changed = lambda *_args: None
    host._on_sim_run = lambda *_args: None
    host._on_sim_stop = lambda *_args: None
    host._rebuild_window_menu = lambda: None

    script_bundle = {
        "master": "# Sequential simulation workflow\n# Runs each enabled worker in order.",
        "scripts": [
            {
                "name": "Frequency_Sweep",
                "job_name": "Frequency_Sweep",
                "index": 1,
                "content": "# Frequency sweep worker\nrun_sweep()",
            },
            {
                "name": "Length_Sweep (antenna_length=40)",
                "job_name": "Length_Sweep",
                "index": 2,
                "content": "# Parametric worker: antenna_length=40\nrun_sweep()",
            },
            {
                "name": "Length_Sweep (antenna_length=50)",
                "job_name": "Length_Sweep",
                "index": 3,
                "content": "# Parametric worker: antenna_length=50\nrun_sweep()",
            },
        ],
    }
    host._sim_cached_script_bundle = script_bundle
    MainWindow._open_simulation_window(host)
    MainWindow._update_simulation_script_tabs(host, script_bundle)
    host._sim_script_tabs.setCurrentIndex(2)
    host._sim_log_view.setPlainText(
        "[info] Project saved before simulation run.\n"
        "[master] Running job 'Length_Sweep (antenna_length=40)'\n"
        "EM3D_SPARAM_PROGRESS: first parameter, chunk 5/101"
    )
    path = output_dir / "window-simulation.png"
    capture_widget(host, path, app)
    host.close()
    return path


def capture_parametric_plot(app, output_dir: Path) -> list[Path]:
    import numpy as np
    from em3d_modeler.ui.chart_view import PlotView, _AxisRangeDialog

    frequencies = np.linspace(8.0, 10.0, 101).tolist()
    first_values = [
        complex(
            0.12 + 0.66 * np.exp(-((frequency - 8.85) / 0.22) ** 2),
            0.035 * np.sin(frequency * 3.1),
        )
        for frequency in frequencies
    ]
    partial_frequencies = frequencies[:5]
    partial_values = [
        complex(
            0.15 + 0.52 * np.exp(-((frequency - 9.35) / 0.3) ** 2),
            0.03 * np.cos(frequency * 2.7),
        )
        for frequency in partial_frequencies
    ]
    plot = PlotView("plot_sp")
    plot.resize(1280, 760)
    plot.set_plot_data(
        frequencies,
        [
            {
                "label": "S11",
                "values": first_values,
                "file_name": "antenna_length=40 mm",
                "file_id": "length-40",
            },
            {
                "label": "S11",
                "values": partial_values,
                "x_values": partial_frequencies,
                "file_name": "antenna_length=50 mm (5/101 points)",
                "file_id": "length-50",
            },
        ],
        title="Parametric S11 - live sequential update",
        xlabel="Frequency (GHz)",
        ylabel="S-parameter",
    )
    plot_path = output_dir / "window-parametric-plot.png"
    capture_widget(plot, plot_path, app)
    plot.close()

    axes_dialog = _AxisRangeDialog((8.0, 10.0), (0.0, 1.0), False, False)
    axes_dialog.resize(440, 320)
    axes_path = output_dir / "dialog-plot-axes.png"
    capture_widget(axes_dialog, axes_path, app)
    axes_dialog.close()
    return [plot_path, axes_path]


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
        "--sketch-toolbar-only", action="store_true",
        help="capture the embedded sketch context toolbar only",
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

    if args.sketch_toolbar_only:
        try:
            path = capture_sketch_context_toolbar(app, output_dir)
            print(path)
            return 0
        except Exception as exc:
            print(f"Sketch-toolbar capture failed: {exc}", file=sys.stderr)
            return 1

    saved = []
    capture_groups = [
        lambda: capture_dialogs(app, output_dir),
        lambda: capture_project_tree_sections(app, output_dir),
        lambda: capture_project_tree_dialogs(app, output_dir),
        lambda: capture_properties_and_parameters(app, output_dir),
        lambda: [capture_simulation_workspace(app, output_dir)],
        lambda: capture_parametric_plot(app, output_dir),
        lambda: [capture_object_materials_tree(app, output_dir, args.project_file)],
    ]
    if not args.offscreen:
        capture_groups.append(lambda: [capture_sketch_context_toolbar(app, output_dir)])
    for capture in capture_groups:
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

    toolbar_path = capture_toolbar_crop(output_dir)
    if toolbar_path is not None:
        saved.append(toolbar_path)

    if not saved:
        print("No screenshots were captured.", file=sys.stderr)
        return 1
    missing = [path for path in saved if not path.is_file() or path.stat().st_size == 0]
    if missing:
        print(
            "Screenshot capture incomplete: " + ", ".join(str(path) for path in missing),
            file=sys.stderr,
        )
        return 1
    print(f"Saved {len(saved)} screenshots to {args.output_dir.resolve()}")
    for path in saved:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())