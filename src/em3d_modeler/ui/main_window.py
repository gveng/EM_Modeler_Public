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
    QMainWindow, QWidget, QSplitter, QAction,
    QFileDialog, QMessageBox, QComboBox,
    QLabel, QDoubleSpinBox, QDialog,
    QVBoxLayout, QTextBrowser,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui  import QIcon, QKeySequence

# Undo/Redo CommandStack


from .viewport_widget        import Viewport3DWidget
from .project_tree_widget    import ProjectTreeWidget
from .body_properties_widget import BodyPropertiesWidget
from .materials_widget       import MaterialsWidget
from .info_bar_widget        import InfoBarWidget
from .reference_plane_dialog import ReferencePlaneDialog
from .sketch_widget          import SketchDialog
from .material_assign_dialog import MaterialAssignDialog

from ..emerge.project_file    import ProjectFile
from ..emerge.script_exporter import export_emerge_script
from ..emerge.step_importer   import import_step
from ..emerge.material_store  import MaterialStore
from .. import __version__, __release_date__


# ������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������������
_UNITS  = ["mm", "um", "cm", "m", "mil", "inch"]
_DOCS_HELP = Path(__file__).parent.parent.parent.parent / "docs" / "HELP.md"
_DOCS_README = Path(__file__).parent.parent.parent.parent / "README.md"


class MainWindow(QMainWindow):
    """EM 3D Modeler - main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("EM 3D Modeler - EMERGE Design Environment")
        self.resize(1400, 860)

        self._project_name  = "Untitled"
        self._project_path  = None
        self._units         = "mm"
        self._draw_material = "PEC"
        self._material_store = MaterialStore()




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
        self._materials      = MaterialsWidget()
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
        centre_layout = __import__("PyQt5.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout(centre_widget)
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
        view_menu.addAction("Top (XY)",           lambda: self._set_view("top"))
        view_menu.addAction("Front (XZ)",         lambda: self._set_view("front"))
        view_menu.addAction("Right (YZ)",         lambda: self._set_view("right"))
        view_menu.addAction("Isometric",          lambda: self._set_view("iso"))
        view_menu.addSeparator()
        view_menu.addAction("Set Reference Plane...", self._open_reference_plane_dialog)
        view_menu.addAction("Reset Reference Plane", self._viewport.reset_reference_plane)

        # Help
        help_menu = mb.addMenu("&Help")
        help_menu.addAction("&Help", self._open_help)
        help_menu.addSeparator()
        help_menu.addAction("&About", self._show_about)

    def _open_help(self) -> None:
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
            f"Version: {__version__}\n"
            f"Release Date: {__release_date__}\n"
            f"Date: {today}",
        )

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� toolbar
    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(__import__("PyQt5.QtCore", fromlist=["QSize"]).QSize(22, 22))

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

        tb.addSeparator()

        # ������ STEP import ������������������������������������������������������������������������������������������������������������������������������������������
        act_step = QAction(_icon("Part_STEP"), "Import STEP", self)
        act_step.setToolTip("Import a STEP file (.step / .stp)")
        act_step.triggered.connect(self._import_step)
        tb.addAction(act_step)

        tb.addSeparator()

        # ������ Grid spacing ���������������������������������������������������������������������������������������������������������������������������������
        tb.addWidget(QLabel(" Grid: "))
        self._grid_spacing_spin = QDoubleSpinBox()
        self._grid_spacing_spin.setDecimals(3)
        self._grid_spacing_spin.setRange(0.001, 10000)
        self._grid_spacing_spin.setValue(10.0)
        self._grid_spacing_spin.setSuffix(" mm")
        self._grid_spacing_spin.setToolTip("Grid snap spacing (redraws grid)")
        self._grid_spacing_spin.valueChanged.connect(self._on_grid_changed)
        tb.addWidget(self._grid_spacing_spin)

        # ������ Workspace size ���������������������������������������������������������������������������������������������������������������������������
        tb.addWidget(QLabel(" Workspace: "))
        self._workspace_spin = QDoubleSpinBox()
        self._workspace_spin.setDecimals(1)
        self._workspace_spin.setRange(1.0, 100000.0)
        self._workspace_spin.setValue(200.0)
        self._workspace_spin.setSuffix(" mm")
        self._workspace_spin.setToolTip("Workspace extent (grid total size)")
        self._workspace_spin.valueChanged.connect(self._on_workspace_changed)
        tb.addWidget(self._workspace_spin)

        tb.addWidget(QLabel(" Units: "))
        self._units_combo = QComboBox()
        self._units_combo.addItems(_UNITS)
        self._units_combo.currentTextChanged.connect(self._on_units_changed)
        tb.addWidget(self._units_combo)

        tb.addSeparator()

        # ������ Selection mode ���������������������������������������������������������������������������������������������������������������������������
        tb.addWidget(QLabel(" Select: "))
        self._sel_mode_combo = QComboBox()
        self._sel_mode_combo.addItems(["All", "Face", "Edge", "Vertex"])
        self._sel_mode_combo.setToolTip(
            "Selection mode:\n"
            "  All    - pick whole bodies\n"
            "  Face   - pick a single face\n"
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
        self._viewport.scene_changed.connect(self._refresh_materials)
        self._viewport.status_message.connect(self._info_bar.set_info)

        # Body props ��� viewport render
        self._body_props.params_changed.connect(self._on_params_changed)
        self._body_props.bulk_material_changed.connect(self._on_bulk_material)
        self._body_props.bulk_style_changed.connect(self._on_bulk_style)
        self._body_props.material_added.connect(self._on_material_added)
        self._body_props.material_picker_requested.connect(self._open_material_picker)

        # Materials tree ��� selection (single + multi)
        self._materials.object_selected.connect(self._on_material_tree_select)
        self._materials.selection_changed.connect(self._on_material_tree_multi_select)
        self._materials.plane_make_active.connect(self._on_plane_make_active)
        self._materials.plane_delete.connect(self._on_plane_delete)
        self._materials.plane_rename.connect(self._on_plane_rename)
        self._materials.plane_add_requested.connect(self._open_reference_plane_dialog)
        self._materials.objects_hide.connect(self._on_materials_hide)
        self._materials.objects_show.connect(self._on_materials_show)
        self._materials.object_rename.connect(self._on_materials_rename)
        self._materials.assign_port_requested.connect(self._on_assign_port_requested)
        self._materials.assign_boundary_requested.connect(self._on_assign_boundary_requested)

        # EMERGE settings changed
        self._project_tree.settings_changed.connect(self._on_settings_changed)

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
            self._viewport._render()
            self._info_bar.set_info(f"Deleted: {obj.name}")

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
        )
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

    def _on_bulk_material(self, material: str, objects: list) -> None:
        self._draw_material = material
        self._viewport._render()
        self._refresh_materials()
        self._materials.highlight(objects)  # Now includes signal emit
        self._info_bar.set_info(f"Material '{material}' applied to {len(objects)} objects")

    def _on_bulk_style(self, material: str, color_hex: str, objects: list) -> None:
        self._draw_material = material
        self._viewport._render()
        self._refresh_materials()
        self._materials.highlight(objects)  # Now includes signal emit
        self._info_bar.set_info(
            f"Applied material '{material}' and color {color_hex} to {len(objects)} objects"
        )

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
            self._workspace_spin.value(),
            self._grid_spacing_spin.value(),
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
            self._workspace_spin.value(),
            self._grid_spacing_spin.value(),
            self._active_draw_plane_name(),
            self._units,
        )

    def _on_grid_changed(self, value: float) -> None:
        size  = self._workspace_spin.value()
        self._viewport.set_grid(size, value, self._active_draw_plane_name(), self._units)

    def _on_workspace_changed(self, value: float) -> None:
        spacing = self._grid_spacing_spin.value()
        self._viewport.set_grid(value, spacing, self._active_draw_plane_name(), self._units)
        self._info_bar.set_info(f"Workspace size: {value} {self._units}")

    def _on_units_changed(self, units: str) -> None:
        self._units = units
        self._grid_spacing_spin.setSuffix(f" {units}")
        self._workspace_spin.setSuffix(f" {units}")
        self._on_grid_changed(self._grid_spacing_spin.value())

    def _on_selection_mode_changed(self, mode: str) -> None:
        """Switch viewport between Object / Face / Edge / Vertex picking."""
        m = mode.lower()
        # "All" in the combo means "whole object"
        if m == "all":
            m = "object"
        self._viewport.set_selection_mode(m)
        self._info_bar.set_info(f"Selection mode: {mode}")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� scene refresh
    def _refresh_materials(self) -> None:
        scene = self._viewport.scene
        by_mat = scene.by_material()
        self._materials.refresh(
            by_mat,
            planes=scene.reference_planes,
            active_plane=scene.active_plane,
        )

    def _on_settings_changed(self) -> None:
        self._info_bar.set_info("EMERGE settings updated.")

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� camera views
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
        self._viewport._renderer.ResetCameraClippingRange()
        self._viewport._render()

    # ��������������������������������������������������������������������������������������������������������������������������������������������������������� project file
    def _new_project(self) -> None:
        self._project_name = "Untitled"
        self._project_path = None
        self._material_store = MaterialStore()
        self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
        self._project_tree.set_project_name(self._project_name)
        self._viewport.scene.clear()
        self._sync_active_reference_plane_to_viewport()
        self._body_props.set_object(None)
        self._sync_material_choices()
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
            self.setWindowTitle(f"EM 3D Modeler - {self._project_name}")
            self._project_tree.set_project_name(self._project_name)
            self._project_tree.load_settings(data.get("emerge_settings", {}))
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
            self._units_combo.setCurrentText(self._units)
            self._grid_spacing_spin.setSuffix(f" {self._units}")
            self._workspace_spin.setSuffix(f" {self._units}")

            self._workspace_spin.blockSignals(True)
            self._grid_spacing_spin.blockSignals(True)
            self._workspace_spin.setValue(float(grid.get("size", 200.0)))
            self._grid_spacing_spin.setValue(float(grid.get("spacing", 10.0)))
            self._workspace_spin.blockSignals(False)
            self._grid_spacing_spin.blockSignals(False)

            loaded_plane = str(grid.get("plane", "")).upper().strip()
            if loaded_plane not in {"XY", "XZ", "YZ"}:
                loaded_plane = self._active_draw_plane_name()

            self._viewport.set_grid(
                self._workspace_spin.value(),
                self._grid_spacing_spin.value(),
                loaded_plane,
                self._units,
            )
            self._sync_active_reference_plane_to_viewport()
            self._viewport.set_camera_state(data.get("camera", {}))
            self._body_props.set_object(None)
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
                reference_planes = self._viewport.scene.reference_planes_to_json(),
                active_plane_name = (
                    self._viewport.scene.active_plane.name
                    if self._viewport.scene.active_plane is not None else None
                ),
                project_materials = self._material_store.project_materials_to_json(),
                global_material_db_path = self._material_store.global_db_path,
                camera = self._viewport.get_camera_state(),
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
                objects      = self._viewport.scene.objects,
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
            event.accept()
        elif reply == QMessageBox.Discard:
            event.accept()
        else:
            event.ignore()
