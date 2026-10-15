"""Dialog to browse, install and remove Argos language packages from
the GUI."""

from PyQt5.QtCore import QSettings, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from argonaut_cli import packages

from argonaut_gui.i18n import pair_text, tr
from argonaut_gui.worker import (
    PackageInstallWorker,
    PackageListWorker,
    PackageSizeWorker,
    speed_text,
    speed_units,
)

PACKAGE_ROLE = Qt.UserRole
STATE_ROLE = Qt.UserRole + 1  # "available", "installed" or "outdated"
SIZE_ROLE = Qt.UserRole + 2
INSTALLED_VERSION_ROLE = Qt.UserRole + 3  # None when not installed


class PackageDialog(QDialog):
    """Lists the packages from the Argos index; the checked ones can be
    installed (with per-package download progress) or removed."""

    packages_changed = pyqtSignal()  # something was installed or removed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("pkg_title"))
        self.setMinimumSize(380, 420)
        self.installer = None
        self.sizer = None
        self.operation = "install"  # or "update": only changes the summary
        self.errors = []

        layout = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("pkg_filter"))
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.state_combo = QComboBox()
        for key, state in (
            ("pkg_state_all", "all"),
            ("pkg_state_installed", "installed"),
            ("pkg_state_available", "available"),
            ("pkg_state_outdated", "outdated"),
        ):
            self.state_combo.addItem(tr(key), state)
        self.state_combo.currentIndexChanged.connect(self.apply_filter)
        self.select_all = QCheckBox(tr("pkg_select_all"))
        self.select_all.clicked.connect(self.on_select_all_clicked)
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.state_combo)
        filter_row.addWidget(self.select_all)
        layout.addLayout(filter_row)

        self.package_list = QListWidget()
        self.package_list.itemChanged.connect(self.update_buttons)
        layout.addWidget(self.package_list, 1)

        self.selection_label = QLabel()
        self.selection_label.setStyleSheet("color: gray;")
        layout.addWidget(self.selection_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel(tr("pkg_loading"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.install_btn = QPushButton(tr("pkg_install_btn"))
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.start_install)
        self.update_btn = QPushButton(tr("pkg_update_btn"))
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self.start_update)
        self.remove_btn = QPushButton(tr("pkg_remove_btn"))
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self.start_remove)
        self.cancel_btn = QPushButton(tr("cancel"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_install)
        self.close_btn = QPushButton(tr("close"))
        self.close_btn.clicked.connect(self.close)
        buttons.addWidget(self.install_btn, 1)
        buttons.addWidget(self.update_btn, 1)
        buttons.addWidget(self.remove_btn, 1)
        buttons.addWidget(self.cancel_btn, 1)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.lister = PackageListWorker(parent=self)
        self.lister.listed.connect(self.on_listed)
        self.lister.start()

    # --- package list ---
    def on_listed(self, available, error):
        if error:
            self.status.setText(tr("pkg_load_failed", error=error))
            return
        unmeasured = []
        for row, pkg in enumerate(available):
            item = QListWidgetItem()
            item.setData(PACKAGE_ROLE, pkg)
            item.setData(SIZE_ROLE, QSettings().value(size_key(pkg), 0, type=int))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            self.package_list.addItem(item)
            if not item.data(SIZE_ROLE):
                unmeasured.append((row, pkg))
        self.refresh_states()
        self.apply_filter()
        self.status.setText(tr("ready"))
        if unmeasured:
            self.sizer = PackageSizeWorker(unmeasured, parent=self)
            self.sizer.size_ready.connect(self.on_size_ready)
            self.sizer.start()

    def refresh_states(self):
        """Recomputes every package's state against what is installed on
        disk. Items whose state changed are unchecked, so a finished
        operation clears its selection while failures stay selected."""
        versions = packages.installed_versions()
        for item in self.all_items():
            pkg = item.data(PACKAGE_ROLE)
            installed = versions.get(packages.pair(pkg))
            if installed is None:
                state = "available"
            elif packages.is_newer(pkg.package_version, installed):
                state = "outdated"
            else:
                state = "installed"
            if item.data(STATE_ROLE) == state and (
                item.data(INSTALLED_VERSION_ROLE) == installed
            ):
                continue
            item.setData(STATE_ROLE, state)
            item.setData(INSTALLED_VERSION_ROLE, installed)
            item.setCheckState(Qt.Unchecked)
            self.refresh_item_text(item)

    def refresh_item_text(self, item):
        pkg = item.data(PACKAGE_ROLE)
        text = pair_text(pkg)
        size = item.data(SIZE_ROLE)
        if size:
            text += f"  ·  {mb_text(size)}"
        # the version in use if installed, otherwise the one on offer
        text += f"  ·  v{item.data(INSTALLED_VERSION_ROLE) or pkg.package_version}"
        state = item.data(STATE_ROLE)
        if state == "installed":
            text += f"  ({tr('pkg_installed')})"
        elif state == "outdated":
            text += f"  ({tr('pkg_outdated', version=pkg.package_version)})"
        item.setText(text)

    def all_items(self):
        return [
            self.package_list.item(i) for i in range(self.package_list.count())
        ]

    def on_size_ready(self, row, size):
        if not size:
            return
        item = self.package_list.item(row)
        item.setData(SIZE_ROLE, size)
        QSettings().setValue(size_key(item.data(PACKAGE_ROLE)), size)
        self.refresh_item_text(item)
        if item.checkState() == Qt.Checked:
            self.update_selection_summary()

    def apply_filter(self, *_):
        """Hides the packages that match neither the typed text nor the
        selected installation state. "Installed" covers the outdated ones
        too: they are installed, only not at the latest version."""
        text = self.filter_edit.text().strip().lower()
        wanted = self.state_combo.currentData()
        for item in self.all_items():
            pkg = item.data(PACKAGE_ROLE)
            state = item.data(STATE_ROLE)
            matches_state = (
                wanted == "all"
                or state == wanted
                or (wanted == "installed" and state == "outdated")
            )
            # the English names are searched alongside the shown text, so a
            # translated list still answers to "german" and to its codes
            haystack = (
                f"{item.text()} {pkg.from_name} {pkg.to_name} "
                f"{pkg.from_code} {pkg.to_code}"
            ).lower()
            item.setHidden(
                (bool(text) and text not in haystack) or not matches_state
            )
        # a hidden package is not part of the selection, so the summary and
        # the buttons must be recomputed whenever the visible set changes
        self.update_buttons()

    def visible_items(self):
        return [item for item in self.all_items() if not item.isHidden()]

    def on_select_all_clicked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        for item in self.visible_items():
            item.setCheckState(state)
        self.sync_select_all()

    def sync_select_all(self):
        """Mirrors the item check states: unchecked, checked or partial."""
        items = self.visible_items()
        checked = sum(item.checkState() == Qt.Checked for item in items)
        if not items or checked == 0:
            state = Qt.Unchecked
        elif checked == len(items):
            state = Qt.Checked
        else:
            state = Qt.PartiallyChecked
        self.select_all.setCheckState(state)

    def checked_items(self, *states):
        # only visible packages count: filtering one out drops it from the
        # selection, so "select all" and the actions honour the filter
        return [
            item
            for item in self.visible_items()
            if item.checkState() == Qt.Checked and item.data(STATE_ROLE) in states
        ]

    def update_buttons(self):
        self.sync_select_all()
        self.update_selection_summary()
        idle = self.installer is None
        self.install_btn.setEnabled(idle and bool(self.checked_items("available")))
        self.update_btn.setEnabled(idle and bool(self.checked_items("outdated")))
        self.remove_btn.setEnabled(
            idle and bool(self.checked_items("installed", "outdated"))
        )

    def update_selection_summary(self):
        """Shows how many packages are checked and their combined download
        size; '≥' marks a total that omits sizes still being measured."""
        items = self.checked_items("available", "installed", "outdated")
        if not items:
            self.selection_label.clear()
            return
        sizes = [item.data(SIZE_ROLE) for item in items]
        total = sum(sizes)
        if not total:
            size_text = "—"
        else:
            prefix = "≥ " if 0 in sizes else ""
            size_text = prefix + mb_text(total)
        self.selection_label.setText(
            tr("pkg_selection", count=len(items), size=size_text)
        )

    # --- installation and updates ---
    def start_install(self):
        self.start_download(self.checked_items("available"), "install")

    def start_update(self):
        # installing replaces the older version, so an update is the same
        # download with a different wording in the summary
        self.start_download(self.checked_items("outdated"), "update")

    def start_download(self, items, operation):
        if not items:
            return
        self.errors = []
        self.operation = operation
        self.set_busy(True)
        self.installer = PackageInstallWorker(
            [item.data(PACKAGE_ROLE) for item in items], parent=self
        )
        self.installer.package_started.connect(self.on_package_started)
        self.installer.progress.connect(self.on_progress)
        self.installer.package_failed.connect(
            lambda desc, err: self.errors.append(f"{desc}: {err}")
        )
        self.installer.install_finished.connect(self.on_install_finished)
        self.installer.start()

    def set_busy(self, busy):
        self.filter_edit.setEnabled(not busy)
        self.state_combo.setEnabled(not busy)
        self.select_all.setEnabled(not busy)
        self.package_list.setEnabled(not busy)
        self.install_btn.setEnabled(not busy)
        self.update_btn.setEnabled(not busy)
        self.remove_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0)

    def cancel_install(self):
        if self.installer is not None:
            self.installer.cancel()
            self.cancel_btn.setEnabled(False)
            self.status.setText(tr("cancelling"))

    def on_package_started(self, index, total, description):
        self.status.setText(
            tr("pkg_downloading", name=description, index=index + 1, total=total)
        )

    def on_progress(self, done, total, speed):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        text = f"%p% — {done}/{total} MB"
        rate = speed_text(speed, speed_units())
        if rate:
            text += f" — {rate}"
        self.progress.setFormat(text)

    def on_install_finished(self, count):
        cancelled = self.installer is not None and self.installer.was_cancelled()
        self.installer = None
        self.set_busy(False)
        self.refresh_states()
        self.apply_filter()  # the packages just installed may leave the view
        self.update_buttons()

        lines = []
        if cancelled:
            lines.append(tr("cancelled"))
        if count:
            done_key = "pkg_updated" if self.operation == "update" else "pkg_done"
            lines.append(tr(done_key, count=count))
        if self.errors:
            lines.append(tr("errors_header"))
            lines.extend(f"  ✗ {e}" for e in self.errors)
        self.status.setText("\n".join(lines) or tr("ready"))
        if count:
            self.packages_changed.emit()

    # --- removal ---
    def start_remove(self):
        items = self.checked_items("installed", "outdated")
        if not items:
            return
        answer = QMessageBox.question(
            self, tr("pkg_title"), tr("pkg_remove_msg", count=len(items))
        )
        if answer != QMessageBox.Yes:
            return

        removed = 0
        errors = []
        for item in items:
            pkg = item.data(PACKAGE_ROLE)
            try:
                packages.uninstall(packages.pair(pkg))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{pair_text(pkg)}: {exc}")
            else:
                removed += 1

        lines = []
        if removed:
            lines.append(tr("pkg_removed", count=removed))
        if errors:
            lines.append(tr("errors_header"))
            lines.extend(f"  ✗ {e}" for e in errors)
        self.status.setText("\n".join(lines) or tr("ready"))
        self.refresh_states()
        self.apply_filter()  # removed packages may leave the view
        self.update_buttons()
        if removed:
            self.packages_changed.emit()

    def closeEvent(self, event):
        if self.installer is not None and self.installer.isRunning():
            self.installer.cancel()
            self.installer.wait(5000)
        if self.sizer is not None and self.sizer.isRunning():
            self.sizer.cancel()
            self.sizer.wait(5000)
        if self.lister.isRunning():
            self.lister.wait(5000)
        super().closeEvent(event)


def mb_text(size_bytes):
    """Whole-megabyte label used in the list and the selection summary."""
    return f"{max(1, round(size_bytes / 2**20))} MB"


def size_key(pkg):
    """QSettings key caching a package's size; versioned so a new release
    of the package is measured again."""
    return f"pkg_size/{pkg.code}@{pkg.package_version}"
