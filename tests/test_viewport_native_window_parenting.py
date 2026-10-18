import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMdiArea,
    QMdiSubWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtTest import QTest

from em3d_modeler.ui.main_window import _PlotMdiArea, _PlotSubWindow
from em3d_modeler.ui.viewport_widget import _PersistentQVTKRenderWindowInteractor


class _NonRenderingInteractor(_PersistentQVTKRenderWindowInteractor):
    def paintEvent(self, _event):
        pass

    def Finalize(self):
        pass


def test_vtk_interactor_does_not_make_workspace_splitter_native_or_hide_right_panel():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    splitter = QSplitter(Qt.Horizontal)
    left_panel = QWidget()
    left_panel.setMinimumWidth(210)
    center_panel = QWidget()
    center_layout = QVBoxLayout(center_panel)
    workspace = QMdiArea(center_panel)
    center_layout.addWidget(workspace)
    model_page = QWidget()
    model_page_layout = QVBoxLayout(model_page)
    model_window = QMdiSubWindow(workspace)
    model_window.setWidget(model_page)
    workspace.addSubWindow(model_window)
    for native_host in (workspace, model_window):
        native_host.setAttribute(Qt.WA_DontCreateNativeAncestors)
        native_host.setAttribute(Qt.WA_NativeWindow)
        native_host.winId()
    right_panel = QWidget()
    right_panel.setMinimumWidth(190)

    splitter.addWidget(left_panel)
    splitter.addWidget(center_panel)
    splitter.addWidget(right_panel)
    splitter.setCollapsible(2, False)
    splitter.setSizes([230, 900, 210])
    window.setCentralWidget(splitter)
    window.resize(1280, 720)

    viewport_host = QWidget(model_page)
    viewport_layout = QVBoxLayout(viewport_host)
    interactor = _NonRenderingInteractor(viewport_host)
    viewport_layout.addWidget(interactor)
    model_page_layout.addWidget(viewport_host)
    window.show()
    model_window.show()
    app.processEvents()

    assert viewport_host.testAttribute(Qt.WA_NativeWindow)
    assert not splitter.testAttribute(Qt.WA_NativeWindow)
    assert workspace.testAttribute(Qt.WA_NativeWindow)
    assert model_window.testAttribute(Qt.WA_NativeWindow)
    assert model_page.parentWidget() is model_window
    assert right_panel.isVisible()
    assert right_panel.width() >= 190

    before_move = interactor.mapToGlobal(interactor.rect().topLeft())
    model_window.move(180, 140)
    app.processEvents()
    after_move = interactor.mapToGlobal(interactor.rect().topLeft())
    assert after_move != before_move
    assert interactor._render_paused
    interactor._render_resume_timer.stop()
    interactor._resume_rendering()
    assert not interactor._render_paused

    window.close()


def test_all_mdi_subwindows_pause_content_updates_until_geometry_settles():
    app = QApplication.instance() or QApplication([])
    host = QMainWindow()
    workspace = _PlotMdiArea(host)
    subwindow = _PlotSubWindow(workspace)
    content = QWidget()
    subwindow.setWidget(content)
    workspace.addSubWindow(subwindow)
    host.setCentralWidget(workspace)
    host.resize(800, 600)
    host.show()
    subwindow.show()
    app.processEvents()

    subwindow._geometry_resume_timer.stop()
    subwindow._resume_content_updates()
    assert content.updatesEnabled()

    subwindow.move(80, 60)
    app.processEvents()
    assert not content.updatesEnabled()
    QTest.qWait(150)
    assert content.updatesEnabled()

    subwindow.resize(420, 320)
    app.processEvents()
    assert not content.updatesEnabled()
    QTest.qWait(150)
    assert content.updatesEnabled()

    host.close()
