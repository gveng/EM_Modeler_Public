# Copyright (C) 2026 Gabriele Vittori
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Persistent info and simulation status bar below the workspace tabs."""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QProgressBar
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
        self._sim_status = QLabel("RUN")
        self._sim_status.setAlignment(Qt.AlignCenter)
        self._sim_status.setFixedWidth(42)
        self._sim_status.setStyleSheet(
            "background:#214d37; color:#b7f7ce; border:1px solid #3c815d; "
            "border-radius:3px; font-weight:700; padding:2px 5px;"
        )
        self._sim_progress = QProgressBar()
        self._sim_progress.setRange(0, 100)
        self._sim_progress.setFixedSize(116, 12)
        self._sim_progress.setTextVisible(False)
        self._sim_progress.setStyleSheet(
            "QProgressBar { background:#343746; border:1px solid #505466; border-radius:3px; }"
            "QProgressBar::chunk { background:#55bd7a; border-radius:2px; }"
        )
        self._sim_progress_label = QLabel("0%")
        self._sim_progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._sim_progress_label.setFixedWidth(38)
        self._model_view = True
        self._model_message = "Ready."
        self._sim_running = False
        self._sim_status.hide()
        self._sim_progress.hide()
        self._sim_progress_label.hide()

        layout.addWidget(self._icon)
        layout.addWidget(self._msg, stretch=1)
        layout.addWidget(self._coords)
        layout.addWidget(self._sim_status)
        layout.addWidget(self._sim_progress)
        layout.addWidget(self._sim_progress_label)

        self.setStyleSheet(
            "background:#1e1e2e; color:#cdd6f4; font-size:11px; border-top:1px solid #45475a;"
        )

    # ─────────────────────────────────────────────────── public API
    def set_info(self, msg: str) -> None:
        self._model_message = str(msg)
        self._icon.setText("ℹ")
        self._icon.setStyleSheet("color:#89b4fa;")
        self._refresh_message()

    def set_error(self, msg: str) -> None:
        self._model_message = str(msg)
        self._icon.setText("✖")
        self._icon.setStyleSheet("color:#f38ba8;")
        self._refresh_message()

    def set_model_view(self, is_model_view: bool) -> None:
        self._model_view = bool(is_model_view)
        self._coords.setVisible(self._model_view)
        self._refresh_message()

    def set_simulation_status(
        self,
        running: bool,
        *,
        completed_jobs: int = 0,
        total_jobs: int = 0,
        current_fraction: float | None = None,
    ) -> None:
        self._sim_running = bool(running)
        self._sim_status.setVisible(self._sim_running)
        self._sim_progress.setVisible(self._sim_running)
        self._sim_progress_label.setVisible(self._sim_running)
        if total_jobs > 0:
            fraction = max(0.0, min(1.0, float(completed_jobs) / total_jobs))
            if current_fraction is not None and completed_jobs < total_jobs:
                fraction = max(
                    fraction,
                    min(1.0, (completed_jobs + max(0.0, current_fraction)) / total_jobs),
                )
            percentage = round(fraction * 100)
            self._sim_progress.setValue(percentage)
            self._sim_progress_label.setText(f"{percentage}%")
        else:
            self._sim_progress.setValue(0)
            self._sim_progress_label.setText("...")
        self._refresh_message()

    def _refresh_message(self) -> None:
        if self._model_view:
            self._msg.setText(self._model_message)
        elif self._sim_running:
            self._msg.setText("Simulation RUN")
        else:
            self._msg.setText("No simulation running")

    def set_coords(self, x: float, y: float, z: float, units: str = "mm") -> None:
        self._coords.setText(
            f"X: {x:8.2f}  Y: {y:8.2f}  Z: {z:8.2f}  {units}"
        )

    def clear_coords(self) -> None:
        self._coords.setText("")
