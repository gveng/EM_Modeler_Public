"""Entry point for EM 3D Modeler."""
import sys
import os
import locale

def main():
    # Must set this before importing VTK / Qt to avoid OpenGL conflicts on Windows
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore    import Qt, QLocale
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)

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
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
