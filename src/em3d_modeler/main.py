"""Entry point for EM 3D Modeler."""
import sys
import os
import locale
import io


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
    from PySide6.QtCore    import QLocale

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

    from em3d_modeler.ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
