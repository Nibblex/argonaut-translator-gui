"""Light, dark and system color themes, applied to the whole application.

"system" (the default) keeps whatever Qt picked up from the desktop. The
explicit themes switch to the Fusion style so both render identically on
every platform: "light" is Fusion's standard palette, "dark" the classic
Fusion dark palette."""

from PyQt5.QtCore import QSettings
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

THEMES = ("system", "light", "dark")

# the desktop's own style and palette, captured before the first override
# so choosing "system" again can restore them
_system = None


def current_theme():
    saved = QSettings().value("theme", "system")
    return saved if saved in THEMES else "system"


def dark_palette():
    """The widely used Fusion dark palette."""
    palette = QPalette()
    window = QColor(53, 53, 53)
    text = QColor(255, 255, 255)
    disabled = QColor(127, 127, 127)
    accent = QColor(42, 130, 218)
    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.AlternateBase, window)
    palette.setColor(QPalette.ToolTipBase, window)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, window)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.Link, accent)
    palette.setColor(QPalette.Highlight, accent)
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, disabled)
    return palette


def apply_theme(name):
    """Applies one of THEMES application-wide; anything else (an unknown
    saved value included) falls back to the system default."""
    global _system
    app = QApplication.instance()
    if _system is None:
        _system = (app.style().objectName(), app.palette())
    if name == "dark":
        app.setStyle("Fusion")
        app.setPalette(dark_palette())
    elif name == "light":
        app.setStyle("Fusion")
        app.setPalette(app.style().standardPalette())
    else:
        app.setStyle(_system[0])
        app.setPalette(_system[1])
