"""Main window: wires all panels together according to the UI layout.

Layout
──────
  ┌─────────────────┬───────────────────────────┬──────────────────┐
  │  ProjectTree    │                           │                  │
  │  (EMERGE cfg)   │       3D Viewport         │ Object/Materials │
  ├─────────────────┤                           │                  │
  │  BlockParams    │                           │                  │
  │  (selected obj) ├───────────────────────────┤                  │
  │                 │     Info / Error bar      │                  │
  └─────────────────┴───────────────────────────┴──────────────────┘
"""
from __future__ import annotations
import os
from pathlib import Path

# Resolve Icons folder relative to this file (4 levels up from ui/)
_ICONS_DIR = Path(__file__).parent.parent.parent.parent / "Icons"


def _icon(name: str) -> "QIcon":
    """Load SVG/PNG icon by Part_Name from the Icons folder."""
    for ext in ("svg", "png"):
        p = _ICONS_DIR / f"{name}.{ext}"
        if p.exists():
            from PyQt5.QtGui import QIcon
            return QIcon(str(p))
    from PyQt5.QtGui import QIcon
    return QIcon()

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QToolBar, QAction,
    QFileDialog, QInputDialog, QMessageBox, QComboBox,
    QLabel, QDoubleSpinBox, QHBoxLayout, QSizePolicy,
)
from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtGui  import QIcon, QKeySequence

from .viewport_widget      import Viewport3DWidget
from .project_tree_widget  import ProjectTreeWidget
from .block_params_widget  import BlockParamsWidget
from .materials_widget     import MaterialsWidget
from .info_bar_widget      import InfoBarWidget

from ..emerge.project_file    import ProjectFile
from ..emerge.script_exporter import export_emerge_script


# ──────────────────────────────────────────────────────────────────────────────
_PLANES = ["XY", "XZ", "YZ"]
_UNITS  = ["mm", "cm", "m", "mil", "inch"]


class MainWindow(QMainWindow):
    """EM 3D Modeler – main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("EM 3D Modeler – EMERGE Design Environment")
        self.resize(1400, 860)

        self._project_name  = "Untitled"
        self._project_path  = None
        self._units         = "mm"
        self._draw_material = "PEC"

        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._connect_signals()
        self._new_project()

    # ─────────────────────────────────────────────────── UI construction
    def _build_ui(self) -> None:
        # ── panels ────────────────────────────────────────────────────────────
        self._project_tree  = ProjectTreeWidget()
        self._block_params  = BlockParamsWidget()
        self._viewport      = Viewport3DWidget()
        self._materials     = MaterialsWidget()
        self._info_bar      = InfoBarWidget()

        # ── left column: project tree (top) + block params (bottom) ───────────
        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.addWidget(self._project_tree)
        left_splitter.addWidget(self._block_params)
        left_splitter.setSizes([350, 300])
        left_splitter.setMinimumWidth(210)

        # ── centre column: viewport (top) + info bar (bottom) ─────────────────
        centre_widget = QWidget()
        centre_layout = __import__("PyQt5.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout(centre_widget)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)
        centre_layout.addWidget(self._viewport, stretch=1)
        centre_layout.addWidget(self._info_bar)

        # ── right column ──────────────────────────────────────────────────────
        self._materials.setMinimumWidth(190)

        # ── main horizontal splitter ──────────────────────────────────────────
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(centre_widget)
        main_splitter.addWidget(self._materials)
        main_splitter.setSizes([230, 900, 210])

        self.setCentralWidget(main_splitter)

    # ─────────────────────────────────────────────────── menus
    def _build_menus(self) -> None:
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("&File")
        self._act_new    = file_menu.addAction("&New Project",    self._new_project,  QKeySequence.New)
        self._act_open   = file_menu.addAction("&Open Project…",  self._open_project, QKeySequence.Open)
        self._act_save   = file_menu.addAction("&Save Project",   self._save_project, QKeySequence.Save)
        self._act_saveas = file_menu.addAction("Save Project &As…", self._save_project_as)
        file_menu.addSeparator()
        act_export = file_menu.addAction("&Export EMERGE Script…", self._export_emerge)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence.Quit)

        # Edit
        edit_menu = mb.addMenu("&Edit")
        self._act_del = edit_menu.addAction("&Delete Selected", self._delete_selected, QKeySequence.Delete)
        edit_menu.addAction("&Cancel Drawing",    self._viewport.cancel_draw, Qt.Key_Escape)

        # View
        view_menu = mb.addMenu("&View")
        view_menu.addAction("Reset Camera",       self._viewport.reset_camera, Qt.Key_Home)
        view_menu.addAction("Top (XY)",           lambda: self._set_view("top"))
        view_menu.addAction("Front (XZ)",         lambda: self._set_view("front"))
        view_menu.addAction("Right (YZ)",         lambda: self._set_view("right"))
        view_menu.addAction("Isometric",          lambda: self._set_view("iso"))

    # ─────────────────────────────────────────────────── toolbar
    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(__import__("PyQt5.QtCore", fromlist=["QSize"]).QSize(22, 22))

        # ── Primitive shapes ────────────────────────────────────────
        _DRAW_ICONS = [
            ("Box",      "box",      "Part_Box"),
            ("Cylinder", "cylinder", "Part_Cylinder"),
            ("Cone",     "cone",     "Part_Cone"),
            ("Sphere",   "sphere",   "Part_Sphere"),
        ]
        for label, mode, icon_name in _DRAW_ICONS:
            act = QAction(_icon(icon_name), label, self)
            act.setToolTip(f"Draw {label}  [click base on viewport to start]")
            act.triggered.connect(lambda checked, m=mode: self._start_draw(m))
            tb.addAction(act)

        tb.addSeparator()

        # ── Boolean operations ────────────────────────────────────
        act_cut = QAction(_icon("Part_Cut"), "Cut", self)
        act_cut.setToolTip("Boolean Cut: subtract Tool shape from Base shape")
        act_cut.triggered.connect(self._bool_cut)
        tb.addAction(act_cut)

        act_fuse = QAction(_icon("Part_Fuse"), "Fuse", self)
        act_fuse.setToolTip("Boolean Fuse (Union): merge two selected shapes")
        act_fuse.triggered.connect(self._bool_fuse)
        tb.addAction(act_fuse)

        act_common = QAction(_icon("Part_Common"), "Common", self)
        act_common.setToolTip("Boolean Common (Intersection): keep overlapping volume")
        act_common.triggered.connect(self._bool_common)
        tb.addAction(act_common)

        tb.addSeparator()

        # ── Drawing plane ───────────────────────────────────────────
        tb.addWidget(QLabel(" Plane: "))
        self._plane_combo = QComboBox()
        self._plane_combo.addItems(_PLANES)
        self._plane_combo.setToolTip("Drawing plane")
        tb.addWidget(self._plane_combo)

        tb.addSeparator()

        # ── Material ─────────────────────────────────────────────────
        tb.addWidget(QLabel(" Material: "))
        self._mat_combo = QComboBox()
        from ..scene.em_objects import MATERIAL_COLORS
        self._mat_combo.addItems(list(MATERIAL_COLORS.keys()) + ["Custom"])
        self._mat_combo.setToolTip("Material for new objects")
        self._mat_combo.currentTextChanged.connect(
            lambda t: setattr(self, "_draw_material", t)
        )
        tb.addWidget(self._mat_combo)

        tb.addSeparator()

        # ── Grid spacing ───────────────────────────────────────────
        tb.addWidget(QLabel(" Grid: "))
        self._grid_spacing_spin = QDoubleSpinBox()
        self._grid_spacing_spin.setRange(0.1, 1000)
        self._grid_spacing_spin.setValue(10.0)
        self._grid_spacing_spin.setSuffix(" mm")
        self._grid_spacing_spin.setToolTip("Grid snap spacing")
        self._grid_spacing_spin.valueChanged.connect(self._on_grid_changed)
        tb.addWidget(self._grid_spacing_spin)

        tb.addWidget(QLabel(" Units: "))
        self._units_combo = QComboBox()
        self._units_combo.addItems(_UNITS)
        self._units_combo.currentTextChanged.connect(self._on_units_changed)
        tb.addWidget(self._units_combo)

    # ─────────────────────────────────────────────────── signal wiring
    def _connect_signals(self) -> None:
        # Viewport → block params + materials
        self._viewport.object_selected.connect(self._on_object_selected)
        self._viewport.scene_changed.connect(self._refresh_materials)
        self._viewport.status_message.connect(self._info_bar.set_info)

        # Block params → viewport render
        self._block_params.params_changed.connect(self._on_params_changed)

        # Materials tree → selection
        self._materials.object_selected.connect(self._on_material_tree_select)

        # EMERGE settings changed
        self._project_tree.settings_changed.connect(self._on_settings_changed)

    # ─────────────────────────────────────────────────── actions
    def _start_draw(self, mode: str) -> None:
        plane    = self._plane_combo.currentText()
        material = self._draw_material
        self._viewport.start_draw(mode, plane, material)

    def _delete_selected(self) -> None:
        obj = self._viewport.scene.selected
        if obj:
            self._viewport.scene.remove_object(obj)
            self._viewport.scene.select(None)
            self._block_params.set_object(None)
            self._refresh_materials()
            self._viewport._render()
            self._info_bar.set_info(f"Deleted: {obj.name}")

    # ─────────────────────────────────────────────────── boolean operations
    def _bool_cut(self) -> None:
        self._info_bar.set_info(
            "Boolean Cut: select Base object then Tool object in the viewport."
        )
        QMessageBox.information(
            self, "Boolean Cut",
            "Boolean operations will be available in a future release.\n\n"
            "Workflow: select the Base shape, then Shift-click the Tool shape,\n"
            "then click Cut."
        )

    def _bool_fuse(self) -> None:
        self._info_bar.set_info(
            "Boolean Fuse: select two objects in the viewport to merge."
        )
        QMessageBox.information(
            self, "Boolean Fuse",
            "Boolean operations will be available in a future release.\n\n"
            "Workflow: select two shapes, then click Fuse."
        )

    def _bool_common(self) -> None:
        self._info_bar.set_info(
            "Boolean Common: select two objects to compute their intersection."
        )
        QMessageBox.information(
            self, "Boolean Common",
            "Boolean operations will be available in a future release.\n\n"
            "Workflow: select two shapes, then click Common."
        )

    # ─────────────────────────────────────────────────── object selection
    def _on_object_selected(self, obj) -> None:
        self._block_params.set_object(obj)
        self._materials.highlight(obj)
        if obj:
            self._info_bar.set_info(f"Selected: {obj.name}  [{type(obj).__name__}]")

    def _on_params_changed(self, obj, _params) -> None:
        self._viewport._render()
        self._refresh_materials()

    def _on_material_tree_select(self, obj) -> None:
        self._viewport.scene.select(obj)
        self._block_params.set_object(obj)
        self._viewport._render()

    # ─────────────────────────────────────────────────── grid / units
    def _on_grid_changed(self, value: float) -> None:
        plane = self._plane_combo.currentText()
        size  = value * 20   # grid extent = 20 × spacing
        self._viewport.set_grid(size, value, plane, self._units)

    def _on_units_changed(self, units: str) -> None:
        self._units = units
        self._grid_spacing_spin.setSuffix(f" {units}")
        self._on_grid_changed(self._grid_spacing_spin.value())

    # ─────────────────────────────────────────────────── scene refresh
    def _refresh_materials(self) -> None:
        by_mat = self._viewport.scene.by_material()
        self._materials.refresh(by_mat)

    def _on_settings_changed(self) -> None:
        self._info_bar.set_info("EMERGE settings updated.")

    # ─────────────────────────────────────────────────── camera views
    def _set_view(self, view: str) -> None:
        cam = self._viewport._renderer.GetActiveCamera()
        if view == "top":
            cam.SetPosition(0, 0, 300)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 1, 0)
        elif view == "front":
            cam.SetPosition(0, -300, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "right":
            cam.SetPosition(300, 0, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        else:  # iso
            cam.SetPosition(150, -200, 150)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        self._viewport._renderer.ResetCamera()
        self._viewport._render()

    # ─────────────────────────────────────────────────── project file
    def _new_project(self) -> None:
        self._project_name = "Untitled"
        self._project_path = None
        self.setWindowTitle(f"EM 3D Modeler – {self._project_name}")
        self._project_tree.set_project_name(self._project_name)
        self._viewport.scene.clear()
        self._block_params.set_object(None)
        self._refresh_materials()
        self._viewport._render()
        self._info_bar.set_info("New project created.")

    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "EM3D Project (*.em3d);;All Files (*)"
        )
        if not path:
            return
        try:
            data = ProjectFile.load(path)
            self._project_name = data.get("project_name", "Untitled")
            self._project_path = path
            self.setWindowTitle(f"EM 3D Modeler – {self._project_name}")
            self._project_tree.set_project_name(self._project_name)
            self._project_tree.load_settings(data.get("emerge_settings", {}))
            self._viewport.scene.from_json(data.get("objects", []))
            grid = data.get("grid", {})
            self._viewport.set_grid(
                grid.get("size",    200),
                grid.get("spacing", 10),
                grid.get("plane",  "XY"),
                data.get("units", "mm"),
            )
            self._refresh_materials()
            self._viewport._render()
            self._info_bar.set_info(f"Project loaded: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    def _save_project(self) -> None:
        if self._project_path is None:
            self._save_project_as()
        else:
            self._do_save(self._project_path)

    def _save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", f"{self._project_name}.em3d",
            "EM3D Project (*.em3d);;All Files (*)"
        )
        if path:
            self._project_path = path
            self._do_save(path)

    def _do_save(self, path: str) -> None:
        try:
            ProjectFile.save(
                path,
                project_name  = self._project_name,
                settings      = self._project_tree.get_settings(),
                objects_json  = self._viewport.scene.to_json(),
                units         = self._units,
                grid_size     = self._viewport.scene._grid_size,
                grid_spacing  = self._viewport.scene._grid_spacing,
                grid_plane    = self._viewport.scene._grid_plane,
            )
            self.setWindowTitle(f"EM 3D Modeler – {self._project_name}")
            self._info_bar.set_info(f"Saved: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def _export_emerge(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export EMERGE Script",
            f"{self._project_name}.em",
            "EMERGE Script (*.em);;Text (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            script = export_emerge_script(
                project_name = self._project_name,
                settings     = self._project_tree.get_settings(),
                objects      = self._viewport.scene.objects,
                units        = self._units,
            )
            Path(path).write_text(script, encoding="utf-8")
            self._info_bar.set_info(f"EMERGE script exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    # ─────────────────────────────────────────────────── close
    def closeEvent(self, event) -> None:
        reply = QMessageBox.question(
            self, "Quit", "Save project before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )
        if reply == QMessageBox.Save:
            self._save_project()
            event.accept()
        elif reply == QMessageBox.Discard:
            event.accept()
        else:
            event.ignore()
