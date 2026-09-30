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


def _attach_worker_streams() -> None:
    def _configure_stream(stream) -> None:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            return
        try:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (OSError, ValueError):
            pass

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if stream is not None:
            _configure_stream(stream)

    if sys.stdout is not None and sys.stderr is not None:
        return
    if sys.platform != "win32":
        _ensure_standard_streams()
        return

    import ctypes
    import msvcrt

    get_std_handle = ctypes.windll.kernel32.GetStdHandle
    get_std_handle.argtypes = [ctypes.c_ulong]
    get_std_handle.restype = ctypes.c_void_p
    for stream_name, standard_handle in (("stdout", -11), ("stderr", -12)):
        if getattr(sys, stream_name) is not None:
            continue
        handle = get_std_handle(standard_handle)
        if handle in (None, 0, -1, ctypes.c_void_p(-1).value):
            continue
        try:
            descriptor = msvcrt.open_osfhandle(int(handle), os.O_TEXT)
            stream = os.fdopen(descriptor, "w", encoding="utf-8", errors="replace", buffering=1)
            setattr(sys, stream_name, stream)
        except OSError:
            pass

    _ensure_standard_streams()
    for stream_name in ("stdout", "stderr"):
        _configure_stream(getattr(sys, stream_name))


def _run_script_worker(script_path: str) -> int:
    import runpy

    _attach_worker_streams()
    try:
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    return 0


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = [ctypes.c_wchar_p]
    set_app_id("GabrieleVittori.EM3DModeler")


_DLL_DIRECTORY_HANDLES = []


def _register_frozen_dll_directories() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    runtime_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    for directory in (runtime_root / "bin", runtime_root):
        if directory.is_dir():
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))


def main():
    _register_frozen_dll_directories()
    if len(sys.argv) > 1 and sys.argv[1] == "--em3d-run-script":
        if len(sys.argv) != 3:
            print("Usage: EM3D_Modeler.exe --em3d-run-script <script.py>", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(_run_script_worker(sys.argv[2]))

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
    win.showMaximized()
    if splash is not None:
        splash_wait = QEventLoop()
        QTimer.singleShot(2000, splash_wait.quit)
        splash_wait.exec()
        splash.finish(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
