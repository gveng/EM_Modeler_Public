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

"""Entry point for EM 3D Modeler."""
import sys
import os
import locale
import io
from pathlib import Path

_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SOURCE_ROOT in sys.path:
    sys.path.remove(_SOURCE_ROOT)
sys.path.insert(0, _SOURCE_ROOT)


def _resource_path(relative_path: str) -> Path:
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(_SOURCE_ROOT).parent)),
        Path(sys.executable).resolve().parent,
        Path(_SOURCE_ROOT).parent,
    ]
    for root in roots:
        candidate = root / relative_path
        if candidate.is_file():
            return candidate
    return roots[0] / relative_path


def _splash_pixmap(pixmap):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    return pixmap.scaled(
        640,
        480,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _ensure_standard_streams() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = [ctypes.c_wchar_p]
    set_app_id("GabrieleVittori.EM3DModeler")


def main():
    # Must set this before importing VTK / Qt to avoid OpenGL conflicts on Windows
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    _ensure_standard_streams()
    _set_windows_app_user_model_id()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore    import QEventLoop, QLocale, QTimer, Qt
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QSplashScreen

    # Qt 6 enables high-DPI scaling by default; explicitly setting the old
    # AA_* attributes triggers deprecation warnings on modern PySide6.
    app = QApplication(sys.argv)
    QLocale.setDefault(QLocale.c())
    try:
        locale.setlocale(locale.LC_ALL, "C")
    except Exception:
        pass
    app.setApplicationName("EM 3D Modeler")
    app.setOrganizationName("EM3D")

    logo_path = _resource_path("Icons/SplashScreen/EM_Logo.ico")
    if not logo_path.is_file():
        logo_path = _resource_path("Icons/SplashScreen/EM_Logo.png")
    logo_icon = QIcon(str(logo_path))
    app.setWindowIcon(logo_icon)

    splash_path = _resource_path("Icons/SplashScreen/EM_3d_MODELER_Splash_Screen.png")
    splash_pixmap = QPixmap(str(splash_path))
    splash = None
    if not splash_pixmap.isNull():
        screen = app.primaryScreen()
        screen_geometry = screen.availableGeometry() if screen is not None else None
        splash_pixmap = _splash_pixmap(splash_pixmap)
        splash = QSplashScreen(splash_pixmap, Qt.WindowType.WindowStaysOnTopHint)
        if screen_geometry is not None:
            splash.move(
                screen_geometry.x() + (screen_geometry.width() - splash.width()) // 2,
                screen_geometry.y() + (screen_geometry.height() - splash.height()) // 2,
            )
        splash.show()
        app.processEvents()

    from em3d_modeler.ui.main_window import MainWindow
    win = MainWindow()
    win.setWindowIcon(logo_icon)
    win.show()
    if splash is not None:
        splash_wait = QEventLoop()
        QTimer.singleShot(2000, splash_wait.quit)
        splash_wait.exec()
        splash.finish(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
