"""About dialog: the application summary, a copyable diagnostic report of
the running environment, and the credits and licenses of the projects
Argonaut builds on."""

import platform
from importlib import metadata

from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QTimer
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import argostranslate.settings

from argonaut_cli import nllb
from argonaut_cli.history import TranslationHistory
from argonaut_cli.translation import SUPPORTED_EXTS, TranslationCache

from argonaut_gui.i18n import tr
from argonaut_gui.window.file_list import human_size

ISSUES_URL = "https://github.com/Nibblex/Argonaut/issues"
RELEASES_URL = "https://github.com/Nibblex/Argonaut/releases"
# the README is the user manual; #readme anchors past the badges
MANUAL_URL = "https://github.com/Nibblex/Argonaut#readme"

# distribution names, as published on PyPI; Argonaut's own engine first,
# since it is released apart from the window
DEPENDENCIES = (
    "argonaut-translator-cli",
    "argos-translate-lt",
    "argos-translate-files",
    "ctranslate2",
    "pymupdf",
    "langdetect",
    "psutil",
)

# project names, homes and licenses; the surrounding prose is translated,
# this list is the same in every language
CREDITS_HTML = (
    "<ul>"
    "<li><a href='https://www.argosopentech.com/'>Argos Translate</a>"
    " — MIT</li>"
    "<li><a href='https://github.com/LibreTranslate/argos-translate-files'>"
    "argos-translate-files</a> — AGPL-3.0</li>"
    "<li><a href='https://github.com/pymupdf/PyMuPDF'>PyMuPDF</a>"
    " — AGPL-3.0</li>"
    "<li><a href='https://www.riverbankcomputing.com/software/pyqt/'>PyQt5</a>"
    " — GPL-3.0</li>"
    "<li><a href='https://github.com/OpenNMT/CTranslate2'>CTranslate2</a>"
    " — MIT</li>"
    "<li><a href='https://github.com/google/sentencepiece'>SentencePiece</a>"
    " — Apache-2.0</li>"
    "<li><a href='https://github.com/Mimino666/langdetect'>langdetect</a>"
    " — Apache-2.0</li>"
    "<li><a href='https://github.com/giampaolo/psutil'>psutil</a>"
    " — BSD-3-Clause</li>"
    "</ul>"
)


def _dist_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "?"


def diagnostic_report(backend, language_count):
    """Plain-text environment report, ready to paste into a bug report.
    Deliberately untranslated: it is read by whoever triages the issue."""
    cache_db = TranslationCache.default_db_path()
    entries, size = TranslationCache.db_info(cache_db)
    history_db = TranslationHistory.default_db_path()
    hist_entries, hist_size = TranslationHistory.db_info(history_db)
    engine = "NLLB-200" if backend == "nllb" else "Argos Translate"
    deps = ", ".join(f"{name} {_dist_version(name)}" for name in DEPENDENCIES)
    return "\n".join([
        f"Argonaut {QApplication.applicationVersion()}",
        f"Python {platform.python_version()} · Qt {QT_VERSION_STR}"
        f" · PyQt {PYQT_VERSION_STR}",
        f"OS: {platform.platform()}",
        f"Dependencies: {deps}",
        f"Engine: {engine} · Languages: {language_count}",
        f"NLLB model installed: {'yes' if nllb.is_model_installed() else 'no'}"
        f" ({nllb.model_dir()})",
        f"Cache: {entries} entries, {human_size(size)} ({cache_db})",
        f"History: {hist_entries} entries, {human_size(hist_size)} ({history_db})",
        f"Argos data: {getattr(argostranslate.settings, 'data_dir', '')}",
    ])


class AboutDialog(QDialog):
    """Three tabs: the classic about text with support links, a copyable
    diagnostic report, and the credits the bundled licenses require."""

    COPY_FEEDBACK_MS = 2000  # how long the copy button says "Copied"

    def __init__(self, backend, language_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("about_title"))
        self.setMinimumSize(480, 400)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # --- about ---
        about_page = QWidget()
        about_layout = QVBoxLayout(about_page)
        self.about_label = QLabel(tr(
            "about_text",
            version=QApplication.applicationVersion(),
            formats=" ".join(SUPPORTED_EXTS),
        ))
        self.about_label.setWordWrap(True)
        self.about_label.setOpenExternalLinks(True)
        about_layout.addWidget(self.about_label)
        self.links_label = QLabel(
            f"<a href='{ISSUES_URL}'>{tr('about_issues')}</a> · "
            f"<a href='{RELEASES_URL}'>{tr('about_releases')}</a>"
        )
        self.links_label.setOpenExternalLinks(True)
        about_layout.addWidget(self.links_label)
        about_layout.addStretch(1)
        self.tabs.addTab(about_page, tr("about_tab_about"))

        # --- details ---
        details_page = QWidget()
        details_layout = QVBoxLayout(details_page)
        self.report = QPlainTextEdit(diagnostic_report(backend, language_count))
        self.report.setReadOnly(True)
        self.report.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        details_layout.addWidget(self.report, 1)
        self.copy_btn = QPushButton(tr("about_copy"))
        self.copy_btn.clicked.connect(self.copy_report)
        copy_row = QHBoxLayout()
        copy_row.addWidget(self.copy_btn)
        copy_row.addStretch(1)
        details_layout.addLayout(copy_row)
        self.tabs.addTab(details_page, tr("about_tab_details"))

        # --- credits and licenses ---
        credits_page = QWidget()
        credits_layout = QVBoxLayout(credits_page)
        self.credits_label = QLabel(
            tr("about_credits_intro") + CREDITS_HTML + tr("about_credits_nllb")
        )
        self.credits_label.setWordWrap(True)
        self.credits_label.setOpenExternalLinks(True)
        credits_layout.addWidget(self.credits_label)
        credits_layout.addStretch(1)
        self.tabs.addTab(credits_page, tr("about_tab_credits"))

        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def copy_report(self):
        QApplication.clipboard().setText(self.report.toPlainText())
        self.copy_btn.setText(tr("about_copied"))  # visible feedback…
        QTimer.singleShot(
            self.COPY_FEEDBACK_MS,
            lambda: self.copy_btn.setText(tr("about_copy")),  # …then back
        )
