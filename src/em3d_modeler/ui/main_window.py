"""Main window: wires all panels together according to the UI layout.

Layout
������������������
  ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
  ���  ProjectTree    ���                           ���                  ���
  ���  (EMERGE cfg)   ���       3D Viewport         ��� Object/Materials ���
  ���������������������������������������������������������                           ���                  ���
  ���  BlockParams    ���                           ���                  ���
  ���  (selected obj) ���������������������������������������������������������������������������������������                  ���
  ���                 ���     Info / Error bar      ���                  ���
  ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import os
import traceback
import sys
import vtk

# Resolve resources from source, PyInstaller's internal directory, or the EXE folder.
_APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent.parent.parent))
_RESOURCE_ROOTS = [
    _APP_ROOT,
    Path(sys.executable).resolve().parent,
    Path(__file__).parent.parent.parent.parent,
]
_ICONS_DIR = next(
    (root / "Icons" for root in _RESOURCE_ROOTS if (root / "Icons").is_dir()),
    _APP_ROOT / "Icons",
)


def _icon(name: str) -> "QIcon":
    """Load SVG/PNG icon by Part_Name from the Icons folder."""
    for ext in ("svg", "png"):
        p = _ICONS_DIR / f"{name}.{ext}"
        if p.exists():
            from PySide6.QtGui import QIcon
            return QIcon(str(p))
    from PySide6.QtGui import QIcon
    return QIcon()

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter,
    QFileDialog, QMessageBox, QComboBox,
    QLabel, QDoubleSpinBox, QDialog, QInputDialog,
    QVBoxLayout, QTextBrowser, QPlainTextEdit,
    QPushButton, QHBoxLayout, QGroupBox, QCheckBox,
    QProgressDialog, QApplication, QTabWidget,
)
from PySide6.QtCore import Qt, QProcess, QLocale, QSettings, QSize, QUrl
from PySide6.QtGui  import QIcon, QKeySequence, QAction, QDesktopServices

# Undo/Redo CommandStack


from .viewport_widget        import Viewport3DWidget
from .project_tree_widget    import ProjectTreeWidget
from .body_properties_widget import BodyPropertiesWidget
from .materials_widget       import MaterialsWidget
from .info_bar_widget        import InfoBarWidget
from .reference_plane_dialog import ReferencePlaneDialog
from .sketch_widget          import SketchDialog
from .material_assign_dialog import MaterialAssignDialog
from .material_library_dialog import MaterialLibraryDialog
from .settings_dialog        import SettingsDialog
from .project_tree_widget    import set_numeric_locale

from ..emerge.project_file    import ProjectFile
from ..emerge.script_exporter import export_emerge_script
from ..emerge.python_script_exporter import export_emerge_python_script
from ..emerge.step_bundle_exporter import export_objects_to_step_bundle
from ..emerge.step_importer   import import_step
from ..emerge.material_store  import MaterialStore
from ..scene.em_objects       import set_selection_color
from .. import __version__, __release_date__


# ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
_UNITS  = ["mm", "um", "cm", "m", "mil", "inch"]
_DOCS_ROOT = _APP_ROOT / "docs"
_DOCS_HELP = _DOCS_ROOT / "HELP.md"
_DOCS_HTML = _DOCS_ROOT / "HELP.html"
_DOCS_README = _APP_ROOT / "README.md"
_LOG_LEVEL_ORDER = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# Unit conversion: mm per unit (reference base)
_MM_PER_UNIT = {
    "mm": 1.0,
    "um": 0.001,
    "cm": 10.0,
    "m": 1000.0,
    "mil": 0.0254,
    "inch": 25.4,
}


class MainWindow(QMainWindow):
    """EM 3D Modeler - main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("EM 3D Modeler - EMERGE Design Environment")
        self.resize(1400, 860)

        self._project_name  = "Untitled"
        self._project_path  = None
        self._units         = "mm"
        self._workspace_size = 200.0
        self._grid_spacing = 10.0
        self._plane_triad_size = 25.0
        self._decimal_separator = "."
        self._selection_color = (0.62, 0.34, 0.85)
        self._draw_material = "PEC"
        self._material_store = MaterialStore()
        self._sim_steps_dirty = True
        self._sim_script_dirty = True
        self._sim_step_bundle_ready = False
        self._sim_step_bundle_cache: dict = {"entries": [], "skipped": []}
        self._sim_cached_script = ""
        self._sim_cached_script_bundle: dict = {"master": "", "scripts": []}
        self._sim_log_verbosity = "INFO"
        self._ui_locale = QLocale.c()

        # Simulation & Mesh settings (app-wide defaults)
        self._sim_solver = "PARDISO"
        self._sim_parallel_enabled = True
        self._sim_pardiso_threads = 8
        self._sim_acc_threads = 10
        self._mesh_resolution = 0.3  # 1/3 wavelength, ~3.3 lines/wavelength
        self._sim_plot_sparams_after_sim = True
        self._sim_export_sparams_after_sim = True

        self._load_app_settings()
        set_selection_color(self._selection_color)
        set_numeric_locale(self._ui_locale)




        self._build_ui()
        self._build_toolbar()
        self._build_menus()
        self._connect_signals()
        self._sync_material_choices()

        self._new_project()


    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� UI construction
    def _build_ui(self) -> None:
        # ������ panels ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
        self._project_tree   = ProjectTreeWidget()
        self._body_props     = BodyPropertiesWidget()
        self._viewport       = Viewport3DWidget()
        self._viewport.set_plane_triad_size(self._plane_triad_size)
        self._materials      = MaterialsWidget(self)
        self._info_bar       = InfoBarWidget()

        # ������ left column: project tree (top) + body props (bottom) ���������������������������������
        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.addWidget(self._project_tree)
        left_splitter.addWidget(self._body_props)
        left_splitter.setSizes([350, 300])
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)
        left_splitter.setCollapsible(0, False)
        left_splitter.setCollapsible(1, False)
        left_splitter.setMinimumWidth(210)

        # ������ centre column: viewport (top) + info bar (bottom) ���������������������������������������������������
        centre_widget = QWidget()
        centre_layout = QVBoxLayout(centre_widget)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)
        centre_layout.addWidget(self._viewport, stretch=1)
        centre_layout.addWidget(self._info_bar)

        # ������ right column ������������������������������������������������������������������������������������������������������������������������������������������������������������������
        self._materials.setMinimumWidth(190)

        # ������ main horizontal splitter ������������������������������������������������������������������������������������������������������������������������������
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(centre_widget)
        main_splitter.addWidget(self._materials)
        main_splitter.setSizes([230, 900, 210])

        self.setCentralWidget(main_splitter)


    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� menus
    def _build_menus(self) -> None:
        mb = self.menuBar()

        # File
        file_menu = mb.addMenu("&File")
        self._act_new    = file_menu.addAction("&New Project",    self._new_project,  QKeySequence.New)
        self._act_open   = file_menu.addAction("&Open Project...",  self._open_project, QKeySequence.Open)
        self._act_save   = file_menu.addAction("&Save Project",   self._save_project, QKeySequence.Save)
        self._act_saveas = file_menu.addAction("Save Project &As...", self._save_project_as)
        file_menu.addSeparator()
        file_menu.addAction("&Import STEP...",            self._import_step)
        file_menu.addSeparator()
        act_export = file_menu.addAction("&Export EMERGE Script...", self._export_emerge)
        file_menu.addSeparator()
        file_menu.addAction("Set &Global Material DB...", self._set_global_material_db)
        file_menu.addAction("&Reload Global Material DB", self._reload_global_material_db)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence.Quit)

        # Edit
        edit_menu = mb.addMenu("&Edit")
        self._act_del = edit_menu.addAction("&Delete Selected", self._delete_selected, QKeySequence.Delete)
        edit_menu.addAction("&Cancel Drawing",    self._viewport.cancel_draw, Qt.Key_Escape)


        # View
        view_menu = mb.addMenu("&View")
        view_menu.addAction("Reset Camera",       self._viewport.reset_camera, Qt.Key_Home)
        act_view_top = view_menu.addAction("Top (XY)", lambda: self._set_view("top"))
        act_view_top.setShortcut(QKeySequence("Ctrl+1"))
        act_view_bottom = view_menu.addAction("Bottom (-XY)", lambda: self._set_view("bottom"))
        act_view_bottom.setShortcut(QKeySequence("Ctrl+Shift+1"))
        act_view_front = view_menu.addAction("Front (XZ)", lambda: self._set_view("front"))
        act_view_front.setShortcut(QKeySequence("Ctrl+2"))
        act_view_back = view_menu.addAction("Back (-XZ)", lambda: self._set_view("back"))
        act_view_back.setShortcut(QKeySequence("Ctrl+Shift+2"))
        act_view_right = view_menu.addAction("Right (YZ)", lambda: self._set_view("right"))
        act_view_right.setShortcut(QKeySequence("Ctrl+3"))
        act_view_left = view_menu.addAction("Left (-YZ)", lambda: self._set_view("left"))
        act_view_left.setShortcut(QKeySequence("Ctrl+Shift+3"))
        act_view_iso = view_menu.addAction("Isometric", lambda: self._set_view("iso"))
        act_view_iso.setShortcut(QKeySequence("Ctrl+4"))
        view_menu.addSeparator()
        view_menu.addAction("Set Reference Plane...", self._open_reference_plane_dialog)
        view_menu.addAction("Reset Reference Plane", self._viewport.reset_reference_plane)

        # Grid visibility toggle
        self._act_grid_toggle = view_menu.addAction("Hide Grid", self._toggle_grid)
        self._act_grid_toggle.setCheckable(True)
        self._act_grid_toggle.setChecked(True)

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction("&Settings", self._open_settings_dialog)
        tools_menu.addAction("Material &Library...", self._open_material_library_dialog)

        # Help
        help_menu = mb.addMenu("&Help")
        help_menu.addAction("&Help", self._open_help)
        help_menu.addSeparator()
        help_menu.addAction("&About", self._show_about)

    def _toggle_grid(self):
        show = self._act_grid_toggle.isChecked()
        self._apply_grid_visibility(show)

    def _open_help(self) -> None:
        if _DOCS_HTML.exists():
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(_DOCS_HTML))):
                return

        for p in (_DOCS_HELP, _DOCS_README):
            if not p.exists():
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except Exception as exc:
                QMessageBox.warning(self, "Help", f"Unable to open help file:\n{p}\n\n{exc}")
                return

            dlg = QDialog(self)
            dlg.setWindowTitle(f"Help - {p.name}")
            dlg.resize(920, 700)
            layout = QVBoxLayout(dlg)
            browser = QTextBrowser(dlg)
            browser.setOpenExternalLinks(True)
            if hasattr(browser, "setMarkdown"):
                browser.setMarkdown(content)
            else:
                browser.setPlainText(content)
            layout.addWidget(browser)
            dlg.exec_()
            return

        QMessageBox.warning(
            self,
            "Help",
            f"No help document found.\nExpected:\n{_DOCS_HELP}\n(or fallback {_DOCS_README})",
        )

    def _show_about(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        QMessageBox.about(
            self,
            "About EM 3D Modeler",
            "EM 3D Modeler\n"
            "Author: Gabriele Vittori\n"
            f"Version: {__version__}\n"
            f"Release Date: {__release_date__}\n"
            f"Date: {today}",
        )

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� toolbar
    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))

        # ������ Primitive shapes ������������������������������������������������������������������������������������������������������������������������
        _DRAW_ICONS = [
            ("Box",       "box",       "Part_Box"),
            ("Plate",     "plate",     "Part_Box"),
            ("Cylinder",  "cylinder",  "Part_Cylinder"),
            ("Cone",      "cone",      "Part_Cone"),
            ("Sphere",    "sphere",    "Part_Sphere"),
            ("Pyramid",   "pyramid",   "Std_Tool1"),
            ("Wedge",     "wedge",     "Std_Tool2"),
            ("Torus",     "torus",     "Std_Tool3"),
            ("Ellipsoid", "ellipsoid", "Part_Sphere"),
        ]
        for label, mode, icon_name in _DRAW_ICONS:
            act = QAction(_icon(icon_name), label, self)
            act.setToolTip(f"Draw {label}  [click base on viewport to start]")
            act.triggered.connect(lambda checked, m=mode: self._start_draw(m))
            tb.addAction(act)

        # Sketch tool
        act_sketch = QAction(_icon("Part_Sketch"), "Sketch", self)
        act_sketch.setToolTip("Open parametric sketch canvas (extrude or revolve)")
        act_sketch.triggered.connect(self._open_sketch)
        tb.addAction(act_sketch)

        act_planar = QAction(_icon("Std_Plane"), "Planar", self)
        act_planar.setToolTip("Define planar structure: pick start/end (vertex/edge/face snap) on active plane")
        act_planar.triggered.connect(lambda: self._start_draw("planar"))
        tb.addAction(act_planar)



        tb.addSeparator()

        # ������ Boolean operations ������������������������������������������������������������������������������������������������������������
        act_cut = QAction(_icon("Part_Cut"), "Cut", self)
        act_cut.setToolTip("Boolean Cut: subtract Tool shape from Base shape")
        act_cut.triggered.connect(self._bool_cut)
        tb.addAction(act_cut)

        act_fuse = QAction(_icon("Part_Fuse"), "Fuse", self)
        act_fuse.setToolTip("Boolean Fuse (Union): merge two selected shapes")
        act_fuse.triggered.connect(self._bool_fuse)
        tb.addAction(act_fuse)

        # Dynamic Fuse selection counter
        self._fuse_label = QLabel(" Fuse (0) ")
        self._fuse_label.setStyleSheet("font-size: 10px; color: #666;")
        tb.addWidget(self._fuse_label)

        act_common = QAction(_icon("Part_Common"), "Common", self)
        act_common.setToolTip("Boolean Common (Intersection): keep overlapping volume")
        act_common.triggered.connect(self._bool_common)
        tb.addAction(act_common)

        act_scale = QAction(_icon("image-scaling"), "Scale", self)
        act_scale.setToolTip("Scale selected object(s) by a user-defined factor")
        act_scale.triggered.connect(self._scale_selected_objects)
        tb.addAction(act_scale)

        act_move_plane = QAction(_icon("Std_TransformManip"), "Move On Plane", self)
        act_move_plane.setToolTip("Move selected object(s) on active plane toward plane origin")
        act_move_plane.triggered.connect(self._move_selection_to_plane_origin)
        tb.addAction(act_move_plane)

        act_dissolve_boolean = QAction(QIcon(), "Dissolve Boolean", self)
        act_dissolve_boolean.setToolTip("Restore source objects from selected boolean result(s)")
        act_dissolve_boolean.triggered.connect(self._bool_dissolve)
        tb.addAction(act_dissolve_boolean)

        tb.addSeparator()

        # ������ STEP import ������������������������������������������������������������������������������������������������������������������������������������������
        act_step = QAction(_icon("Part_STEP"), "Import STEP", self)
        act_step.setToolTip("Import a STEP file (.step / .stp)")
        act_step.triggered.connect(self._import_step)
        tb.addAction(act_step)

        act_play = QAction(_icon("media-playback-start"), "Play", self)
        act_play.setToolTip("Open simulation panel (EMERGE Python script + verbose output)")
        act_play.triggered.connect(self._open_simulation_window)
        tb.addAction(act_play)

        tb.addSeparator()

        # ������ Selection mode ���������������������������������������������������������������������������������������������������������������������������
        tb.addWidget(QLabel(" Select: "))
        self._sel_mode_combo = QComboBox()
        self._sel_mode_combo.addItems(["All", "Surface", "Edge", "Vertex"])
        self._sel_mode_combo.setToolTip(
            "Selection mode:\n"
            "  All    - pick whole bodies\n"
            "  Surface- pick a single face/surface\n"
            "  Edge   - pick a single edge\n"
            "  Vertex - pick a single vertex"
        )
        self._sel_mode_combo.currentTextChanged.connect(self._on_selection_mode_changed)
        tb.addWidget(self._sel_mode_combo)

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� signal wiring
    def _connect_signals(self) -> None:
        # Viewport ��� body props + materials
        self._viewport.object_selected.connect(self._on_object_selected)
        self._viewport.selection_changed.connect(self._on_selection_changed)
        self._viewport.scene_changed.connect(self._on_scene_changed)
        self._viewport.status_message.connect(self._info_bar.set_info)
        self._viewport.picked_coords.connect(self._info_bar.set_coords)
        self._viewport.clear_coords_requested.connect(self._info_bar.clear_coords)

        # Body props ��� viewport render
        self._body_props.params_changed.connect(self._on_params_changed)
        self._body_props.bulk_material_changed.connect(self._on_bulk_material)
        self._body_props.bulk_style_changed.connect(self._on_bulk_style)
        self._body_props.model_role_changed.connect(self._on_model_role_changed)
        self._body_props.material_added.connect(self._on_material_added)
        self._body_props.material_picker_requested.connect(self._open_material_picker)

        # Materials tree ��� selection (single + multi)
        self._materials.object_selected.connect(self._on_material_tree_select)
        self._materials.selection_changed.connect(self._on_material_tree_multi_select)
        self._materials.plane_make_active.connect(self._on_plane_make_active)
        self._materials.plane_view_normal.connect(self._on_plane_view_normal)
        self._materials.plane_delete.connect(self._on_plane_delete)
        self._materials.plane_rename.connect(self._on_plane_rename)
        self._materials.plane_add_requested.connect(self._open_reference_plane_dialog)
        self._materials.plane_triad_visibility_changed.connect(self._on_plane_triad_visibility_changed)
        self._materials.grid_visibility_changed.connect(self._on_grid_visibility_changed)
        self._materials.objects_hide.connect(self._on_materials_hide)
        self._materials.objects_show.connect(self._on_materials_show)
        self._materials.objects_model_role_changed.connect(self._on_materials_model_role_changed)
        self._materials.object_rename.connect(self._on_materials_rename)
        self._materials.objects_bulk_rename.connect(self._on_materials_bulk_rename)
        self._materials.assign_port_requested.connect(self._on_assign_port_requested)
        self._materials.assign_boundary_requested.connect(self._on_assign_boundary_requested)
        self._materials.assign_mesh_resolution_requested.connect(self._on_assign_mesh_resolution_requested)
        self._materials.material_priority_changed.connect(self._on_material_priority_changed)

        # EMERGE settings changed
        self._project_tree.settings_changed.connect(self._on_settings_changed)
        self._project_tree.output_plot_requested.connect(self._on_output_plot_requested)

    def _reset_simulation_cache(self) -> None:
        self._sim_steps_dirty = True
        self._sim_script_dirty = True
        self._sim_step_bundle_ready = False
        self._sim_step_bundle_cache = {"entries": [], "skipped": []}
        self._sim_cached_script = ""
        self._sim_cached_script_bundle = {"master": "", "scripts": []}

    def _mark_simulation_dirty(self, *, steps: bool = False, script: bool = True) -> None:
        if steps:
            self._sim_steps_dirty = True
            self._sim_step_bundle_ready = False
        if script:
            self._sim_script_dirty = True

    def _on_scene_changed(self) -> None:
        self._refresh_materials()
        self._sync_port_reference_state()
        self._mark_simulation_dirty(steps=True, script=True)

    def _sync_port_reference_state(self) -> None:
        object_names = [str(o.name) for o in self._viewport.scene.objects if getattr(o, "name", None)]
        self._project_tree.set_scene_object_names(object_names)

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� actions
    def _start_draw(self, mode: str) -> None:
        plane    = self._active_draw_plane_name()
        material = self._draw_material
        self._viewport.start_draw(mode, plane, material)

    def _delete_selected(self) -> None:
        obj = self._viewport.scene.selected
        if obj:
            self._viewport.scene.remove_object(obj)
            self._viewport.scene.select(None)
            self._body_props.set_object(None)
            self._refresh_materials()
            self._sync_port_reference_state()
            self._viewport._render()
            self._info_bar.set_info(f"Deleted: {obj.name}")

    def _scale_mesh_object(self, obj, factor: float) -> bool:
        actor = getattr(obj, "actor", None)
        if actor is None or actor.GetMapper() is None or actor.GetMapper().GetInput() is None:
            return False

        src_poly = vtk.vtkPolyData()
        src_poly.DeepCopy(actor.GetMapper().GetInput())

        tfm = vtk.vtkTransform()
        tfm.Scale(factor, factor, factor)

        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetTransform(tfm)
        tf.SetInputData(src_poly)
        tf.Update()

        out_poly = vtk.vtkPolyData()
        out_poly.DeepCopy(tf.GetOutput())

        actor.GetMapper().SetInputData(out_poly)
        actor.GetMapper().Update()
        if hasattr(obj, "_polydata"):
            obj._polydata = out_poly
        if getattr(obj, "step_source_path", None):
            obj.step_geometry_modified = True
            off = getattr(obj, "step_export_offset", (0.0, 0.0, 0.0))
            try:
                obj.step_export_offset = (
                    float(off[0]) * factor,
                    float(off[1]) * factor,
                    float(off[2]) * factor,
                )
            except Exception:
                obj.step_export_offset = (0.0, 0.0, 0.0)
        return True

    def _scale_by_attributes(self, obj, factor: float) -> bool:
        t = type(obj).__name__

        if t in {"BoxObject", "PlateObject"}:
            for name in ("x1", "y1", "z1", "x2", "y2", "z2"):
                setattr(obj, name, float(getattr(obj, name)) * factor)
            obj._refresh_source()
            obj.refresh_appearance()
            return True

        if t in {"CylinderObject", "ConeObject"}:
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.radius *= factor
            obj.height *= factor
            obj._refresh_source()
            obj._apply_orientation()
            obj.refresh_appearance()
            return True

        if t == "SphereObject":
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.radius *= factor
            obj._refresh_source()
            obj.refresh_appearance()
            return True

        if t == "PyramidObject":
            obj.bx1 *= factor
            obj.by1 *= factor
            obj.bx2 *= factor
            obj.by2 *= factor
            obj.bz *= factor
            obj.apex_z *= factor
            obj._refresh_source()
            obj._apply_position()
            obj.refresh_appearance()
            return True

        if t == "WedgeObject":
            obj.x1 *= factor
            obj.y1 *= factor
            obj.z1 *= factor
            obj.x2 *= factor
            obj.y2 *= factor
            obj.z2 *= factor
            obj.x3 *= factor
            obj.y3 *= factor
            obj.z_height *= factor
            obj._source = obj._make_wedge_polydata()
            obj._actor.GetMapper().SetInputData(obj._source)
            obj.refresh_appearance()
            return True

        if t == "TorusObject":
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.major_radius *= factor
            obj.minor_radius *= factor
            obj._refresh_source()
            obj._actor.SetPosition(obj.cx, obj.cy, obj.cz)
            obj.refresh_appearance()
            return True

        if t == "EllipsoidObject":
            obj.cx *= factor
            obj.cy *= factor
            obj.cz *= factor
            obj.rx *= factor
            obj.ry *= factor
            obj.rz *= factor
            obj._refresh_source()
            obj._apply_scale()
            obj.refresh_appearance()
            return True

        if t == "ExtrudedObject":
            obj._profile_pts = [(float(x) * factor, float(y) * factor) for x, y in obj._profile_pts]
            obj._depth *= factor
            obj._plane_origin = tuple(float(v) * factor for v in obj._plane_origin)
            obj._rebuild()
            return True

        if t == "RevolvedObject":
            obj._profile_pts = [(float(x) * factor, float(y) * factor) for x, y in obj._profile_pts]
            obj._axis_pt1 = tuple(float(v) * factor for v in obj._axis_pt1)
            obj._axis_pt2 = tuple(float(v) * factor for v in obj._axis_pt2)
            obj._plane_origin = tuple(float(v) * factor for v in obj._plane_origin)
            local_pts, user_matrix = obj._profile_local_and_transform()
            obj._revolve.SetInputData(obj._profile_to_polydata(local_pts))
            obj._revolve.SetAngle(obj._angle)
            obj._revolve.Update()
            if obj._actor is not None:
                obj._actor.SetUserMatrix(user_matrix)
            obj.refresh_appearance()
            return True

        if t == "MeshObject":
            return self._scale_mesh_object(obj, factor)

        return False

    def _scale_selected_objects(self) -> None:
        selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(
                self,
                "Scale Objects",
                "Select one or more objects from the Object/Materials tree, then run Scale.",
            )
            return

        default_factor = self._format_locale_number(1.0)
        factor_txt, ok = QInputDialog.getText(
            self,
            "Scale Selected Objects",
            "Uniform scale factor:",
            text=default_factor,
        )
        if not ok:
            self._info_bar.set_info("Scale cancelled.")
            return

        try:
            factor = self._parse_locale_number(factor_txt)
        except ValueError:
            QMessageBox.warning(self, "Scale Objects", "Invalid scale factor for current locale.")
            return

        if factor <= 0.0:
            QMessageBox.warning(self, "Scale Objects", "Scale factor must be greater than zero.")
            return

        scaled = []
        failed = []
        for obj in selection:
            try:
                if self._scale_by_attributes(obj, float(factor)):
                    scaled.append(obj)
                else:
                    failed.append(str(getattr(obj, "name", type(obj).__name__)))
            except Exception as exc:
                failed.append(f"{getattr(obj, 'name', type(obj).__name__)} ({exc})")

        if scaled:
            self._viewport.scene_changed.emit()
            self._refresh_materials()
            self._viewport._render()
            self._viewport.selection_changed.emit(selection)
            self._mark_simulation_dirty(steps=True, script=True)

        msg = f"Scaled {len(scaled)} object(s) by factor {self._format_locale_number(factor)}."
        if failed:
            msg += f" Failed: {', '.join(failed)}"
        self._info_bar.set_info(msg)

    def _translate_mesh_object(self, obj, dx: float, dy: float, dz: float) -> bool:
        actor = getattr(obj, "actor", None)
        if actor is None or actor.GetMapper() is None or actor.GetMapper().GetInput() is None:
            return False
        src_poly = vtk.vtkPolyData()
        src_poly.DeepCopy(actor.GetMapper().GetInput())
        tfm = vtk.vtkTransform()
        tfm.Translate(dx, dy, dz)
        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetTransform(tfm)
        tf.SetInputData(src_poly)
        tf.Update()
        out_poly = vtk.vtkPolyData()
        out_poly.DeepCopy(tf.GetOutput())
        actor.GetMapper().SetInputData(out_poly)
        actor.GetMapper().Update()
        if hasattr(obj, "_polydata"):
            obj._polydata = out_poly
        if getattr(obj, "step_source_path", None):
            obj.step_geometry_modified = True
            off = getattr(obj, "step_export_offset", (0.0, 0.0, 0.0))
            try:
                obj.step_export_offset = (
                    float(off[0]) + float(dx),
                    float(off[1]) + float(dy),
                    float(off[2]) + float(dz),
                )
            except Exception:
                obj.step_export_offset = (float(dx), float(dy), float(dz))
        return True

    def _translate_object(self, obj, dx: float, dy: float, dz: float) -> bool:
        t = type(obj).__name__
        if t in {"BoxObject", "PlateObject"}:
            obj.x1 += dx; obj.y1 += dy; obj.z1 += dz
            obj.x2 += dx; obj.y2 += dy; obj.z2 += dz
            obj._refresh_source(); obj.refresh_appearance(); return True
        if t in {"CylinderObject", "ConeObject", "SphereObject"}:
            obj.cx += dx; obj.cy += dy; obj.cz += dz
            if t in {"CylinderObject", "ConeObject"}:
                obj._apply_orientation()
            else:
                obj._refresh_source()
            obj.refresh_appearance(); return True
        if t == "PyramidObject":
            obj.bx1 += dx; obj.by1 += dy; obj.bx2 += dx; obj.by2 += dy
            obj.bz += dz; obj.apex_z += dz
            obj._refresh_source(); obj._apply_position(); obj.refresh_appearance(); return True
        if t == "WedgeObject":
            obj.x1 += dx; obj.y1 += dy; obj.z1 += dz
            obj.x2 += dx; obj.y2 += dy; obj.z2 += dz
            obj.x3 += dx; obj.y3 += dy
            obj._source = obj._make_wedge_polydata()
            obj._actor.GetMapper().SetInputData(obj._source)
            obj.refresh_appearance(); return True
        if t == "TorusObject":
            obj.cx += dx; obj.cy += dy; obj.cz += dz
            obj._actor.SetPosition(obj.cx, obj.cy, obj.cz)
            obj.refresh_appearance(); return True
        if t == "EllipsoidObject":
            obj.cx += dx; obj.cy += dy; obj.cz += dz
            obj._apply_scale(); obj.refresh_appearance(); return True
        if t == "ExtrudedObject":
            obj._plane_origin = (obj._plane_origin[0] + dx, obj._plane_origin[1] + dy, obj._plane_origin[2] + dz)
            obj._rebuild(); return True
        if t == "RevolvedObject":
            obj._axis_pt1 = (obj._axis_pt1[0] + dx, obj._axis_pt1[1] + dy, obj._axis_pt1[2] + dz)
            obj._axis_pt2 = (obj._axis_pt2[0] + dx, obj._axis_pt2[1] + dy, obj._axis_pt2[2] + dz)
            obj._plane_origin = (obj._plane_origin[0] + dx, obj._plane_origin[1] + dy, obj._plane_origin[2] + dz)
            local_pts, user_matrix = obj._profile_local_and_transform()
            obj._revolve.SetInputData(obj._profile_to_polydata(local_pts))
            obj._revolve.SetAngle(obj._angle)
            obj._revolve.Update()
            if obj._actor is not None:
                obj._actor.SetUserMatrix(user_matrix)
            obj.refresh_appearance(); return True
        if t == "MeshObject":
            return self._translate_mesh_object(obj, dx, dy, dz)
        return False

    def _move_selection_to_plane_origin(self) -> None:
        selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(self, "Move On Plane", "Select one or more objects first.")
            return

        scene = self._viewport.scene
        plane = scene.active_plane
        if plane is None:
            QMessageBox.warning(self, "Move On Plane", "No active reference plane.")
            return

        centers = []
        for obj in selection:
            actor = getattr(obj, "actor", None)
            if actor is None:
                continue
            b = actor.GetBounds()
            if b is None:
                continue
            centers.append(((b[0] + b[1]) * 0.5, (b[2] + b[3]) * 0.5, (b[4] + b[5]) * 0.5))
        if not centers:
            QMessageBox.warning(self, "Move On Plane", "Unable to determine selected object centers.")
            return

        cx = sum(p[0] for p in centers) / len(centers)
        cy = sum(p[1] for p in centers) / len(centers)
        cz = sum(p[2] for p in centers) / len(centers)

        ox, oy, oz = [float(v) for v in plane.origin]
        nx, ny, nz = [float(v) for v in plane.normal]
        mag = (nx * nx + ny * ny + nz * nz) ** 0.5
        if mag < 1e-12:
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx, ny, nz = nx / mag, ny / mag, nz / mag

        dx = ox - cx
        dy = oy - cy
        dz = oz - cz
        # Keep translation constrained on the active plane.
        dot = dx * nx + dy * ny + dz * nz
        dx -= dot * nx
        dy -= dot * ny
        dz -= dot * nz

        moved = 0
        failed = []
        for obj in selection:
            try:
                if self._translate_object(obj, dx, dy, dz):
                    moved += 1
                else:
                    failed.append(str(getattr(obj, "name", type(obj).__name__)))
            except Exception:
                failed.append(str(getattr(obj, "name", type(obj).__name__)))

        if moved:
            self._viewport.scene_changed.emit()
            self._refresh_materials()
            self._viewport._render()
            self._viewport.selection_changed.emit(selection)
            self._mark_simulation_dirty(steps=True, script=True)

        msg = f"Moved {moved} object(s) on plane '{plane.name}' toward origin."
        if failed:
            msg += f" Failed: {', '.join(failed)}"
        self._info_bar.set_info(msg)

    def _serialize_object_snapshot(self, obj):
        item = {
            "type": type(obj).__name__,
            "name": str(getattr(obj, "name", type(obj).__name__)),
            "params": obj.get_parameters() if hasattr(obj, "get_parameters") else {},
            "visible": bool(obj.is_visible()) if hasattr(obj, "is_visible") else True,
            "is_model": bool(getattr(obj, "is_model", True)),
        }
        if type(obj).__name__ == "MeshObject":
            mesh_poly = None
            if getattr(obj, "actor", None) is not None and obj.actor.GetMapper() is not None:
                mesh_poly = obj.actor.GetMapper().GetInput()
            item["mesh"] = self._viewport.scene._polydata_to_json(mesh_poly)
        return item

    def _rebuild_object_from_snapshot(self, item):
        if not isinstance(item, dict):
            return None

        from ..scene import em_objects as emo

        t = item.get("type")
        cls = getattr(emo, str(t), None)
        if cls is None:
            return None

        name = str(item.get("name", ""))
        p = item.get("params", {})
        if not isinstance(p, dict):
            p = {}

        try:
            if t == "MeshObject":
                mesh_blob = item.get("mesh")
                mesh_poly = self._viewport.scene._polydata_from_json(mesh_blob)
                obj = cls(
                    name,
                    mesh_poly,
                    p.get("Material", "PEC"),
                    step_source_path=p.get("StepSourcePath"),
                    step_solid_name=p.get("StepSolidName"),
                    boolean_op=p.get("BooleanOperation"),
                    boolean_source_names=p.get("BooleanSourceNames"),
                    boolean_sources_data=p.get("BooleanSourcesData"),
                )
                if hasattr(obj, "set_parameters"):
                    obj.set_parameters(p)
                if (
                    "StepGeometryModified" not in p
                    and mesh_blob is not None
                    and str(p.get("StepSourcePath", "") or "").strip()
                ):
                    obj.step_geometry_modified = True
            else:
                obj = cls(name=name, material=p.get("Material", "PEC"))
                if hasattr(obj, "set_parameters"):
                    obj.set_parameters(p)

            obj.set_visible(bool(item.get("visible", True)))
            obj.is_model = bool(item.get("is_model", True))
            obj.refresh_appearance()
            return obj
        except Exception:
            return None

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� boolean operations
    def _do_boolean(self, op: str, label: str) -> None:
        from ..scene.boolean_ops import boolean, fuse_many
        from ..scene.em_objects import MeshObject

        sel = list(self._viewport.scene.selection)
        if len(sel) < 2:
            QMessageBox.information(
                self,
                f"Boolean {label}",
                "Select at least 2 objects (viewport or Object/Materials tree), then run the operation.\n\n"
                "Seleziona da Object/Materials tree con Ctrl/Shift-click per multi-selezione.\n"
                "First selected = Base, all others = Tools.",
            )
            return

        base = sel[0]
        tools = sel[1:]
        names = ", ".join(o.name for o in tools[:8])
        if len(tools) > 8:
            names += f", ... (+{len(tools)-8} more)"

        reply = QMessageBox.question(
            self,
            f"Confirm Boolean {label}",
            f"Perform Boolean {label}?\n\n"
            f"Base : {base.name}\n"
            f"Tools: {len(tools)} object(s)\n"
            f"{names}\n\n"
            "All source objects will be removed and replaced by one result.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._info_bar.set_info(f"Boolean {label} cancelled.")
            return

        current = base
        current_poly = None
        try:
            if op == "fuse" and len(sel) > 2:
                current_poly = fuse_many(sel)
            else:
                for idx, tool in enumerate(tools):
                    current_poly = boolean(op, current, tool)
                    if idx < len(tools) - 1:
                        current = MeshObject(
                            name=f"_tmp_{label}_{idx}",
                            polydata=current_poly,
                            material=base.material,
                        )
        except Exception as exc:
            QMessageBox.critical(self, f"Boolean {label} failed", str(exc))
            self._info_bar.set_info(f"Boolean {label} failed: {exc}")
            return

        result = MeshObject(
            name=f"{label}_{base.name}",
            polydata=current_poly,
            material=base.material,
            boolean_op=op,
            boolean_source_names=[o.name for o in ([base] + tools)],
        )
        result.source_objects = list([base] + tools)
        result.boolean_sources_data = [self._serialize_object_snapshot(o) for o in ([base] + tools)]
        result.refresh_appearance()

        scene = self._viewport.scene
        scene.add_object(result)
        for obj in [base] + tools:
            scene.remove_object(obj)
        scene.select(result)

        self._viewport.object_selected.emit(result)
        self._viewport.selection_changed.emit([result])
        self._viewport.scene_changed.emit()
        self._refresh_materials()
        self._viewport._render()
        self._info_bar.set_info(
            f"Boolean {label}: created {result.name} from {len(sel)} objects"
        )

    def _bool_cut(self) -> None:
        self._do_boolean("cut", "Cut")

    def _bool_fuse(self) -> None:
        self._do_boolean("fuse", "Fuse")

    def _bool_common(self) -> None:
        self._do_boolean("common", "Common")

    def _bool_dissolve(self) -> None:
        selection = list(self._viewport.scene.selection)
        if not selection:
            QMessageBox.information(
                self,
                "Dissolve Boolean",
                "Select one or more boolean result objects, then run Dissolve Boolean.",
            )
            return

        candidates = []
        unavailable = []
        for obj in selection:
            op = str(getattr(obj, "boolean_op", "") or "").strip().lower()
            sources = list(getattr(obj, "source_objects", []) or [])
            source_data = list(getattr(obj, "boolean_sources_data", []) or [])
            if op in {"fuse", "cut", "common"} and (sources or source_data):
                candidates.append((obj, sources, source_data))
            else:
                unavailable.append(str(getattr(obj, "name", type(obj).__name__)))

        if not candidates:
            detail = ""
            if unavailable:
                detail = "\n\nUnavailable: " + ", ".join(unavailable)
            QMessageBox.information(
                self,
                "Dissolve Boolean",
                "Selected objects do not contain dissolvable boolean provenance." + detail,
            )
            return

        reply = QMessageBox.question(
            self,
            "Confirm Dissolve Boolean",
            f"Dissolve {len(candidates)} boolean result(s) and restore their source objects?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._info_bar.set_info("Dissolve Boolean cancelled.")
            return

        scene = self._viewport.scene
        restored = []
        restored_ids = {id(obj) for obj in scene.objects}

        existing_names = {str(getattr(o, "name", "")) for o in scene.objects}

        for result_obj, sources, source_data in candidates:
            # Remove result first so source names equal to result name can be restored.
            result_name = str(getattr(result_obj, "name", ""))
            scene.remove_object(result_obj)
            restored_ids.discard(id(result_obj))
            if result_name:
                existing_names.discard(result_name)

            added_this_result = 0
            if sources:
                for source_obj in sources:
                    # Guard against corrupted provenance where the result object
                    # is accidentally present in its own source list.
                    if source_obj is result_obj:
                        continue
                    if id(source_obj) in restored_ids:
                        # Already in scene – still count it so selection works
                        if source_obj in scene.objects:
                            restored.append(source_obj)
                            added_this_result += 1
                        continue
                    scene.add_object(source_obj)
                    source_obj.refresh_appearance()
                    restored.append(source_obj)
                    restored_ids.add(id(source_obj))
                    existing_names.add(str(getattr(source_obj, "name", "")))
                    added_this_result += 1

            # Use snapshot data as fallback when live references produced no results
            # (e.g. after project reload where source_objects is empty).
            if added_this_result == 0 and source_data:
                for item in source_data:
                    rebuilt = self._rebuild_object_from_snapshot(item)
                    if rebuilt is None:
                        continue
                    if rebuilt.name in existing_names:
                        # Object already in scene under the same name – select it
                        for o in scene.objects:
                            if getattr(o, "name", "") == rebuilt.name and o not in restored:
                                restored.append(o)
                                break
                        continue
                    scene.add_object(rebuilt)
                    restored.append(rebuilt)
                    restored_ids.add(id(rebuilt))
                    existing_names.add(str(rebuilt.name))

        # Rebuild the tree FIRST so that highlight() can find the restored objects.
        self._viewport.scene_changed.emit()
        self._refresh_materials()

        if restored:
            scene.deselect_all()
            for source_obj in restored:
                scene.select_add(source_obj)
            self._viewport.object_selected.emit(restored[-1])
            self._viewport.selection_changed.emit(restored)
        else:
            scene.select(None)
            self._viewport.object_selected.emit(None)
            self._viewport.selection_changed.emit([])

        self._viewport._render()

        message = f"Dissolved {len(candidates)} boolean result(s); restored {len(restored)} source object(s)"
        if unavailable:
            message += f". Skipped: {', '.join(unavailable)}"
        self._info_bar.set_info(message)

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� object selection
    def _on_object_selected(self, obj) -> None:
        self._body_props.set_object(obj)
        self._materials.highlight(obj)
        # Update Fuse counter when single object selected
        self._fuse_label.setText(" Fuse (1) ")
        self._fuse_label.setStyleSheet("font-size: 10px; color: #666;")
        if obj:
            self._info_bar.set_info(f"Selected: {obj.name}  [{type(obj).__name__}]")

    def _on_selection_changed(self, objects: list) -> None:
        self._body_props.set_selection(objects)
        self._materials.highlight(objects)
        # Update Fuse counter in toolbar
        if len(objects) >= 2:
            self._fuse_label.setText(f" Fuse ({len(objects)}) ")
            self._fuse_label.setStyleSheet("font-size: 10px; color: #0a0; font-weight: bold;")
        else:
            self._fuse_label.setText(" Fuse (0) ")
            self._fuse_label.setStyleSheet("font-size: 10px; color: #666;")
        if len(objects) > 1:
            self._info_bar.set_info(f"{len(objects)} objects selected (Ctrl+click to extend)")

    def _on_params_changed(self, obj, _params) -> None:
        self._viewport._render()
        self._refresh_materials()
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_bulk_material(self, material: str, objects: list) -> None:
        self._draw_material = material
        self._viewport._render()
        self._refresh_materials()
        self._materials.highlight(objects)  # Now includes signal emit
        self._info_bar.set_info(f"Material '{material}' applied to {len(objects)} objects")
        self._mark_simulation_dirty(steps=False, script=True)

    def _on_bulk_style(self, material: str, color_hex: str, objects: list) -> None:
        self._draw_material = material
        self._viewport._render()
        self._refresh_materials()
        self._materials.highlight(objects)  # Now includes signal emit
        self._info_bar.set_info(
            f"Applied material '{material}' and color {color_hex} to {len(objects)} objects"
        )
        self._mark_simulation_dirty(steps=False, script=True)

    def _on_material_added(self, name: str) -> None:
        if not name:
            return
        if self._material_store.get_record(name, source="project") is None:
            self._material_store.add_project_material(name=name)
        self._sync_material_choices()

    def _open_material_picker(self, current_name: str, selected_objects: list) -> None:
        dlg = MaterialAssignDialog(
            self,
            project_records=self._material_store.project_records(),
            global_records=self._material_store.global_records(),
            selected_name=current_name,
            can_append_global=bool(self._material_store.global_records()),
        )
        if dlg.exec_() != dlg.Accepted:
            return

        rec = dlg.selected_record
        if rec is None:
            return

        if dlg.appended_names:
            added = self._material_store.append_global_to_project(dlg.appended_names)
            if added:
                self._sync_material_choices()
                self._info_bar.set_info(f"Appended {len(added)} material(s) to project DB")

        if rec.source == "global" and self._material_store.get_record(rec.name, source="project") is None:
            self._material_store.append_global_to_project([rec.name])
            self._sync_material_choices()
        elif rec.source == "project" and self._material_store.get_record(rec.name, source="project") is None:
            self._material_store.upsert_project_record(rec)
            self._sync_material_choices()

        name = rec.name
        self._draw_material = name

        if len(selected_objects) > 1:
            for obj in selected_objects:
                obj.material = name
                obj.refresh_appearance()
            self._viewport._render()
            self._refresh_materials()
            self._info_bar.set_info(f"Material '{name}' applied to {len(selected_objects)} objects")
            self._mark_simulation_dirty(steps=False, script=True)
            return

        self._body_props.set_selected_material(name)

    def _on_material_tree_select(self, obj) -> None:
        self._viewport.scene.select(obj)
        self._body_props.set_object(obj)
        self._viewport._render()

    def _on_material_tree_multi_select(self, objects: list) -> None:
        # Update SceneManager selection to match tree selection
        self._viewport.scene.deselect_all()
        if not objects:
            self._body_props.set_object(None)
            self._viewport._render()
            return
        for o in objects:
            self._viewport.scene.select_add(o)
        self._body_props.set_selection(objects)
        self._viewport._render()

    def _on_materials_hide(self, objects: list) -> None:
        if not objects:
            return
        self._viewport.scene.set_visibility(objects, False)
        self._refresh_materials()
        self._materials.highlight(objects)  # Preserve selection after refresh
        self._viewport._render()
        self._info_bar.set_info(f"Hidden {len(objects)} object(s)")

    def _on_materials_show(self, objects: list) -> None:
        if not objects:
            return
        self._viewport.scene.set_visibility(objects, True)
        self._refresh_materials()
        self._materials.highlight(objects)  # Preserve selection after refresh
        self._viewport._render()
        self._info_bar.set_info(f"Shown {len(objects)} object(s)")

    def _on_materials_model_role_changed(self, objects: list, is_model: bool) -> None:
        if not objects:
            return
        for obj in objects:
            obj.is_model = bool(is_model)
        self._refresh_materials()
        self._materials.highlight(objects)
        self._viewport._render()
        role_txt = "MODEL" if is_model else "NON MODEL"
        self._info_bar.set_info(f"Set {len(objects)} object(s) as {role_txt}")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_model_role_changed(self, obj, is_model: bool) -> None:
        if obj is None:
            return
        obj.is_model = bool(is_model)
        self._refresh_materials()
        self._materials.highlight(obj)
        self._viewport._render()
        role_txt = "MODEL" if is_model else "NON MODEL"
        self._info_bar.set_info(f"{obj.name}: {role_txt}")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_materials_rename(self, obj, new_name: str) -> None:
        if obj is None or not new_name:
            return
        old_name = obj.name
        obj.name = new_name
        self._project_tree.rename_object_references(old_name, new_name)
        self._refresh_materials()
        self._materials.highlight(obj)  # Preserve selection after refresh
        if self._viewport.scene.selected is obj:
            self._body_props.set_object(obj)
        self._viewport._render()
        self._info_bar.set_info(f"Renamed to: {new_name}")
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_materials_bulk_rename(self, objects: list, base_name: str, start_index: int) -> None:
        if not objects or not base_name:
            return
        for i, obj in enumerate(objects):
            old_name = obj.name
            new_name = f"{base_name}_{start_index + i}"
            obj.name = new_name
            self._project_tree.rename_object_references(old_name, new_name)
        self._refresh_materials()
        self._materials.highlight(objects)
        self._viewport._render()
        self._info_bar.set_info(
            f"Renamed {len(objects)} object(s): {base_name}_{start_index} – {base_name}_{start_index + len(objects) - 1}"
        )
        self._mark_simulation_dirty(steps=True, script=True)

    def _on_assign_port_requested(self, obj) -> None:
        if obj is None:
            return
        self._project_tree.assign_port_to_object(obj.name)
        self._info_bar.set_info(f"Port assignment updated for {obj.name}")

    def _on_assign_boundary_requested(self, obj) -> None:
        if obj is None:
            return
        self._project_tree.assign_boundary_to_object(obj.name)
        self._info_bar.set_info(f"Boundary assignment updated for {obj.name}")

    def _on_assign_mesh_resolution_requested(self, objects: list) -> None:
        if not objects:
            return
        settings = self._project_tree.get_settings()
        mesh_cfg = settings.get("mesh")
        if not isinstance(mesh_cfg, dict):
            mesh_cfg = {}
            settings["mesh"] = mesh_cfg

        current_default = mesh_cfg.get("default_fraction", 0.3)
        try:
            current_default = float(current_default)
        except Exception:
            current_default = 0.3
        current_default = max(0.01, min(1.0, current_default))

        value, ok = QInputDialog.getDouble(
            self,
            "Assign Mesh Resolution",
            "Resolution fraction (1/λ):",
            current_default,
            0.01,
            1.0,
            4,
        )
        if not ok:
            return

        mesh_assign = mesh_cfg.get("object_resolutions")
        if not isinstance(mesh_assign, dict):
            mesh_assign = {}
            mesh_cfg["object_resolutions"] = mesh_assign

        names: list[str] = []
        seen: set[str] = set()
        for obj in objects:
            name = str(getattr(obj, "name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)

        for name in names:
            mesh_assign[name] = float(value)
        mesh_cfg["default_fraction"] = float(value)

        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._mark_simulation_dirty(steps=False, script=True)
        if len(names) == 1:
            self._info_bar.set_info(f"Mesh 1/λ set to {value:.4g} for {names[0]}")
        else:
            self._info_bar.set_info(f"Mesh 1/λ set to {value:.4g} for {len(names)} objects")

    def _on_material_priority_changed(self, material_name: str, delta: int) -> None:
        """Handle material priority change: delta is 1 for higher, -1 for lower."""
        if not material_name:
            return
        # Get current priorities from project settings
        settings = self._project_tree.get_settings()
        priorities = settings.get("material_priorities")
        if not isinstance(priorities, dict):
            priorities = {}
            settings["material_priorities"] = priorities
        # Update priority
        current_priority = priorities.get(material_name, 0)
        new_priority = current_priority + delta
        priorities[material_name] = new_priority
        # Apply the full settings payload back to the widget.
        self._project_tree.load_settings(settings)
        self._project_tree.settings_changed.emit()
        self._refresh_materials()
        self._reset_simulation_cache()
        self._info_bar.set_info(f"Material '{material_name}' priority set to {new_priority}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� STEP import
    def _import_step(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import STEP File", "",
            "STEP Files (*.step *.stp);;All Files (*)"
        )
        if not path:
            return
        try:
            solids = import_step(path)
        except RuntimeError as exc:
            QMessageBox.warning(
                self, "STEP Import",
                str(exc)
            )
            return
        except Exception as exc:
            QMessageBox.critical(self, "STEP Import Error", str(exc))
            return

        from ..scene.em_objects import MeshObject
        
        added = []
        for solid in solids:
            obj = MeshObject(
                name     = solid["name"],
                polydata = solid["polydata"],
                material = self._draw_material,
                color    = solid.get("color"),
                step_source_path = path,
                step_solid_name = solid["name"],
            )
            self._viewport.scene.add_object(obj)
            added.append(obj)

        if added:
            self._viewport.scene.select(added[-1])
            self._viewport.object_selected.emit(added[-1])
            self._viewport.selection_changed.emit([added[-1]])
            self._viewport.scene_changed.emit()
            self._viewport._render()
            self._refresh_materials()
            self._info_bar.set_info(
                f"STEP imported: {len(added)} solid(s) from {Path(path).name}"
            )

    def _open_simulation_window(self) -> None:
        dlg = getattr(self, "_sim_dlg", None)
        if dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("EMERGE Simulation")
            dlg.resize(1200, 760)

            root = QVBoxLayout(dlg)

            script_box = QGroupBox("Python Script to run with EMERGE")
            script_layout = QVBoxLayout(script_box)
            self._sim_script_tabs = QTabWidget(script_box)
            self._sim_script_view = QPlainTextEdit(script_box)
            self._sim_script_view.setReadOnly(True)
            self._sim_script_view.setLineWrapMode(QPlainTextEdit.NoWrap)
            self._sim_script_tabs.addTab(self._sim_script_view, "Master")
            script_layout.addWidget(self._sim_script_tabs)

            log_box = QGroupBox("Verbose execution log")
            log_layout = QVBoxLayout(log_box)
            log_top_row = QHBoxLayout()
            log_top_row.addStretch()
            _btn_clear_log = QPushButton("Clear Log")
            _btn_clear_log.setFixedWidth(90)
            _btn_clear_log.setToolTip("Clear the verbose execution log")
            _btn_clear_log.clicked.connect(lambda: self._sim_log_view.clear() if hasattr(self, "_sim_log_view") else None)
            log_top_row.addWidget(_btn_clear_log)
            log_layout.addLayout(log_top_row)
            self._sim_log_view = QPlainTextEdit(log_box)
            self._sim_log_view.setReadOnly(True)
            self._sim_log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
            log_layout.addWidget(self._sim_log_view)

            btn_row = QHBoxLayout()
            self._sim_chk_show_model = QCheckBox("Show Model")
            self._sim_chk_show_model.setChecked(False)
            self._sim_chk_show_model.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_show_model)

            self._sim_chk_show_mesh = QCheckBox("Show Mesh")
            self._sim_chk_show_mesh.setChecked(False)
            self._sim_chk_show_mesh.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_show_mesh)

            self._sim_chk_run_sweep = QCheckBox("Run Sweep")
            self._sim_chk_run_sweep.setChecked(True)
            self._sim_chk_run_sweep.setEnabled(False)
            self._sim_chk_run_sweep.setToolTip("Simulation execution is always enabled")
            btn_row.addWidget(self._sim_chk_run_sweep)

            self._sim_chk_boolean_debug = QCheckBox("Boolean Debug")
            self._sim_chk_boolean_debug.setChecked(False)
            self._sim_chk_boolean_debug.setToolTip("Export only boolean source objects, shifted apart for visual inspection")
            self._sim_chk_boolean_debug.toggled.connect(self._on_sim_option_changed)
            btn_row.addWidget(self._sim_chk_boolean_debug)

            btn_row.addWidget(QLabel("Log:"))
            self._sim_log_level = QComboBox()
            self._sim_log_level.addItems(["Trace", "Debug", "Info", "Warning", "Error"])
            self._sim_log_level.setCurrentText(self._sim_log_verbosity.title())
            self._sim_log_level.currentTextChanged.connect(self._on_sim_log_level_changed)
            btn_row.addWidget(self._sim_log_level)

            self._sim_btn_generate = QPushButton("Generate Script")
            self._sim_btn_generate.clicked.connect(self._on_sim_generate)
            btn_row.addWidget(self._sim_btn_generate)

            self._sim_btn_save = QPushButton("Save Script")
            self._sim_btn_save.clicked.connect(self._on_sim_save_script)
            btn_row.addWidget(self._sim_btn_save)

            self._sim_btn_run = QPushButton("Run")
            self._sim_btn_run.clicked.connect(self._on_sim_run)
            btn_row.addWidget(self._sim_btn_run)

            self._sim_btn_stop = QPushButton("Stop")
            self._sim_btn_stop.clicked.connect(self._on_sim_stop)
            self._sim_btn_stop.setEnabled(False)
            btn_row.addWidget(self._sim_btn_stop)

            btn_row.addStretch(1)

            root.addWidget(script_box, stretch=3)
            root.addLayout(btn_row)
            root.addWidget(log_box, stretch=2)

            self._sim_dlg = dlg

        try:
            self._generate_simulation_assets(
                show_progress=True,
                force_script=True,
                force_step_export=True,
            )
        except Exception as exc:
            self._append_sim_log(f"[error] Failed to generate script: {exc}")
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_sim_option_changed(self, _checked: bool) -> None:
        self._mark_simulation_dirty(steps=False, script=True)
        self._on_sim_generate(show_progress=False, force_script=True)

    def _normalize_log_level(self, value: str) -> str:
        level = str(value or "").strip().upper()
        return level if level in _LOG_LEVEL_ORDER else "INFO"

    def _step_log_enabled(self, level: str) -> bool:
        want = _LOG_LEVEL_ORDER.get(self._normalize_log_level(level), 20)
        current = _LOG_LEVEL_ORDER.get(self._normalize_log_level(self._sim_log_verbosity), 20)
        return want >= current

    def _sync_log_verbosity_from_settings(self) -> None:
        try:
            self._sim_log_verbosity = self._normalize_log_level(self._project_tree.get_log_verbosity())
        except Exception:
            self._sim_log_verbosity = "INFO"

        combo = getattr(self, "_sim_log_level", None)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentText(self._sim_log_verbosity.title())
            combo.blockSignals(False)

    def _on_sim_log_level_changed(self, text: str) -> None:
        self._sim_log_verbosity = self._normalize_log_level(text)
        try:
            self._project_tree.set_log_verbosity(self._sim_log_verbosity.title())
        except Exception:
            pass
        self._append_sim_log(f"[info] STEP export log verbosity: {self._sim_log_verbosity}")

    def _collect_plate_lumped_ports(self) -> list[dict]:
        settings_ports = self._project_tree.get_settings().get("ports", [])
        if not isinstance(settings_ports, list):
            return []

        plate_by_name = {
            str(obj.name): obj
            for obj in self._viewport.scene.objects
            if type(obj).__name__ == "PlateObject" and bool(getattr(obj, "is_model", True))
        }

        entries: list[dict] = []
        for idx, port in enumerate(settings_ports, start=1):
            if not isinstance(port, dict):
                continue
            if str(port.get("type", "")).strip() != "LumpedPort":
                continue

            obj_name = str(port.get("object", "")).strip()
            if not obj_name:
                continue
            plate = plate_by_name.get(obj_name)
            if plate is None:
                continue

            p = plate.get_parameters()
            xmin, xmax = sorted([float(p.get("X1", 0.0)), float(p.get("X2", 0.0))])
            ymin, ymax = sorted([float(p.get("Y1", 0.0)), float(p.get("Y2", 0.0))])
            zmin, zmax = sorted([float(p.get("Z1", 0.0)), float(p.get("Z2", 0.0))])

            spans = [xmax - xmin, ymax - ymin, zmax - zmin]
            normal_axis = min(range(3), key=lambda i: abs(spans[i]))
            tangent_axes = [a for a in (0, 1, 2) if a != normal_axis]

            mins = [xmin, ymin, zmin]
            maxs = [xmax, ymax, zmax]
            origin = [mins[0], mins[1], mins[2]]
            origin[normal_axis] = (mins[normal_axis] + maxs[normal_axis]) / 2.0

            u = [0.0, 0.0, 0.0]
            v = [0.0, 0.0, 0.0]
            u[tangent_axes[0]] = maxs[tangent_axes[0]] - mins[tangent_axes[0]]
            v[tangent_axes[1]] = maxs[tangent_axes[1]] - mins[tangent_axes[1]]

            params = port.get("params", {}) if isinstance(port.get("params", {}), dict) else {}
            configured_direction = [
                float(params.get("Direction_X", 0.0)),
                float(params.get("Direction_Y", 0.0)),
                float(params.get("Direction_Z", 0.0)),
            ]
            direction_magnitude = sum(value * value for value in configured_direction) ** 0.5
            if direction_magnitude > 1e-9:
                # An explicit port direction is part of the user's excitation
                # definition and must be preserved in the generated script.
                direction = [value / direction_magnitude for value in configured_direction]
            else:
                direction = [0.0, 0.0, 0.0]
                direction[normal_axis] = 1.0
            z0 = float(params.get("Resistance_Ohm", 50.0))
            power = float(params.get("Voltage_V", 1.0))

            entries.append(
                {
                    "index": idx,
                    "name": str(port.get("name", f"Port_{idx}")),
                    "plate_name": obj_name,
                    "origin": origin,
                    "u": u,
                    "v": v,
                    "width": abs(u[tangent_axes[0]]),
                    "height": abs(v[tangent_axes[1]]),
                    "direction": direction,
                    "z0": z0,
                    "power": power,
                }
            )
        return entries

    def _enabled_simulations(self, settings: dict) -> list[dict]:
        sims = settings.get("simulations", [])
        out: list[dict] = []
        if isinstance(sims, list):
            for idx, item in enumerate(sims, start=1):
                if not isinstance(item, dict):
                    continue
                if not bool(item.get("enabled", True)):
                    continue
                name = str(item.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}"
                out.append({
                    "name": name,
                    "type": str(item.get("type", "Sweep")).strip() or "Sweep",
                    "enabled": True,
                    "Fmin_GHz": item.get("Fmin_GHz", 0.1),
                    "Fmax_GHz": item.get("Fmax_GHz", 10.0),
                    "Fstep_GHz": item.get("Fstep_GHz", 0.1),
                    "EigenmodeCount": item.get("EigenmodeCount", 5),
                    "ParamName": item.get("ParamName", ""),
                    "ParamValues": item.get("ParamValues", ""),
                    "sparam_fitting": dict(item.get("sparam_fitting", {})) if isinstance(item.get("sparam_fitting", {}), dict) else {"enabled": False, "points": 1001},
                    "LogVerbosity": item.get("LogVerbosity", "Info"),
                })

        if out:
            return out

        legacy = settings.get("simulation", {})
        if not isinstance(legacy, dict):
            legacy = {}
        return [{
            "name": "Simulation_1",
            "type": "Sweep",
            "enabled": True,
            "Fmin_GHz": legacy.get("Fmin_GHz", 0.1),
            "Fmax_GHz": legacy.get("Fmax_GHz", 10.0),
            "Fstep_GHz": legacy.get("Fstep_GHz", 0.1),
            "EigenmodeCount": legacy.get("EigenmodeCount", 5),
            "ParamName": legacy.get("ParamName", ""),
            "ParamValues": legacy.get("ParamValues", ""),
            "sparam_fitting": dict(legacy.get("sparam_fitting", {})) if isinstance(legacy.get("sparam_fitting", {}), dict) else {"enabled": False, "points": 1001},
            "LogVerbosity": legacy.get("LogVerbosity", "Info"),
        }]

    def _safe_script_token(self, value: str) -> str:
        token = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(value))
        token = token.strip("_")
        return token or "Simulation"

    def _build_master_simulation_script(
        self,
        jobs: list[dict],
    ) -> str:
        job_lines = []
        for job in jobs:
            job_lines.append(
                "        {"
                f"'name': {repr(str(job.get('name', 'Simulation')))}, "
                f"'type': {repr(str(job.get('type', 'Sweep')).strip().lower())}, "
                f"'filename': {repr(str(job.get('filename', 'simulation_emerge_run.py')))}"
                "},"
            )
        jobs_literal = "\n".join(job_lines) if job_lines else ""
        return (
            "# Auto-generated EMERGE master script\n"
            "import os\n"
            "import sys\n"
            "import subprocess\n\n"
            "STOP_ON_ERROR = True\n\n"
            "def _run_children() -> int:\n"
            "    script_dir = os.path.dirname(os.path.abspath(__file__))\n"
            "    jobs = [\n"
            f"{jobs_literal}\n"
            "    ]\n"
            "    for job in jobs:\n"
            "        child = str(job.get('filename', ''))\n"
            "        child_path = os.path.join(script_dir, child)\n"
            "        env = os.environ.copy()\n"
            "        env.setdefault('PYTHONIOENCODING', 'utf-8')\n"
            "        env.setdefault('PYTHONUTF8', '1')\n"
            "        print(f\"[master] Running job '{job.get('name')}' from {child_path}\")\n"
            "        result = subprocess.run([sys.executable, '-u', child_path], cwd=script_dir, env=env)\n"
            "        if result.returncode != 0:\n"
            "            print(f\"[master] Job failed ({result.returncode}): {job.get('name')}\")\n"
            "            if STOP_ON_ERROR:\n"
            "                return int(result.returncode)\n"
            "    print('[master] All simulations completed successfully.')\n"
            "    return 0\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(_run_children())\n"
        )

    def _build_simulation_script_bundle(self, step_entries: list[dict], show_model: bool, show_mesh: bool, run_sweep: bool) -> dict:
        settings = self._project_tree.get_settings()
        mesh_cfg = settings.get("mesh", {}) if isinstance(settings, dict) else {}
        mesh_fraction = self._mesh_resolution
        if isinstance(mesh_cfg, dict):
            try:
                mesh_fraction = max(0.01, min(1.0, float(mesh_cfg.get("default_fraction", mesh_fraction))))
            except Exception:
                pass

        enabled_sims = self._enabled_simulations(settings)
        scripts: list[dict] = []
        for idx, sim_cfg in enumerate(enabled_sims, start=1):
            sim_name = str(sim_cfg.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}"
            child_settings = dict(settings)
            child_settings["simulations"] = [dict(sim_cfg)]
            child_settings["simulation"] = {
                "Fmin_GHz": sim_cfg.get("Fmin_GHz", 0.1),
                "Fmax_GHz": sim_cfg.get("Fmax_GHz", 10.0),
                "Fstep_GHz": sim_cfg.get("Fstep_GHz", 0.1),
                "LogVerbosity": sim_cfg.get("LogVerbosity", "Info"),
            }
            safe_project = self._safe_script_token(self._project_name)
            safe_sim = self._safe_script_token(sim_name)
            filename = f"{safe_project}_{idx:02d}_{safe_sim}_emerge_run.py"
            script_text = export_emerge_python_script(
                project_name=self._project_name,
                settings=child_settings,
                step_entries=step_entries,
                units=self._units,
                materials_catalog=self._material_store.material_export_catalog(),
                show_model=show_model,
                show_mesh=show_mesh,
                run_sweep=run_sweep,
                lumped_ports=self._collect_plate_lumped_ports(),
                solver=self._sim_solver,
                parallel_enabled=self._sim_parallel_enabled,
                pardiso_threads=self._sim_pardiso_threads,
                acc_threads=self._sim_acc_threads,
                mesh_resolution_fraction=mesh_fraction,
                plot_sparams_after_sim=self._sim_plot_sparams_after_sim,
                export_sparams_after_sim=self._sim_export_sparams_after_sim,
                simulation_override=sim_cfg,
            )
            scripts.append(
                {
                    "name": sim_name,
                    "job_name": sim_name,
                    "type": str(sim_cfg.get("type", "Sweep")).strip().lower(),
                    "index": idx,
                    "filename": filename,
                    "content": script_text,
                }
            )

        master_script = self._build_master_simulation_script(
            scripts,
        )
        return {"master": master_script, "scripts": scripts}

    def _update_simulation_script_tabs(self, script_bundle: dict) -> None:
        tabs = getattr(self, "_sim_script_tabs", None)
        if tabs is None:
            return

        while tabs.count() > 0:
            w = tabs.widget(0)
            tabs.removeTab(0)
            if w is not None:
                w.deleteLater()

        master_editor = QPlainTextEdit(tabs)
        master_editor.setReadOnly(True)
        master_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        master_editor.setPlainText(str(script_bundle.get("master", "")))
        tabs.addTab(master_editor, "Master")
        self._sim_script_view = master_editor

        for item in script_bundle.get("scripts", []):
            editor = QPlainTextEdit(tabs)
            editor.setReadOnly(True)
            editor.setLineWrapMode(QPlainTextEdit.NoWrap)
            editor.setPlainText(str(item.get("content", "")))
            job_name = str(item.get("job_name", item.get("name", "Simulation"))).strip() or "Simulation"
            idx = int(item.get("index", 0))
            tab_name = f"{idx:02d} {job_name}" if idx > 0 else job_name
            tabs.addTab(editor, tab_name)

    def _simulation_model_objects(self) -> list:
        return [obj for obj in self._viewport.scene.objects if bool(getattr(obj, "is_model", True))]

    def _generate_simulation_assets(self, show_progress: bool = True, force_script: bool = False, force_step_export: bool = False) -> str | None:
        progress = None
        canceled = {"value": False}
        sim_objects = self._simulation_model_objects()
        total_candidates = sum(
            1 for obj in sim_objects
            if type(obj).__name__ != "PlateObject"
        )

        need_step_export = force_step_export or self._sim_steps_dirty or (not self._sim_step_bundle_ready)

        if show_progress and need_step_export and total_candidates > 0:
            progress = QProgressDialog("Exporting STEP objects...", "Cancel", 0, total_candidates, self)
            progress.setWindowTitle("Preparing Simulation")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)

        def _progress_cb(done: int, total: int, obj_name: str) -> None:
            msg = f"Converting STEP {done}/{total}: {obj_name}"
            self._info_bar.set_info(msg)
            if progress is not None:
                progress.setMaximum(max(total, 1))
                progress.setValue(done)
                progress.setLabelText(msg)
                QApplication.processEvents()
                if progress.wasCanceled():
                    canceled["value"] = True

        try:
            if need_step_export:
                self._reset_step_export_log()
                self._append_step_export_log("[info] STEP export started.", level="INFO")

                def _step_cb(level: str, message: str) -> None:
                    lvl = self._normalize_log_level(level)
                    if self._step_log_enabled(lvl):
                        self._append_step_export_log(f"[{lvl.lower()}] {message}", level=lvl)

                try:
                    bundle = export_objects_to_step_bundle(
                        objects=sim_objects,
                        bundle_dir=self._simulation_bundle_dir(),
                        progress_callback=_progress_cb,
                        log_callback=_step_cb,
                        debug_boolean_sources_only=bool(getattr(self, "_sim_chk_boolean_debug", None) and self._sim_chk_boolean_debug.isChecked()),
                        material_priorities=self._project_tree.get_settings().get("material_priorities", {}),
                    )
                except Exception as exc:
                    self._append_step_export_log(f"[error] STEP export failed: {exc}", level="ERROR")
                    self._append_step_export_log(traceback.format_exc().rstrip("\n"), level="ERROR")
                    raise

                if canceled["value"]:
                    self._append_step_export_log("[warn] STEP export cancelled by user.", level="WARNING")
                    self._append_sim_log("[warn] STEP export cancelled by user.")
                    return None

                self._sim_step_bundle_cache = bundle
                self._sim_step_bundle_ready = True
                self._sim_steps_dirty = False

                exported = len(bundle.get("entries", []))
                skipped = bundle.get("skipped", [])
                step_msg = f"[info] STEP exported: {exported} object(s) in {self._simulation_bundle_dir()}"
                self._append_sim_log(step_msg)
                self._append_step_export_log(step_msg, level="INFO")
                for entry in bundle.get("entries", []):
                    step_file = self._simulation_bundle_dir() / str(entry.get("step_file", ""))
                    try:
                        step_size = step_file.stat().st_size
                    except OSError:
                        step_size = -1
                    detail = (
                        f"[info] {entry.get('object_name')} | type={entry.get('object_type')} | "
                        f"mode={entry.get('export_mode')} | file={entry.get('step_file')} | "
                        f"size_bytes={step_size} | solids={entry.get('solid_count')} | material={entry.get('material')} | "
                        f"boolean={entry.get('boolean_op')} | sources={entry.get('source_count')} | "
                        f"poly_points={entry.get('poly_points')} | poly_polys={entry.get('poly_polys')}"
                    )
                    self._append_step_export_log(detail, level="INFO")
                if skipped:
                    skipped_msg = "[info] Skipped objects: " + ", ".join(skipped)
                    self._append_sim_log(skipped_msg)
                    self._append_step_export_log(skipped_msg, level="INFO")
                    self._info_bar.set_info(f"STEP export done with skips: exported={exported}, skipped={len(skipped)}")
                else:
                    self._info_bar.set_info(f"STEP export done: {exported} object(s)")
            else:
                bundle = self._sim_step_bundle_cache

            need_script = force_script or self._sim_script_dirty or (not self._sim_cached_script_bundle.get("master", ""))

            if need_script:
                show_model = bool(getattr(self, "_sim_chk_show_model", None) and self._sim_chk_show_model.isChecked())
                show_mesh = bool(getattr(self, "_sim_chk_show_mesh", None) and self._sim_chk_show_mesh.isChecked())
                run_sweep = bool(getattr(self, "_sim_chk_run_sweep", None) and self._sim_chk_run_sweep.isChecked())

                script_bundle = self._build_simulation_script_bundle(
                    bundle.get("entries", []),
                    show_model=show_model,
                    show_mesh=show_mesh,
                    run_sweep=run_sweep,
                )
                self._sim_cached_script_bundle = script_bundle
                script = str(script_bundle.get("master", ""))
                self._sim_cached_script = script
                self._sim_script_dirty = False
                self._append_sim_log(f"[info] Script bundle generated ({len(script_bundle.get('scripts', []))} simulation scripts + master).")
            else:
                script_bundle = self._sim_cached_script_bundle
                script = str(script_bundle.get("master", ""))

            self._update_simulation_script_tabs(script_bundle)
            if need_step_export:
                self._append_step_export_log("[info] STEP export finished.", level="INFO")
            return script
        finally:
            if progress is not None:
                progress.close()

    def _on_sim_generate(self, show_progress: bool = True, force_script: bool = True) -> None:
        try:
            self._generate_simulation_assets(show_progress=show_progress, force_script=force_script)
        except Exception as exc:
            self._append_sim_log(f"[error] Failed to generate script: {exc}")

    def _simulation_project_dir(self) -> Path:
        if self._project_path:
            project_path = Path(self._project_path).expanduser().resolve()
            if project_path.exists():
                if project_path.is_file():
                    return project_path.parent
                if project_path.is_dir():
                    return project_path

            if project_path.suffix.lower() == ".em3d":

                # Recover a stale relative/launcher path by resolving the
                # project file by name inside the workspace.
                workspace_root = Path(__file__).resolve().parents[3]
                matches = list(workspace_root.rglob(project_path.name))
                for match in matches:
                    if match.is_file() and match.suffix.lower() == ".em3d":
                        self._project_path = str(match.resolve())
                        return match.resolve().parent

        # main_window.py lives in <workspace>/src/em3d_modeler/ui.  Resolve
        # first so the result never depends on the launcher's current folder.
        return Path(__file__).resolve().parents[3]

    def _simulation_bundle_dir(self) -> Path:
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self._project_name)
        safe_name = safe_name or "Project"
        out_dir = self._simulation_project_dir() / f"{safe_name}_EmergeSim"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _step_export_log_path(self) -> Path:
        return self._simulation_bundle_dir() / "StepExport.log"

    def _reset_step_export_log(self) -> Path:
        log_path = self._step_export_log_path()
        log_path.write_text("", encoding="utf-8")
        return log_path

    def _append_step_export_log(self, message: str, level: str = "INFO") -> None:
        if not message:
            return
        if not self._step_log_enabled(level):
            return
        try:
            with self._step_export_log_path().open("a", encoding="utf-8") as handle:
                handle.write(message.rstrip("\n") + "\n")
        except Exception:
            pass

    def _simulation_script_path(self) -> Path:
        out_dir = self._simulation_bundle_dir()
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self._project_name)
        safe_name = safe_name or "project"
        return out_dir / f"{safe_name}_master_emerge_run.py"

    def _on_sim_save_script(self) -> None:
        script_bundle = self._sim_cached_script_bundle
        if not str(script_bundle.get("master", "")).strip():
            try:
                master_script = self._generate_simulation_assets(show_progress=True)
                if not master_script:
                    return
                script_bundle = self._sim_cached_script_bundle
            except Exception as exc:
                self._append_sim_log(f"[error] Failed to generate script: {exc}")
                return

        default_path = str(self._simulation_script_path())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save EMERGE Python Script",
            default_path,
            "Python (*.py);;All Files (*)",
        )
        if not path:
            return
        master_path = Path(path)
        target_dir = master_path.parent
        master_path.write_text(str(script_bundle.get("master", "")), encoding="utf-8")
        self._append_sim_log(f"[info] Master script saved: {master_path}")
        for item in script_bundle.get("scripts", []):
            child_name = str(item.get("filename", "simulation_emerge_run.py"))
            child_path = target_dir / child_name
            child_path.write_text(str(item.get("content", "")), encoding="utf-8")
            self._append_sim_log(f"[info] Child script saved: {child_path}")

    def _on_sim_run(self) -> None:
        try:
            master_script = self._generate_simulation_assets(show_progress=True, force_script=False)
        except Exception as exc:
            self._append_sim_log(f"[error] Failed to prepare simulation assets: {exc}")
            return
        if not master_script:
            return

        bundle = self._sim_cached_script_bundle
        script_path = self._simulation_script_path()
        script_path.write_text(str(bundle.get("master", "")), encoding="utf-8")
        for item in bundle.get("scripts", []):
            child_name = str(item.get("filename", "simulation_emerge_run.py"))
            child_path = script_path.parent / child_name
            child_path.write_text(str(item.get("content", "")), encoding="utf-8")
        self._append_sim_log(f"[info] Running master script: {script_path}")

        proc = getattr(self, "_sim_process", None)
        if proc is not None and proc.state() != QProcess.NotRunning:
            self._append_sim_log("[warn] A simulation process is already running.")
            return

        self._sim_process = QProcess(self)
        self._sim_process.setProgram(sys.executable)
        self._sim_process.setArguments(["-u", str(script_path)])
        self._sim_process.setWorkingDirectory(str(script_path.parent))
        self._sim_process.setProcessChannelMode(QProcess.MergedChannels)
        self._sim_process.readyReadStandardOutput.connect(self._on_sim_process_output)
        self._sim_process.finished.connect(self._on_sim_finished)
        self._sim_process.start()

        self._sim_btn_run.setEnabled(False)
        self._sim_btn_stop.setEnabled(True)

    def _on_sim_stop(self) -> None:
        proc = getattr(self, "_sim_process", None)
        if proc is None or proc.state() == QProcess.NotRunning:
            return
        proc.kill()
        self._append_sim_log("[info] Simulation process terminated by user.")

    def _on_sim_process_output(self) -> None:
        proc = getattr(self, "_sim_process", None)
        if proc is None:
            return
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self._append_sim_log(data.rstrip("\n"))

    def _on_sim_finished(self, exit_code: int, _status) -> None:
        self._append_sim_log(f"[info] Simulation finished with exit code {exit_code}.")
        if hasattr(self, "_sim_btn_run"):
            self._sim_btn_run.setEnabled(True)
        if hasattr(self, "_sim_btn_stop"):
            self._sim_btn_stop.setEnabled(False)

    def _append_sim_log(self, message: str) -> None:
        if not hasattr(self, "_sim_log_view"):
            return
        if message:
            self._sim_log_view.appendPlainText(message)

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� sketch
    def _open_sketch(self) -> None:
        vp = self._viewport
        dlg = SketchDialog(
            self,
            plane_origin=vp._custom_plane_origin,
            plane_normal=vp._custom_plane_normal,
        )
        dlg.extrude_requested.connect(self._on_extrude_requested)
        dlg.revolve_requested.connect(self._on_revolve_requested)
        dlg.show()

    def _on_extrude_requested(self, profile, depth, origin, normal) -> None:
        from ..scene.em_objects import ExtrudedObject
        obj = ExtrudedObject(
            profile_pts  = profile,
            depth        = depth,
            plane_origin = origin,
            plane_normal = normal,
            material     = self._draw_material,
        )
        self._viewport.scene.add_object(obj)
        self._viewport.scene.select(obj)
        self._viewport.object_selected.emit(obj)
        self._viewport.selection_changed.emit([obj])
        self._viewport.scene_changed.emit()
        self._viewport._render()
        self._refresh_materials()
        self._info_bar.set_info(f"Extruded body created: {obj.name}")

    def _on_revolve_requested(self, profile, angle, axis_pt1, axis_pt2) -> None:
        from ..scene.em_objects import RevolvedObject
        obj = RevolvedObject(
            profile_pts = profile,
            angle       = angle,
            axis_pt1    = axis_pt1,
            axis_pt2    = axis_pt2,
            material    = self._draw_material,
        )
        self._viewport.scene.add_object(obj)
        self._viewport.scene.select(obj)
        self._viewport.object_selected.emit(obj)
        self._viewport.selection_changed.emit([obj])
        self._viewport.scene_changed.emit()
        self._viewport._render()
        self._refresh_materials()
        self._info_bar.set_info(f"Revolved body created: {obj.name}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� reference plane
    def _open_reference_plane_dialog(self) -> None:
        vp = self._viewport
        # Reuse a single dialog instance so it survives hide/show during 3D picking
        dlg = getattr(self, "_ref_plane_dlg", None)
        if dlg is None:
            dlg = ReferencePlaneDialog(
                self,
                current_origin=vp._custom_plane_origin,
                current_normal=vp._custom_plane_normal,
                viewport=vp,
            )
            dlg.plane_defined.connect(self._on_plane_defined)
            self._ref_plane_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_plane_defined(self, origin, normal, name) -> None:
        """Slot for ReferencePlaneDialog.plane_defined."""
        scene = self._viewport.scene
        plane = scene.add_reference_plane(
            name or f"Plane {len(scene.reference_planes)+1}",
            origin, normal, make_active=True,
        )
        self._viewport.set_reference_plane(origin, normal)
        self._viewport.set_grid(
            self._workspace_size,
            self._grid_spacing,
            "CUSTOM",
            self._units,
        )
        self._refresh_materials()
        self._info_bar.set_info(f"Reference plane '{plane.name}' added and made active.")

    def _on_plane_make_active(self, plane) -> None:
        scene = self._viewport.scene
        scene.set_active_plane(plane)
        self._sync_active_reference_plane_to_viewport()
        self._refresh_materials()
        self._info_bar.set_info(f"Active reference plane: {plane.name}")

    def _on_plane_view_normal(self, plane) -> None:
        """Look at the selected reference plane along its positive normal."""
        import math

        normal = tuple(float(value) for value in plane.normal)
        length = math.sqrt(sum(value * value for value in normal))
        if length < 1e-12:
            return
        normal = tuple(value / length for value in normal)
        origin = tuple(float(value) for value in plane.origin)

        camera = self._viewport._renderer.GetActiveCamera()
        current_position = camera.GetPosition()
        distance = math.sqrt(sum(
            (current_position[index] - origin[index]) ** 2
            for index in range(3)
        ))
        distance = max(distance, float(self._workspace_size), 1.0)
        camera.SetPosition(*(
            origin[index] + normal[index] * distance
            for index in range(3)
        ))
        camera.SetFocalPoint(*origin)

        # Prefer world Z as the vertical direction, unless it is parallel to
        # the view direction; then use world Y to keep the view upright.
        view_up = (0.0, 0.0, 1.0)
        if abs(sum(view_up[index] * normal[index] for index in range(3))) > 0.95:
            view_up = (0.0, 1.0, 0.0)
        camera.SetViewUp(*view_up)
        self._viewport._renderer.ResetCameraClippingRange()
        self._viewport._render()
        self._info_bar.set_info(f"View normal to plane: {plane.name}")

    def _on_plane_delete(self, plane) -> None:
        scene = self._viewport.scene
        # Don't allow deleting the last remaining plane
        if len(scene.reference_planes) <= 1:
            QMessageBox.warning(self, "Delete Plane",
                                "Cannot delete the last reference plane.")
            return
        was_active = (scene.active_plane is plane)
        scene.remove_reference_plane(plane)
        if was_active and scene.active_plane is not None:
            self._sync_active_reference_plane_to_viewport()
        self._refresh_materials()
        self._info_bar.set_info(f"Plane deleted: {plane.name}")

    def _on_plane_rename(self, plane, new_name: str) -> None:
        plane.name = new_name
        self._refresh_materials()

    def _on_plane_triad_visibility_changed(self, visible: bool) -> None:
        self._viewport.set_plane_triad_visible(bool(visible))
        self._refresh_materials()
        state = "shown" if visible else "hidden"
        self._info_bar.set_info(f"Reference-plane XYZ triad {state}.")

    def _apply_grid_visibility(self, visible: bool) -> None:
        self._viewport.set_grid_visible(bool(visible))
        self._act_grid_toggle.setChecked(bool(visible))
        self._act_grid_toggle.setText("Hide Grid" if visible else "Show Grid")
        self._refresh_materials()

    def _on_grid_visibility_changed(self, visible: bool) -> None:
        self._apply_grid_visibility(bool(visible))
        state = "shown" if visible else "hidden"
        self._info_bar.set_info(f"Reference-plane grid {state}.")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� grid / units / workspace
    @staticmethod
    def _axis_plane_from_normal(normal) -> str:
        """Map a plane normal to the closest axis-aligned drawing mode."""
        nx, ny, nz = abs(float(normal[0])), abs(float(normal[1])), abs(float(normal[2]))
        if nz >= nx and nz >= ny:
            return "XY"
        if ny >= nx and ny >= nz:
            return "XZ"
        return "YZ"

    def _active_draw_plane_name(self) -> str:
        scene = self._viewport.scene
        if scene.active_plane is None:
            return "XY"
        return self._axis_plane_from_normal(scene.active_plane.normal)

    def _sync_active_reference_plane_to_viewport(self) -> None:
        """Keep viewport drawing grid/reference aligned with the active tree plane."""
        scene = self._viewport.scene
        if scene.active_plane is None:
            return
        self._viewport.set_reference_plane(
            scene.active_plane.origin,
            scene.active_plane.normal,
        )
        self._viewport.set_grid(
            self._workspace_size,
            self._grid_spacing,
            self._active_draw_plane_name(),
            self._units,
        )

    def _on_grid_changed(self, value: float) -> None:
        self._grid_spacing = float(value)
        self._viewport.set_grid(self._workspace_size, self._grid_spacing, self._active_draw_plane_name(), self._units)
        self._sync_settings_dialog_values()

    def _on_workspace_changed(self, value: float) -> None:
        self._workspace_size = float(value)
        self._viewport.set_grid(self._workspace_size, self._grid_spacing, self._active_draw_plane_name(), self._units)
        self._sync_settings_dialog_values()
        self._info_bar.set_info(f"Workspace size: {self._format_locale_number(self._workspace_size)} {self._units}")

    def _parse_locale_number(self, text: str) -> float:
        val, ok = self._ui_locale.toDouble(str(text).strip())  # FIX: val is float, ok is bool!
        if ok:
            return float(val)
        normalized = str(text).strip().replace(" ", "").replace("\u00a0", "")
        if "," in normalized and "." in normalized:
            if normalized.rfind(",") > normalized.rfind("."):
                normalized = normalized.replace(".", "").replace(",", ".")
            else:
                normalized = normalized.replace(",", "")
        else:
            normalized = normalized.replace(",", ".")
        try:
            return float(normalized)
        except Exception as exc:
            raise ValueError("invalid locale number") from exc

    def _format_locale_number(self, value: float) -> str:
        return self._ui_locale.toString(float(value), 'g', 12)

    def _make_numeric_locale(self, decimal_separator: str) -> QLocale:
        if decimal_separator == ",":
            return QLocale(QLocale.Italian, QLocale.Italy)
        return QLocale.c()

    def _load_app_settings(self) -> None:
        settings = QSettings()

        units = str(settings.value("display/units", self._units))
        if units in _UNITS:
            self._units = units

        try:
            self._workspace_size = float(settings.value("display/workspace_size", self._workspace_size))
        except Exception:
            pass

        try:
            self._grid_spacing = float(settings.value("display/grid_size", self._grid_spacing))
        except Exception:
            pass

        try:
            self._plane_triad_size = float(settings.value("display/plane_triad_size", self._plane_triad_size))
        except Exception:
            pass

        decimal_separator = str(settings.value("display/decimal_separator", self._decimal_separator))
        if decimal_separator in {".", ","}:
            self._decimal_separator = decimal_separator

        color_text = str(settings.value("display/selection_color", ""))
        if color_text:
            parts = color_text.split(",")
            if len(parts) == 3:
                try:
                    self._selection_color = tuple(float(p) for p in parts)
                except Exception:
                    pass

        self._ui_locale = self._make_numeric_locale(self._decimal_separator)

        # Load simulation & mesh settings
        solver = str(settings.value("simulation/solver", self._sim_solver)).strip()
        if solver in ["PARDISO"]:
            self._sim_solver = solver

        try:
            self._sim_parallel_enabled = bool(int(settings.value("simulation/parallel_enabled", 1)))
        except Exception:
            pass

        try:
            self._sim_pardiso_threads = max(1, int(settings.value("simulation/pardiso_threads", 8)))
        except Exception:
            pass

        try:
            self._sim_acc_threads = max(1, int(settings.value("simulation/acc_threads", 10)))
        except Exception:
            pass

        try:
            self._mesh_resolution = max(0.01, min(1.0, float(settings.value("mesh/resolution", 0.3))))
        except Exception:
            pass

        try:
            self._sim_plot_sparams_after_sim = bool(int(settings.value("simulation/plot_sparams_after_sim", 1)))
        except Exception:
            pass

        try:
            self._sim_export_sparams_after_sim = bool(int(settings.value("simulation/export_sparams_after_sim", 1)))
        except Exception:
            pass

    def _save_app_settings(self) -> None:
        settings = QSettings()
        settings.setValue("display/units", self._units)
        settings.setValue("display/decimal_separator", self._decimal_separator)
        settings.setValue("display/workspace_size", self._workspace_size)
        settings.setValue("display/grid_size", self._grid_spacing)
        settings.setValue("display/plane_triad_size", self._plane_triad_size)
        settings.setValue(
            "display/selection_color",
            ",".join(str(float(v)) for v in self._selection_color),
        )

        # Save simulation & mesh settings
        settings.setValue("simulation/solver", self._sim_solver)
        settings.setValue("simulation/parallel_enabled", int(self._sim_parallel_enabled))
        settings.setValue("simulation/pardiso_threads", int(self._sim_pardiso_threads))
        settings.setValue("simulation/acc_threads", int(self._sim_acc_threads))
        settings.setValue("mesh/resolution", float(self._mesh_resolution))
        settings.setValue("simulation/plot_sparams_after_sim", int(self._sim_plot_sparams_after_sim))
        settings.setValue("simulation/export_sparams_after_sim", int(self._sim_export_sparams_after_sim))

    def _sync_settings_dialog_values(self) -> None:
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is None:
            return
        dlg.set_values(
            units=self._units,
            decimal_separator=self._decimal_separator,
            workspace_size=self._workspace_size,
            grid_size=self._grid_spacing,
            plane_triad_size=self._plane_triad_size,
            selection_color=self._selection_color,
            locale=self._ui_locale,
            solver=self._sim_solver,
            parallel_enabled=self._sim_parallel_enabled,
            pardiso_threads=self._sim_pardiso_threads,
            acc_threads=self._sim_acc_threads,
            mesh_resolution=self._mesh_resolution,
            plot_sparams_after_sim=self._sim_plot_sparams_after_sim,
            export_sparams_after_sim=self._sim_export_sparams_after_sim,
        )

    def _apply_display_settings(self, values: dict) -> None:
        units = str(values.get("units", self._units))
        decimal_separator = str(values.get("decimal_separator", self._decimal_separator))
        selection_color = tuple(values.get("selection_color", self._selection_color))

        if decimal_separator not in {".", ","}:
            decimal_separator = "."

        old_units = self._units
        old_locale = self._ui_locale

        self._units = units
        self._decimal_separator = decimal_separator
        self._ui_locale = self._make_numeric_locale(decimal_separator)
        QLocale.setDefault(self._ui_locale)
        set_numeric_locale(self._ui_locale)

        self._grid_spacing = float(values.get("grid_size", self._grid_spacing))
        self._workspace_size = float(values.get("workspace_size", self._workspace_size))
        self._plane_triad_size = max(1e-6, float(values.get("plane_triad_size", self._plane_triad_size)))

        # Apply simulation & mesh settings
        solver = str(values.get("solver", self._sim_solver)).strip()
        if solver in ["PARDISO"]:
            self._sim_solver = solver
        self._sim_parallel_enabled = bool(values.get("parallel_enabled", self._sim_parallel_enabled))
        self._sim_pardiso_threads = max(1, int(values.get("pardiso_threads", self._sim_pardiso_threads)))
        self._sim_acc_threads = max(1, int(values.get("acc_threads", self._sim_acc_threads)))
        self._mesh_resolution = max(0.01, min(1.0, float(values.get("mesh_resolution", self._mesh_resolution))))
        self._sim_plot_sparams_after_sim = bool(values.get("plot_sparams_after_sim", self._sim_plot_sparams_after_sim))
        self._sim_export_sparams_after_sim = bool(values.get("export_sparams_after_sim", self._sim_export_sparams_after_sim))

        if selection_color != self._selection_color:
            self._selection_color = selection_color
            set_selection_color(self._selection_color)
            for obj in self._viewport.scene.objects:
                obj.refresh_appearance()

        self._viewport.set_grid(self._workspace_size, self._grid_spacing, self._active_draw_plane_name(), self._units)
        self._viewport.set_plane_triad_size(self._plane_triad_size)
        self._sync_settings_dialog_values()
        self._viewport._render()
        self._save_app_settings()

        if old_locale != self._ui_locale:
            self._refresh_materials()

    def _open_settings_dialog(self) -> None:
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is None:
            dlg = SettingsDialog(self)
            self._settings_dlg = dlg
        self._sync_settings_dialog_values()
        if dlg.exec_() == dlg.Accepted:
            self._apply_display_settings(dlg.values())
            self._info_bar.set_info("Display settings updated.")

    def _on_units_changed(self, units: str) -> None:
        workspace_size = self._workspace_size
        grid_size = self._grid_spacing
        plane_triad_size = self._plane_triad_size
        if units != self._units and self._units in _MM_PER_UNIT and units in _MM_PER_UNIT:
            factor = _MM_PER_UNIT[self._units] / _MM_PER_UNIT[units]
            workspace_size *= factor
            grid_size *= factor
            plane_triad_size *= factor
        self._apply_display_settings(
            {
                "units": units,
                "decimal_separator": self._decimal_separator,
                "workspace_size": workspace_size,
                "grid_size": grid_size,
                "plane_triad_size": plane_triad_size,
                "selection_color": self._selection_color,
            }
        )
        self._info_bar.set_info(f"Units changed to {units}")

    def _on_selection_mode_changed(self, mode: str) -> None:
        """Switch viewport between Object / Face / Edge / Vertex picking."""
        m = mode.lower()
        # "All" in the combo means "whole object"
        if m == "all":
            m = "object"
        elif m == "surface":
            m = "face"
        self._viewport.set_selection_mode(m)
        self._info_bar.set_info(f"Selection mode: {mode}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� scene refresh
    def _refresh_materials(self) -> None:
        scene = self._viewport.scene
        by_mat = scene.by_material()
        settings = self._project_tree.get_settings()
        material_priorities = settings.get("material_priorities", {})
        if not isinstance(material_priorities, dict):
            material_priorities = {}
        self._materials.refresh(
            by_mat,
            planes=scene.reference_planes,
            active_plane=scene.active_plane,
            plane_triad_visible=self._viewport.is_plane_triad_visible(),
            grid_visible=self._viewport.is_grid_visible(),
            material_priorities=material_priorities,
        )

    def _on_settings_changed(self) -> None:
        self._sync_log_verbosity_from_settings()
        self._sync_port_reference_state()
        self._info_bar.set_info("EMERGE settings updated.")
        self._mark_simulation_dirty(steps=False, script=True)

    def _simdata_file_in_dir(self, results_dir: Path) -> Path | None:
        if not results_dir.exists() or not results_dir.is_dir():
            return None
        for name in ("simdata.emerge", "simdata.EMERGE"):
            p = results_dir / name
            if p.exists() and p.is_file():
                return p
        return None

    def _candidate_results_dirs_for_sim(self, simulation_name: str) -> list[Path]:
        bundle_dir = self._simulation_bundle_dir()
        safe_project = self._safe_script_token(self._project_name)
        safe_sim = self._safe_script_token(simulation_name)

        sim_index = None
        settings = self._project_tree.get_settings()
        sims = settings.get("simulations", []) if isinstance(settings, dict) else []
        if isinstance(sims, list):
            for idx, sim in enumerate(sims, start=1):
                if not isinstance(sim, dict):
                    continue
                name = str(sim.get("name", f"Simulation_{idx}")).strip() or f"Simulation_{idx}"
                if name == simulation_name:
                    sim_index = idx
                    break

        candidates: list[Path] = []
        if sim_index is not None:
            candidates.append(bundle_dir / f"{safe_project}_{sim_index:02d}_{safe_sim}.EMResults")
        candidates.append(bundle_dir / f"{safe_project}.EMResults")

        try:
            all_res = sorted(bundle_dir.glob("*.EMResults"), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            all_res = []

        sim_token = safe_sim.lower()
        for p in all_res:
            stem = p.stem.lower()
            if sim_token and sim_token in stem and p not in candidates:
                candidates.append(p)
        for p in all_res:
            if p not in candidates:
                candidates.append(p)

        # Existing results may have been generated before the project path was
        # corrected. Recover only matching EMERGE result folders in the
        # workspace so the plot command can still open the latest run.
        if not any(self._simdata_file_in_dir(path) for path in candidates):
            workspace_root = Path(__file__).resolve().parents[3]
            for simdata in workspace_root.rglob("simdata.emerge"):
                result_dir = simdata.parent
                if result_dir not in candidates and safe_project.lower() in result_dir.parent.name.lower():
                    candidates.append(result_dir)
        return candidates

    def _resolve_emerge_simulation_ctor(self):
        # EMERGE API differs across versions/distributions.
        # Some builds require importing emerge_iron before emerge symbols are exposed.
        import importlib
        import pkgutil

        for bootstrap in ("emerge_iron",):
            try:
                importlib.import_module(bootstrap)
            except Exception:
                pass

        self._import_installed_emerge()

        tried: list[str] = []

        def _try(mod_name: str, attr_name: str):
            tried.append(f"{mod_name}.{attr_name}")
            try:
                mod = importlib.import_module(mod_name)
                ctor = getattr(mod, attr_name, None)
                if callable(ctor):
                    return ctor
            except Exception:
                return None
            return None

        for mod_name, attr_name in (
            ("emerge", "Simulation"),
            ("emerge.core", "Simulation"),
            ("emerge.ext", "Simulation"),
            ("emerge._emerge.simmodel", "Simulation"),
            ("emerge", "SimulationBeta"),
        ):
            ctor = _try(mod_name, attr_name)
            if ctor is not None:
                return ctor

        # Dynamic fallback: search emerge submodules for a callable Simulation symbol.
        em = None
        em_file = "<unknown>"
        em_attrs = []
        try:
            em = importlib.import_module("emerge")
            em_file = str(getattr(em, "__file__", "<unknown>"))
            em_attrs = [a for a in dir(em) if "sim" in a.lower()][:20]
        except Exception:
            em = None

        if em is not None:
            # Check already imported child modules first.
            for attr_name in dir(em):
                try:
                    obj = getattr(em, attr_name, None)
                    ctor = getattr(obj, "Simulation", None)
                    if callable(ctor):
                        return ctor
                except Exception:
                    continue

            # Then scan package modules.
            em_path = getattr(em, "__path__", None)
            if em_path is not None:
                try:
                    for mod_info in pkgutil.walk_packages(em_path, em.__name__ + "."):
                        mod_name = mod_info.name
                        lname = mod_name.lower()
                        if not ("sim" in lname or "core" in lname or "ext" in lname):
                            continue
                        tried.append(f"{mod_name}.Simulation")
                        try:
                            mod = importlib.import_module(mod_name)
                            ctor = getattr(mod, "Simulation", None)
                            if callable(ctor):
                                return ctor
                        except Exception:
                            continue
                except Exception:
                    pass

        raise RuntimeError(
            "Unable to resolve EMERGE Simulation class. "
            f"Tried: {', '.join(tried)}. "
            f"emerge module: {em_file}; sim-like attrs: {em_attrs}"
        )

    @staticmethod
    def _import_installed_emerge():
        """Import site-packages EMERGE, not the local ``emerge`` helper package."""
        import importlib
        import sys
        from pathlib import Path

        local_emerge = Path(__file__).resolve().parents[1] / "emerge"
        local_root = str(local_emerge.parent).lower()
        current = sys.modules.get("emerge")
        current_file = str(getattr(current, "__file__", "")).lower()
        if current is not None and current_file.startswith(str(local_emerge).lower()):
            for name in list(sys.modules):
                if name == "emerge" or name.startswith("emerge."):
                    del sys.modules[name]

        old_path = list(sys.path)
        sys.path[:] = [
            entry for entry in sys.path
            if str(Path(entry or ".").resolve()).lower() != local_root
        ]
        try:
            module = importlib.import_module("emerge")
            module_file = str(getattr(module, "__file__", "")).lower()
            if module_file.startswith(str(local_emerge).lower()):
                raise RuntimeError(f"Local EMERGE helper package was imported: {module_file}")
            return module
        finally:
            sys.path[:] = old_path

    def _load_sim_grid_from_results(self, simulation_name: str):
        sim_ctor = self._resolve_emerge_simulation_ctor()

        candidates = self._candidate_results_dirs_for_sim(simulation_name)
        last_err = None
        for results_dir in candidates:
            simdata = self._simdata_file_in_dir(results_dir)
            if simdata is None:
                continue
            results_dir = results_dir.resolve()
            model_name = str(results_dir.parent / results_dir.stem)
            cwd_prev = os.getcwd()
            try:
                os.chdir(str(results_dir.parent))
                try:
                    sim = sim_ctor(model_name, load_file=True)
                except TypeError:
                    sim = sim_ctor(model_name)
                    load_fn = getattr(sim, "load", None)
                    if callable(load_fn):
                        try:
                            load_fn()
                        except TypeError:
                            load_fn(str(results_dir))
                mw_data = getattr(getattr(sim, "data", None), "mw", None)
                if mw_data is None:
                    raise RuntimeError("Loaded simulation has no microwave data.")
                scalar = getattr(mw_data, "scalar", None)
                grid = getattr(scalar, "grid", None)
                if grid is None:
                    raise RuntimeError("Loaded simulation has no scalar.grid data.")
                return sim, grid, simdata
            except Exception as exc:
                last_err = exc
            finally:
                os.chdir(cwd_prev)

        if last_err is not None:
            raise RuntimeError(f"Failed to load results for '{simulation_name}': {last_err}") from last_err
        raise RuntimeError(
            f"No simdata.emerge found for simulation '{simulation_name}' in {self._simulation_bundle_dir()}"
        )

    def _grid_port_numbers(self, grid) -> list[int]:
        port_map = getattr(grid, "_portmap", None)
        if isinstance(port_map, dict) and port_map:
            out: list[int] = []
            for k in port_map.keys():
                try:
                    out.append(int(k))
                except Exception:
                    pass
            if out:
                return sorted(set(out))
        try:
            smat = grid.Smat
            n_ports = int(smat.shape[1]) if len(getattr(smat, "shape", ())) >= 3 else 1
            return list(range(1, max(1, n_ports) + 1))
        except Exception:
            return [1]

    def _on_output_plot_requested(self, payload: dict) -> None:
        name = str(payload.get("name", "Output")).strip() or "Output"
        sim_name = str(payload.get("simulation", "")).strip()
        plot_type = str(payload.get("plot_type", "plot_sp")).strip() or "plot_sp"
        plot_params = payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {}
        if not sim_name:
            QMessageBox.warning(self, "Output", "Output has no simulation assigned.")
            return

        try:
            loaded_sim, grid, simdata_path = self._load_sim_grid_from_results(sim_name)
        except Exception as exc:
            QMessageBox.warning(self, "Output", str(exc))
            self._append_sim_log(f"[warn] Output '{name}' failed: {exc}")
            return

        try:
            import importlib
            import numpy as np
            self._import_installed_emerge()
            emerge_plot = importlib.import_module("emerge.plot")
            plot_sp = emerge_plot.plot_sp
            plot_vswr = emerge_plot.plot_vswr
            smith = emerge_plot.smith
            plot = emerge_plot.plot
            plot_ff = getattr(emerge_plot, "plot_ff", None)
            plot_ff_polar = getattr(emerge_plot, "plot_ff_polar", None)
        except Exception as exc:
            QMessageBox.warning(self, "Output", f"Unable to import emerge.plot: {exc}")
            return

        def _as_2d_curve(array):
            arr = np.asarray(array)
            if arr.ndim == 0:
                return arr.reshape(1)
            return arr.reshape(-1)

        if plot_type in {"plot_ff", "plot_ff_polar", "plot_ff_3d"}:
            plane = str(plot_params.get("plane", "XY")).strip().upper() or "XY"
            polar_3d = bool(plot_params.get("polar_3d", False))
            theta = getattr(grid, "theta", None)
            phi = getattr(grid, "phi", None)
            ff_data = getattr(grid, "ff", None)
            if ff_data is None and hasattr(grid, "E"):
                ff_data = getattr(grid, "E")
            if ff_data is None and hasattr(grid, "farfield"):
                ff_data = getattr(grid, "farfield")
            if ff_data is None:
                mw_data = getattr(getattr(loaded_sim, "data", None), "mw", None)
                field_data = getattr(mw_data, "field", None)
                frequencies = np.asarray(getattr(grid, "freq", []))
                if field_data is not None and frequencies.size:
                    requested_frequency_ghz = float(plot_params.get("frequency_GHz", 0.0) or 0.0)
                    requested_frequency = requested_frequency_ghz * 1e9
                    if requested_frequency <= 0.0:
                        requested_frequency = (float(frequencies[0]) + float(frequencies[-1])) / 2.0
                    selected_frequency = float(
                        frequencies[int(np.argmin(np.abs(frequencies - requested_frequency)))]
                    )
                    field_entry = field_data.find(freq=selected_frequency)
                    boundary_geometries = [
                        geometry
                        for geometry in loaded_sim.all_geos()
                        if callable(getattr(geometry, "boundary", None))
                    ]
                    if not boundary_geometries:
                        raise RuntimeError(
                            "Far-field plotting requires at least one geometry with boundary faces."
                        )
                    faces = boundary_geometries[0].boundary()
                    for geometry in boundary_geometries[1:]:
                        faces = faces + geometry.boundary()
                    if plot_type == "plot_ff_3d":
                        farfield = field_entry.farfield_3d(faces)
                        display = loaded_sim.display
                        model_display_state = {
                            str(getattr(obj, "name", "")).strip(): (
                                bool(obj.is_visible()),
                                max(0.0, min(1.0, float(getattr(obj, "opacity", 0.85)))),
                            )
                            for obj in self._viewport.scene.objects
                            if bool(getattr(obj, "is_model", True))
                        }
                        for geometry in loaded_sim.all_geos():
                            geometry_name = str(getattr(geometry, "name", "")).strip()
                            source_name = geometry_name.split("_OCC", 1)[0].strip()
                            display_state = model_display_state.get(source_name)
                            if display_state is None:
                                display_state = model_display_state.get(geometry_name)
                            if display_state is not None:
                                _visible, source_opacity = display_state
                                display.add_object(
                                    geometry,
                                    opacity=source_opacity if _visible else 0.0,
                                )
                        mesh_nodes = np.asarray(getattr(loaded_sim.mesh, "nodes", []))
                        rmax = None
                        if mesh_nodes.ndim == 2 and mesh_nodes.shape[0] == 3 and mesh_nodes.shape[1]:
                            rmax = max(float(np.ptp(mesh_nodes, axis=1).max()) * 2.0, 1e-6)
                        display.add_farfield3d(
                            farfield,
                            component="normE",
                            quantity="abs",
                            dB=True,
                            dBfloor=-40,
                            rmax=rmax,
                            opacity=0.75,
                        )
                        display.show()
                        self._info_bar.set_info(f"Output plotted: {name} ({plot_type}) from {simdata_path}")
                        self._append_sim_log(f"[info] Output plotted: {name} ({plot_type}) from {simdata_path}")
                        return
                    else:
                        plane_axes = {
                            "XY": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
                            "XZ": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
                            "YZ": ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
                        }
                        ref_direction, plane_normal = plane_axes.get(plane, plane_axes["XY"])
                        farfield = field_entry.farfield_2d(ref_direction, plane_normal, faces)
                        theta = farfield.ang
                    ff_data = farfield.gain.norm
            if ff_data is None:
                raise RuntimeError(
                    "This result does not contain saved E/H fields for far-field plotting. "
                    "Enable a far-field output and rerun the simulation."
                )

            value = ff_data
            if isinstance(ff_data, dict):
                for key in ("E", "field", "value", "magnitude", "Et", "Etheta", "Ephi"):
                    if key in ff_data:
                        value = ff_data[key]
                        break
                if isinstance(value, dict):
                    for key in ("theta", "phi", "ang", "angles"):
                        if key in value:
                            theta = value[key]
                            break

            theta_arr = np.asarray(theta) if theta is not None else None
            phi_arr = np.asarray(phi) if phi is not None else None
            values_arr = np.asarray(value)
            if values_arr.ndim > 1 and theta_arr is not None and values_arr.size == theta_arr.size:
                values_arr = values_arr.reshape(theta_arr.shape)
            if theta_arr is None and phi_arr is None and values_arr.ndim >= 1:
                theta_arr = np.linspace(0.0, 2.0 * np.pi, values_arr.size)

            if plot_type in {"plot_ff", "plot_ff_polar"}:
                if theta_arr is None or values_arr.size == 0:
                    raise RuntimeError("Far-field result does not expose angle and magnitude data for a polar plot.")
                if plot_type == "plot_ff":
                    if plot_ff is None:
                        raise RuntimeError("EMERGE does not expose plot_ff in this installation.")
                    plot_ff(theta_arr, values_arr, dB=True, labels=[name], xlabel="Theta (rad)", ylabel="Magnitude (dB)", title=f"{name} - {plane} plane")
                else:
                    if plot_ff_polar is None:
                        raise RuntimeError("EMERGE does not expose plot_ff_polar in this installation.")
                    plot_ff_polar(theta_arr, values_arr, dB=True, dBfloor=-80, labels=[name], title=f"{name} - {plane} plane", zero_location="N", clockwise=False)
            self._info_bar.set_info(f"Output plotted: {name} ({plot_type}) from {simdata_path}")
            self._append_sim_log(f"[info] Output plotted: {name} ({plot_type}) from {simdata_path}")
            return

        freq = getattr(grid, "freq", None)
        if freq is None:
            QMessageBox.warning(self, "Output", "Loaded simdata has no frequency axis.")
            return
        ports = self._grid_port_numbers(grid)
        import re
        s_parameter = str(plot_params.get("s_parameter", "S11")).strip().upper()
        match = re.fullmatch(r"S(\d+)[,:/_-]?(\d+)", s_parameter)
        if match:
            output_port = int(match.group(1))
            input_port = int(match.group(2))
        else:
            output_port = max(1, int(plot_params.get("port_i", 1)))
            input_port = max(1, int(plot_params.get("port_j", 1)))
            s_parameter = f"S{output_port}{input_port}"
        if output_port not in ports or input_port not in ports:
            QMessageBox.warning(
                self,
                "Output",
                f"S-parameter {s_parameter} is not available for this simulation ({len(ports)} port(s)).",
            )
            return
        selected_curve = grid.S(output_port, input_port)

        try:
            if plot_type == "plot_sp":
                curves = [selected_curve]
                labels = [s_parameter]
                plot_sp(freq, curves, labels=labels)
            elif plot_type == "plot_vswr":
                curves = [selected_curve]
                labels = [f"VSWR{output_port}{input_port}"]
                plot_vswr(freq, curves, labels=labels)
            elif plot_type == "smith":
                smith([selected_curve], f=freq, labels=[s_parameter])
            elif plot_type == "plot":
                curves = [20.0 * np.log10(np.maximum(np.abs(selected_curve), 1e-12))]
                labels = [f"|{s_parameter}| dB"]
                plot(freq, curves, labels=labels, xlabel="Frequency (Hz)", ylabel="Magnitude (dB)")
            else:
                QMessageBox.warning(self, "Output", f"Unsupported plot type: {plot_type}")
                return
        except Exception as exc:
            QMessageBox.warning(self, "Output", f"Failed to generate plot '{name}': {exc}")
            self._append_sim_log(f"[warn] Output '{name}' plot error: {exc}")
            return

        self._info_bar.set_info(f"Output plotted: {name} ({plot_type}) from {simdata_path}")
        self._append_sim_log(f"[info] Output plotted: {name} ({plot_type}) from {simdata_path}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� camera views
    def _set_view(self, view: str) -> None:
        cam = self._viewport._renderer.GetActiveCamera()
        if view == "top":
            cam.SetPosition(0, 0, 300)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 1, 0)
        elif view == "bottom":
            cam.SetPosition(0, 0, -300)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 1, 0)
        elif view == "front":
            cam.SetPosition(0, -300, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "back":
            cam.SetPosition(0, 300, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "right":
            cam.SetPosition(300, 0, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        elif view == "left":
            cam.SetPosition(-300, 0, 0)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        else:  # iso
            cam.SetPosition(150, -200, 150)
            cam.SetFocalPoint(0, 0, 0)
            cam.SetViewUp(0, 0, 1)
        self._viewport._renderer.ResetCameraClippingRange()
        self._viewport._render()

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� project file
    def _new_project(self) -> None:
        self._project_name = "Untitled"
        self._project_path = None
        self._material_store = MaterialStore()
        self._reset_simulation_cache()
        self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
        self._project_tree.set_project_name(self._project_name)
        self._project_tree.reset_settings()
        self._sync_log_verbosity_from_settings()
        self._viewport.scene.clear()
        self._sync_active_reference_plane_to_viewport()
        self._body_props.set_object(None)
        self._sync_material_choices()
        self._sync_port_reference_state()
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
            loaded_name = str(data.get("project_name", "")).strip()
            self._project_name = loaded_name if loaded_name and loaded_name != "Untitled" else Path(path).stem
            self._project_path = str(Path(path).expanduser().resolve())
            self._reset_simulation_cache()
            self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
            self._project_tree.set_project_name(self._project_name)
            self._project_tree.load_settings(data.get("emerge_settings", {}))
            self._sync_log_verbosity_from_settings()
            self._viewport.scene.from_json(data.get("objects", []))
            self._viewport.scene.reference_planes_from_json(
                data.get("reference_planes", []),
                data.get("active_plane_name"),
            )
            self._material_store.load_project_materials(data.get("project_materials", []))
            self._material_store.set_global_db_path(
                data.get("global_material_db_path"),
                create_if_missing=False,
            )
            self._sync_material_choices()
            grid = data.get("grid", {})
            self._units = data.get("units", "mm")
            self._workspace_size = float(grid.get("size", 200.0))
            self._grid_spacing = float(grid.get("spacing", 10.0))

            ds = data.get("display_settings", {})
            if ds:
                self._apply_display_settings({
                    "units": ds.get("units", self._units),
                    "decimal_separator": ds.get("decimal_separator", self._decimal_separator),
                    "workspace_size": float(ds.get("workspace_size", self._workspace_size)),
                    "grid_size": float(ds.get("grid_size", self._grid_spacing)),
                    "plane_triad_size": float(ds.get("plane_triad_size", self._plane_triad_size)),
                    "selection_color": tuple(ds.get("selection_color", list(self._selection_color))),
                })

            self._sync_settings_dialog_values()

            loaded_plane = str(grid.get("plane", "")).upper().strip()
            if loaded_plane not in {"XY", "XZ", "YZ"}:
                loaded_plane = self._active_draw_plane_name()

            self._viewport.set_grid(
                self._workspace_size,
                self._grid_spacing,
                loaded_plane,
                self._units,
            )
            self._sync_active_reference_plane_to_viewport()
            default_plane = self._viewport.scene.active_plane.name if self._viewport.scene.active_plane is not None else loaded_plane
            plane_name = str(default_plane).split()[0].upper() if default_plane else loaded_plane
            if plane_name not in {"XY", "XZ", "YZ"}:
                plane_name = loaded_plane
            normal_map = {"XY": (0.0, 0.0, 1.0), "XZ": (0.0, 1.0, 0.0), "YZ": (1.0, 0.0, 0.0)}
            for obj in self._viewport.scene.objects:
                if obj.creation_plane == "XY" and obj.creation_plane_normal == (0.0, 0.0, 1.0):
                    obj.set_creation_plane(plane_name, (0.0, 0.0, 0.0), normal_map.get(plane_name, (0.0, 0.0, 1.0)))
            self._viewport.set_camera_state(data.get("camera", {}))
            self._body_props.set_object(None)
            self._sync_port_reference_state()
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
            self._project_path = str(Path(path).expanduser().resolve())
            self._project_name = Path(path).stem
            self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
            self._project_tree.set_project_name(self._project_name)
            self._do_save(path)

    def _do_save(self, path: str) -> None:
        try:
            self._project_name = Path(path).stem
            ProjectFile.save(
                path,
                project_name  = self._project_name,
                settings      = self._project_tree.get_settings(),
                objects_json  = self._viewport.scene.to_json(),
                units         = self._units,
                grid_size     = self._viewport.scene._grid_size,
                grid_spacing  = self._viewport.scene._grid_spacing,
                grid_plane    = self._viewport.scene._grid_plane,
                reference_planes = self._viewport.scene.reference_planes_to_json(),
                active_plane_name = (
                    self._viewport.scene.active_plane.name
                    if self._viewport.scene.active_plane is not None else None
                ),
                project_materials = self._material_store.project_materials_to_json(),
                global_material_db_path = self._material_store.global_db_path,
                camera = self._viewport.get_camera_state(),
                display_settings = {
                    "units": self._units,
                    "decimal_separator": self._decimal_separator,
                    "workspace_size": self._workspace_size,
                    "grid_size": self._grid_spacing,
                    "plane_triad_size": self._plane_triad_size,
                    "selection_color": list(self._selection_color),
                },
            )
            self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
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
                objects      = self._simulation_model_objects(),
                units        = self._units,
                materials_catalog = self._material_store.material_export_catalog(),
            )
            Path(path).write_text(script, encoding="utf-8")
            self._info_bar.set_info(f"EMERGE script exported: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _set_global_material_db(self) -> None:
        suggested = self._material_store.global_db_path or str(Path.home() / "em3d_materials_global.json")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Global Material Database",
            suggested,
            "JSON (*.json);;All Files (*)",
        )
        if not path:
            return
        self._material_store.set_global_db_path(path, create_if_missing=True)
        self._material_store.save_global_db()
        self._sync_material_choices()
        self._info_bar.set_info(f"Global material DB: {path}")

    def _reload_global_material_db(self) -> None:
        self._material_store.set_global_db_path(self._material_store.global_db_path, create_if_missing=False)
        self._sync_material_choices()
        if self._material_store.global_db_path:
            self._info_bar.set_info(f"Global material DB reloaded: {self._material_store.global_db_path}")
        else:
            self._info_bar.set_info("No global material DB configured")

    def _open_material_library_dialog(self) -> None:
        dlg = MaterialLibraryDialog(self, self._material_store, self._sync_material_choices)
        dlg.exec_()
        self._sync_material_choices()
        if dlg.changed:
            self._info_bar.set_info("Material library updated")

    def _sync_material_choices(self) -> None:
        names = self._material_store.all_project_names()
        self._body_props.set_materials(names)
        if self._draw_material not in names and names:
            self._draw_material = names[0]

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� close
    def closeEvent(self, event) -> None:
        reply = QMessageBox.question(
            self, "Quit", "Save project before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )
        if reply == QMessageBox.Save:
            self._save_project()
            self._save_app_settings()
            event.accept()
        elif reply == QMessageBox.Discard:
            self._save_app_settings()
            event.accept()
        else:
            event.ignore()
