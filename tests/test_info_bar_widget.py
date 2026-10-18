import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QVBoxLayout, QWidget

from em3d_modeler.ui.info_bar_widget import InfoBarWidget


def test_long_status_message_does_not_force_workspace_wider_than_screen():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    splitter = QSplitter(Qt.Horizontal)
    left_panel = QWidget()
    left_panel.setMinimumWidth(210)
    center_panel = QWidget()
    center_layout = QVBoxLayout(center_panel)
    workspace = QWidget()
    workspace.setMinimumWidth(480)
    info_bar = InfoBarWidget()
    center_layout.addWidget(workspace, 1)
    center_layout.addWidget(info_bar)
    right_panel = QWidget()
    right_panel.setMinimumWidth(190)
    splitter.addWidget(left_panel)
    splitter.addWidget(center_panel)
    splitter.addWidget(right_panel)
    splitter.setCollapsible(2, False)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([230, 900, 210])
    window.setCentralWidget(splitter)
    window.resize(1280, 720)
    window.show()
    app.processEvents()

    message = "IPC2581 import warning: " + "detail " * 100
    info_bar.set_error(message)
    app.processEvents()

    assert window.minimumSizeHint().width() < 1280
    assert right_panel.isVisible()
    assert right_panel.width() >= 190
    assert info_bar._msg.toolTip() == message

    window.close()
