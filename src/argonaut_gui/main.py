#!/usr/bin/env python3
"""Minimalist document translator (PyQt5 + argos-translate-files).

Entry point: sets up warning silencing (which must happen before importing
the libraries that emit them) and launches the main window.
"""

import os
import sys
import warnings

# silence harmless warnings: window-activation attempts on Wayland and the
# requests version check
os.environ.setdefault(
    "QT_LOGGING_RULES", "*.debug=false;qt.qpa.wayland.warning=false"
)
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*")

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from argonaut_gui import __version__
from argonaut_gui.i18n import load_language
from argonaut_gui.window import MainWindow


def main():
    app = QApplication(sys.argv)
    # organization and application identify where QSettings stores the language
    app.setOrganizationName("Argonaut")
    app.setApplicationName("Argonaut")
    app.setApplicationVersion(__version__)
    # the Wayland app_id must match the installed .desktop file so the
    # compositor can resolve the window icon; without it Wayland shows its
    # generic fallback icon
    app.setDesktopFileName("io.github.nibblex.Argonaut")
    # Wayland ignores window icons and relies on the app_id above; this covers
    # X11, where the icon is taken from the window instead
    app.setWindowIcon(QIcon.fromTheme("io.github.nibblex.Argonaut"))
    load_language()
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
