"""Dialog listing the translations recorded in the history."""

import os
from datetime import datetime

from PyQt5.QtCore import QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from argonaut_cli.history import TranslationHistory

from argonaut_gui.i18n import tr
from argonaut_gui.window.translation_run import TranslationRunMixin

DATE_COL = 0
FILE_COL = 1
FOLDER_COL = 2
PAIR_COL = 3
ENGINE_COL = 4
TIME_COL = 5
CACHE_COL = 6
COLUMN_KEYS = (
    "hist_col_date", "hist_col_file", "hist_col_folder", "hist_col_pair",
    "hist_col_engine", "hist_col_time", "hist_col_cache",
)

OUTPUT_ROLE = Qt.UserRole  # where the translation was written
SOURCE_ROLE = Qt.UserRole + 1
# the cache reuse rate as a number, so the column sorts by it rather than by
# the "33 %" it reads; None for a row recorded before it was kept
CACHE_RATE_ROLE = Qt.UserRole + 2

ENGINE_NAMES = {"nllb": "NLLB-200", "argos": "Argos Translate"}


class HistoryItem(QTreeWidgetItem):
    """A row in the history. The cache column sorts by its rate rather than by
    the text of it, since "100 %" is not greater than "33 %" as a string; the
    rest sort case-insensitively by their text."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else DATE_COL
        if column == CACHE_COL:
            return self.cache_rate() < other.cache_rate()
        return self.text(column).lower() < other.text(column).lower()

    def cache_rate(self):
        """The reuse rate, or -1 for a row that recorded none: those group
        below every rate rather than reading as a run that reused nothing."""
        rate = self.data(CACHE_COL, CACHE_RATE_ROLE)
        return -1 if rate is None else rate


class HistoryDialog(QDialog):
    """Shows what was translated, newest first. Double-clicking a row opens
    the translated file; rows whose output no longer exists are greyed out
    rather than hidden, since the history is a record of what happened."""

    history_cleared = pyqtSignal()  # so the menu's size label can refresh

    def __init__(self, db_path=None, parent=None):
        super().__init__(parent)
        self.db_path = db_path or TranslationHistory.default_db_path()
        self.setWindowTitle(tr("hist_title"))
        self.setMinimumSize(620, 380)

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setHeaderLabels([tr(key) for key in COLUMN_KEYS])
        self.tree.itemDoubleClicked.connect(self.open_item)
        layout.addWidget(self.tree, 1)

        self.summary = QLabel()
        self.summary.setStyleSheet("color: gray;")
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        self.open_btn = QPushButton(tr("hist_open"))
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self.open_selected)
        self.clear_btn = QPushButton(tr("hist_clear"))
        self.clear_btn.clicked.connect(self.clear_history)
        self.close_btn = QPushButton(tr("close"))
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.tree.itemSelectionChanged.connect(self.update_buttons)
        self.reload()

    def reload(self):
        """Reads the database again and repopulates the list."""
        self.tree.setSortingEnabled(False)  # sorting while filling is wasted work
        self.tree.clear()
        history = TranslationHistory(self.db_path, ttl_days=0)  # never prune here
        try:
            entries = history.entries()
        finally:
            history.close()
        for entry in entries:
            self.tree.addTopLevelItem(self._item_for(entry))
        self.tree.setSortingEnabled(True)
        for col in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(col)
        self.summary.setText(
            tr("hist_count", count=len(entries)) if entries else tr("hist_empty")
        )
        self.clear_btn.setEnabled(bool(entries))
        self.update_buttons()

    @staticmethod
    def _item_for(entry):
        item = HistoryItem()
        item.setText(
            DATE_COL,
            datetime.fromtimestamp(entry.translated_at).strftime("%Y-%m-%d %H:%M"),
        )
        item.setText(FILE_COL, os.path.basename(entry.source_path))
        # the folder of the source, as the file list shows it: the name alone
        # does not say which of two same-named documents was translated
        item.setText(FOLDER_COL, os.path.dirname(entry.source_path))
        item.setText(PAIR_COL, f"{entry.from_code} → {entry.to_code}")
        item.setText(ENGINE_COL, ENGINE_NAMES.get(entry.engine, entry.engine))
        item.setText(TIME_COL, TranslationRunMixin.format_duration(entry.seconds))
        item.setTextAlignment(TIME_COL, Qt.AlignRight | Qt.AlignVCenter)
        # how much of this file the cache answered: the rate in the cell, the
        # figures behind it in the tooltip, and an empty cell for a row
        # recorded before they were kept — which is not the same as a 0 %
        if entry.segments:
            rate = round(100 * entry.reused / entry.segments)
            item.setText(CACHE_COL, f"{rate} %")
            item.setData(CACHE_COL, CACHE_RATE_ROLE, rate)
            item.setToolTip(CACHE_COL, tr(
                "cache_reused_summary",
                reused=entry.reused, total=entry.segments, percent=rate,
            ))
        item.setTextAlignment(CACHE_COL, Qt.AlignRight | Qt.AlignVCenter)
        item.setData(FILE_COL, OUTPUT_ROLE, entry.output_path)
        item.setData(FILE_COL, SOURCE_ROLE, entry.source_path)
        item.setToolTip(
            FILE_COL, f"{entry.source_path}\n→ {entry.output_path}"
        )
        if not (entry.output_path and os.path.exists(entry.output_path)):
            item.setDisabled(True)  # the translation was moved or deleted
            item.setToolTip(FILE_COL, item.toolTip(FILE_COL) + f"\n{tr('hist_missing')}")
        return item

    def selected_output(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        path = items[0].data(FILE_COL, OUTPUT_ROLE)
        return path if path and os.path.exists(path) else None

    def update_buttons(self):
        self.open_btn.setEnabled(self.selected_output() is not None)

    def open_selected(self):
        path = self.selected_output()
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_item(self, item, _column):
        path = item.data(FILE_COL, OUTPUT_ROLE)
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def clear_history(self):
        count, _ = TranslationHistory.db_info(self.db_path)
        answer = QMessageBox.question(
            self, tr("hist_title"), tr("hist_clear_msg", count=count)
        )
        if answer != QMessageBox.Yes:
            return
        TranslationHistory.purge_db(self.db_path)
        self.reload()
        self.history_cleared.emit()
