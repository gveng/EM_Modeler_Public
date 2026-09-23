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
    from PySide6.QtGui import QPainter, QPixmap

    canvas = QPixmap(640, 480)
    canvas.fill(pixmap.toImage().pixelColor(0, 0))
    image = pixmap.scaled(
        canvas.size(),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.drawPixmap((canvas.width() - image.width()) // 2, (canvas.height() - image.height()) // 2, image)
    painter.end()
    return canvas


def _ensure_standard_streams() -> None:
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()

def main():
    # Must set this before importing VTK / Qt to avoid OpenGL conflicts on Windows
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    _ensure_standard_streams()

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

    logo_path = _resource_path("Icons/SplashScreen/EM_Logo.png")
    logo_icon = QIcon(str(logo_path))
    app.setWindowIcon(logo_icon)

    splash_path = _resource_path("Icons/SplashScreen/EM_Studio_Splash_Screen.png")
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
