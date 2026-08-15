"""Info / error bar at the bottom of the viewport column."""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QStatusBar
from PySide6.QtCore    import Qt
from PySide6.QtGui     import QColor, QPalette


class InfoBarWidget(QWidget):
    """Thin status bar that shows messages and errors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)

        self._icon  = QLabel("ℹ")
        self._icon.setFixedWidth(18)
        self._msg   = QLabel("Ready.")
        self._msg.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._coords = QLabel("")
        self._coords.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self._coords.setFixedWidth(220)

        layout.addWidget(self._icon)
        layout.addWidget(self._msg, stretch=1)
        layout.addWidget(self._coords)

        self.setStyleSheet(
            "background:#1e1e2e; color:#cdd6f4; font-size:11px; border-top:1px solid #45475a;"
        )

    # ─────────────────────────────────────────────────── public API
    def set_info(self, msg: str) -> None:
        self._icon.setText("ℹ")
        self._icon.setStyleSheet("color:#89b4fa;")
        self._msg.setText(msg)

    def set_error(self, msg: str) -> None:
        self._icon.setText("✖")
        self._icon.setStyleSheet("color:#f38ba8;")
        self._msg.setText(msg)

    def set_coords(self, x: float, y: float, z: float, units: str = "mm") -> None:
        self._coords.setText(
            f"X: {x:8.2f}  Y: {y:8.2f}  Z: {z:8.2f}  {units}"
        )

    def clear_coords(self) -> None:
        self._coords.setText("")
