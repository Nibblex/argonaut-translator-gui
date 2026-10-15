"""File-list side of the main window: adding, removing and opening files,
column visibility, drag and drop and the output folder."""

import os
from datetime import datetime

from PyQt5.QtCore import QSettings, QUrl, Qt
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QAction, QFileDialog, QMenu

from argonaut_cli.translation import SUPPORTED_EXTS

from argonaut_gui.i18n import tr
from argonaut_gui.window.file_list import (
    COLUMN_KEYS,
    FILE_MTIME_ROLE,
    FILE_PATH_ROLE,
    FILE_SIZE_ROLE,
    FILE_STATE_ROLE,
    FOLDER_COL,
    HIDEABLE_COLS,
    MODIFIED_COL,
    NAME_COL,
    SIZE_COL,
    STATUS_COL,
    TYPE_COL,
    FileItem,
    human_size,
)


class FileListMixin:
    """File-handling half of MainWindow. Operates on the widgets its
    __init__ creates (file_list, total_label, the output-folder row)."""

    def add_files(self):
        patterns = " ".join(f"*{ext}" for ext in SUPPORTED_EXTS)
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("select_documents"), "", tr("documents_filter", patterns=patterns)
        )
        self.add_paths(paths)

    def add_folder(self):
        """Adds every supported document in a folder, walking it the way a
        dropped folder is walked: the two are the same act, and picking a
        hundred files by hand in the file dialog is not."""
        path = QFileDialog.getExistingDirectory(self, tr("select_folder"))
        if path:
            self.add_paths(self.expand_dirs([path]))

    def paths(self):
        return [
            self.file_list.topLevelItem(i).data(NAME_COL, FILE_PATH_ROLE)
            for i in range(self.file_list.topLevelItemCount())
        ]

    def add_paths(self, paths):
        existing = set(self.paths())
        for path in paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in SUPPORTED_EXTS and path not in existing:
                existing.add(path)
                self.add_file_item(path)
        self.update_total_size()

    def add_file_item(self, path):
        try:
            stat = os.stat(path)
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        item = FileItem()
        # the name column shows the file name; the folder column holds its path
        item.setText(NAME_COL, os.path.basename(path))
        item.setData(NAME_COL, FILE_PATH_ROLE, path)
        item.setText(TYPE_COL, os.path.splitext(path)[1].lstrip(".").upper())
        item.setText(SIZE_COL, human_size(size))
        item.setData(SIZE_COL, FILE_SIZE_ROLE, size)
        item.setTextAlignment(SIZE_COL, Qt.AlignRight | Qt.AlignVCenter)
        item.setText(MODIFIED_COL, self._format_mtime(mtime))
        item.setData(MODIFIED_COL, FILE_MTIME_ROLE, mtime)
        item.setText(FOLDER_COL, os.path.dirname(path))
        self.file_list.addTopLevelItem(item)

    @staticmethod
    def _format_mtime(mtime):
        if not mtime:
            return ""
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

    def _item_for_path(self, path):
        for i in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(i)
            if item.data(NAME_COL, FILE_PATH_ROLE) == path:
                return item
        return None

    def _set_file_state(self, path, state):
        """Sets the Status column of the row for `path` (no-op if it was
        removed meanwhile) and returns the row's item. The state key is
        stored so the column keeps sorting by rank and re-translates on a
        language change."""
        item = self._item_for_path(path)
        if item is not None:
            item.setData(STATUS_COL, FILE_STATE_ROLE, state)
            item.setText(STATUS_COL, tr(f"status_{state}"))
        return item

    # --- column visibility ---
    def show_columns_menu(self, pos):
        """Header context menu with a checkable entry per hideable column."""
        menu = QMenu(self)
        for col in HIDEABLE_COLS:
            action = QAction(tr(COLUMN_KEYS[col]), self, checkable=True)
            action.setChecked(not self.file_list.isColumnHidden(col))
            action.toggled.connect(lambda shown, c=col: self.set_column_visible(c, shown))
            menu.addAction(action)
        menu.exec_(self.file_list.header().mapToGlobal(pos))

    def set_column_visible(self, col, visible):
        self.file_list.setColumnHidden(col, not visible)
        hidden = [
            COLUMN_KEYS[c] for c in HIDEABLE_COLS if self.file_list.isColumnHidden(c)
        ]
        QSettings().setValue("hidden_columns", ",".join(hidden))

    def restore_columns(self):
        saved = QSettings().value("hidden_columns", "")
        hidden = set(saved.split(",")) if saved else set()
        for col in HIDEABLE_COLS:
            self.file_list.setColumnHidden(col, COLUMN_KEYS[col] in hidden)

    def update_total_size(self):
        count = self.file_list.topLevelItemCount()
        if not count:
            self.total_label.clear()
            return
        total = sum(
            self.file_list.topLevelItem(i).data(SIZE_COL, FILE_SIZE_ROLE) or 0
            for i in range(count)
        )
        self.total_label.setText(
            tr("batch_total", count=count, size=human_size(total))
        )

    def refresh_file_buttons(self):
        """Remove and Open act on the selected rows and Clear on all of them,
        so with nothing selected, or nothing in the list, they have nothing to
        do. Driven by the list's own signals rather than by the calls that add
        and remove rows, which is what keeps it from going stale."""
        selected = bool(self.file_list.selectedItems())
        self.remove_btn.setEnabled(selected)
        self.open_file_btn.setEnabled(selected)
        self.clear_btn.setEnabled(self.file_list.topLevelItemCount() > 0)

    def clear_files(self):
        self.file_list.clear()
        self.update_total_size()

    def open_selected(self):
        for item in self.file_list.selectedItems():
            path = item.data(NAME_COL, FILE_PATH_ROLE)
            if os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeTopLevelItem(self.file_list.indexOfTopLevelItem(item))
        self.update_total_size()

    def dragEnterEvent(self, event):
        """Drops land anywhere on the window, but the file list is where they
        end up, so that is what lights up while the drag is overhead."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.file_list.set_drag_active(True)

    def dragLeaveEvent(self, event):
        self.file_list.set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.file_list.set_drag_active(False)
        self.add_paths(
            self.expand_dirs(
                url.toLocalFile()
                for url in event.mimeData().urls()
                if url.isLocalFile()
            )
        )

    @staticmethod
    def expand_dirs(paths):
        """Yields files as-is and walks dropped directories recursively;
        add_paths filters out the unsupported extensions."""
        for path in paths:
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    dirs.sort()
                    for name in sorted(files):
                        yield os.path.join(root, name)
            else:
                yield path

    # --- output folder ---
    def choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, tr("output_dir_title"), self.output_dir or ""
        )
        if path:
            self.output_dir = path
            self.output_label.setText(path)
            self.output_label.setStyleSheet("")
            self.open_out_btn.setEnabled(True)

    def reset_output_dir(self):
        self.output_dir = None
        self.output_label.setText(tr("output_default"))
        self.output_label.setStyleSheet("color: gray;")
        self.open_out_btn.setEnabled(False)

    def open_output_dir(self):
        if self.output_dir and os.path.isdir(self.output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_dir))
