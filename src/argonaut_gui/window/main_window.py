"""The main window itself: widget construction, settings persistence,
interface-language switching and the deferred close."""

import os

import psutil
from PyQt5.QtCore import QSettings, QTimer, QUrl, Qt
from PyQt5.QtGui import QDesktopServices, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import argostranslate.settings

from argonaut_cli import nllb

from argonaut_gui.i18n import (
    LANGUAGES,
    current_language,
    lang_text,
    set_language,
    sorted_languages,
    tr,
)
from argonaut_gui.window.about_dialog import ISSUES_URL, MANUAL_URL, AboutDialog
from argonaut_gui.window.engine import EngineMixin
from argonaut_gui.window.file_list import (
    COLUMN_KEYS,
    FILE_STATE_ROLE,
    FOLDER_COL,
    MODIFIED_COL,
    NAME_COL,
    SIZE_COL,
    STATUS_COL,
    TYPE_COL,
    FileTree,
)
from argonaut_gui.window.files import FileListMixin
from argonaut_gui.window.theme import THEMES, apply_theme, current_theme
from argonaut_gui.window.translation_run import TranslationRunMixin
from argonaut_gui.worker import quality_mode, speed_units


class MainWindow(FileListMixin, EngineMixin, TranslationRunMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(520, 420)
        self.setAcceptDrops(True)
        self.worker = None
        self.downloader = None
        self.results = []
        self.detected = {}
        self._batch_reused = 0  # segments the batch served from the cache
        self._batch_segments = 0  # segments that reuse is out of
        self._cancelling = False  # True while waiting for a worker to stop
        self._closing = False  # True when a close is deferred until it stops
        # live status below the progress bar, as (translation key, kwargs)
        # parts joined by " — ", so a language change can re-render it
        self._status_parts = []
        self._eta_total = None
        self._eta_t0 = 0.0
        self._eta_done0 = 0

        self.backend = QSettings().value("backend", "argos")
        if self.backend == "nllb" and not nllb.is_model_installed():
            self.backend = "argos"  # the model was removed from disk

        # applied before any widget is built, so nothing paints twice
        apply_theme(current_theme())

        self.max_threads = os.cpu_count() or 1
        # every core by default: translation is what the user is waiting for,
        # and holding threads back only makes them wait longer
        saved = QSettings().value("cpu_threads", self.max_threads, type=int)
        self.cpu_threads = min(max(1, saved), self.max_threads)
        argostranslate.settings.intra_threads = self.cpu_threads
        self.apply_quality_mode()  # before any engine reads its beam width

        self.languages = self.load_languages()

        # --- file menu ---
        # the documents are what the application is about, so they get the
        # first menu; before this the bar opened on "Language", which named
        # neither the files nor which of the two kinds of language it meant
        self.file_menu = self.menuBar().addMenu("")
        self.add_files_action = QAction("", self)
        self.add_files_action.triggered.connect(self.add_files)
        self.file_menu.addAction(self.add_files_action)
        self.add_folder_action = QAction("", self)
        self.add_folder_action.triggered.connect(self.add_folder)
        self.file_menu.addAction(self.add_folder_action)
        self.file_menu.addSeparator()
        self.open_output_action = QAction("", self)
        self.open_output_action.triggered.connect(self.open_output_dir)
        self.file_menu.addAction(self.open_output_action)
        self.file_menu.addSeparator()
        self.quit_action = QAction("", self)
        # spelled out rather than QKeySequence.Quit, which Qt binds on macOS
        # and leaves empty on X11 and Wayland — where this application runs,
        # and where Ctrl+Q is the convention anyway
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.close)
        self.file_menu.addAction(self.quit_action)
        # only openable once there is a folder of one's own to open, like the
        # button beside the output row
        self.file_menu.aboutToShow.connect(
            lambda: self.open_output_action.setEnabled(self.output_dir is not None)
        )

        # --- settings menu ---
        self.settings_menu = self.menuBar().addMenu("")

        # Preferences holds what the person using the window likes; the rest
        # of Settings holds how translation is carried out. They were mixed
        # together, so the theme sat between the CPU threads and the download
        # units as if choosing a colour were part of configuring an engine.
        # The interface language belongs here too rather than in a menu of its
        # own: at the top level "Language" read as the language being
        # translated, which is what the two combos are for
        self.prefs_menu = self.settings_menu.addMenu("")
        self.lang_menu = self.prefs_menu.addMenu("")
        self._fill_radio_menu(
            self.lang_menu, LANGUAGES, current_language(), self.change_language
        )
        self.theme_menu = self.prefs_menu.addMenu("")
        self._theme_options = [(name, f"theme_{name}") for name in THEMES]
        self.theme_actions = self._fill_radio_menu(
            self.theme_menu,
            [(name, "") for name in THEMES],  # labels: retranslate_ui
            current_theme(),
            self.change_theme,
        )
        self.speed_menu = self.prefs_menu.addMenu("")
        self._speed_options = [
            ("bits", "speed_units_bits"),
            ("bytes", "speed_units_bytes"),
        ]
        self.speed_actions = self._fill_radio_menu(
            self.speed_menu,
            [(value, "") for value, _ in self._speed_options],  # retranslate_ui
            speed_units(),
            self.change_speed_units,
        )

        self.settings_menu.addSeparator()
        self.engine_menu = self.settings_menu.addMenu("")
        engine_group = QActionGroup(self)
        engine_group.setExclusive(True)
        self.argos_action = QAction("Argos Translate", self, checkable=True)
        self.argos_action.triggered.connect(lambda: self.change_backend("argos"))
        self.nllb_action = QAction("", self, checkable=True)
        self.nllb_action.triggered.connect(lambda: self.change_backend("nllb"))
        for action in (self.argos_action, self.nllb_action):
            engine_group.addAction(action)
            self.engine_menu.addAction(action)
        (self.nllb_action if self.backend == "nllb" else self.argos_action).setChecked(True)
        self.threads_menu = self.settings_menu.addMenu("")
        self._fill_radio_menu(
            self.threads_menu,
            [(n, str(n)) for n in range(1, self.max_threads + 1)],
            self.cpu_threads,
            self.change_cpu_threads,
        )
        self.quality_menu = self.settings_menu.addMenu("")
        self._quality_options = [
            ("quality", "quality_best"),
            ("fast", "quality_fast"),
        ]
        self.quality_actions = self._fill_radio_menu(
            self.quality_menu,
            [(value, "") for value, _ in self._quality_options],  # retranslate_ui
            quality_mode(),
            self.change_quality_mode,
        )
        self.settings_menu.addSeparator()
        self.cache_menu = self.settings_menu.addMenu("")
        self.cache_enable_action = QAction("", self, checkable=True)
        self.cache_enable_action.setChecked(self.cache_enabled())
        self.cache_enable_action.triggered.connect(self.toggle_cache)
        self.cache_menu.addAction(self.cache_enable_action)
        self.cache_menu.addSeparator()
        self.cache_ttl_menu = self.cache_menu.addMenu("")
        self._cache_ttl_options = [
            (0, "cache_ttl_never"),
            (30, "cache_ttl_30"),
            (90, "cache_ttl_90"),
            (365, "cache_ttl_365"),
        ]
        self.cache_ttl_actions = self._fill_radio_menu(
            self.cache_ttl_menu,
            [(days, "") for days, _ in self._cache_ttl_options],  # labels: retranslate_ui
            QSettings().value("cache_ttl_days", 90, type=int),
            self.change_cache_ttl,
        )
        self.cache_menu.addSeparator()
        self.cache_size_action = QAction("", self)
        self.cache_size_action.setEnabled(False)  # a read-only label
        self.cache_menu.addAction(self.cache_size_action)
        self.cache_menu.addSeparator()
        self.cache_clear_action = QAction("", self)
        self.cache_clear_action.triggered.connect(self.clear_cache)
        self.cache_menu.addAction(self.cache_clear_action)
        self.cache_menu.aboutToShow.connect(self.refresh_cache_menu)
        self.history_menu = self.settings_menu.addMenu("")
        self.history_enable_action = QAction("", self, checkable=True)
        self.history_enable_action.setChecked(self.history_enabled())
        self.history_enable_action.triggered.connect(self.toggle_history)
        self.history_menu.addAction(self.history_enable_action)
        self.history_menu.addSeparator()
        self.history_ttl_menu = self.history_menu.addMenu("")
        self._history_ttl_options = [
            (0, "cache_ttl_never"),
            (30, "cache_ttl_30"),
            (90, "cache_ttl_90"),
            (365, "cache_ttl_365"),
        ]
        self.history_ttl_actions = self._fill_radio_menu(
            self.history_ttl_menu,
            [(days, "") for days, _ in self._history_ttl_options],  # retranslate_ui
            QSettings().value("history_ttl_days", 90, type=int),
            self.change_history_ttl,
        )
        self.history_menu.addSeparator()
        self.history_size_action = QAction("", self)
        self.history_size_action.setEnabled(False)  # a read-only label
        self.history_menu.addAction(self.history_size_action)
        self.history_menu.addSeparator()
        self.history_show_action = QAction("", self)
        self.history_show_action.triggered.connect(self.show_history_dialog)
        self.history_menu.addAction(self.history_show_action)
        self.history_clear_action = QAction("", self)
        self.history_clear_action.triggered.connect(self.clear_history)
        self.history_menu.addAction(self.history_clear_action)
        self.history_menu.aboutToShow.connect(self.refresh_history_menu)
        self.settings_menu.addSeparator()
        self.pkg_install_action = QAction("", self)
        self.pkg_install_action.triggered.connect(self.show_package_dialog)
        self.settings_menu.addAction(self.pkg_install_action)
        self.nllb_remove_action = QAction("", self)
        self.nllb_remove_action.triggered.connect(self.remove_nllb_model)
        self.settings_menu.addAction(self.nllb_remove_action)
        # kept accurate even if the model directory is deleted externally
        self.settings_menu.aboutToShow.connect(
            lambda: self.nllb_remove_action.setEnabled(nllb.is_model_installed())
        )
        self.nllb_remove_action.setEnabled(nllb.is_model_installed())

        # --- help menu ---
        self.help_menu = self.menuBar().addMenu("")
        self.manual_action = QAction("", self)
        self.manual_action.triggered.connect(self.show_manual)
        self.help_menu.addAction(self.manual_action)
        self.report_action = QAction("", self)
        self.report_action.triggered.connect(self.report_bug)
        self.help_menu.addAction(self.report_action)
        self.help_menu.addSeparator()
        self.about_action = QAction("", self)
        self.about_action.triggered.connect(self.show_about)
        self.help_menu.addAction(self.about_action)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # --- language selection ---
        lang_row = QHBoxLayout()
        self.from_combo = QComboBox()
        self.to_combo = QComboBox()
        self.from_combo.addItem("", None)  # "Detect language" (text set in retranslate)
        for lang in sorted_languages(self.languages):
            self.from_combo.addItem(lang_text(lang), lang)
            self.to_combo.addItem(lang_text(lang), lang)

        self.swap_btn = QToolButton()
        self.swap_btn.setText("⇄")
        self.swap_btn.clicked.connect(self.swap_languages)

        lang_row.addWidget(self.from_combo, 1)
        lang_row.addWidget(self.swap_btn)
        lang_row.addWidget(self.to_combo, 1)
        layout.addLayout(lang_row)

        self.select_defaults()

        # --- file list ---
        self.file_list = FileTree()
        self.file_list.setColumnCount(len(COLUMN_KEYS))
        self.file_list.setRootIsDecorated(False)  # flat list, no expand arrows
        self.file_list.setUniformRowHeights(True)
        self.file_list.setAllColumnsShowFocus(True)
        self.file_list.setSelectionMode(FileTree.ExtendedSelection)
        # sortable headers; -1 keeps insertion order until a header is clicked
        self.file_list.setSortingEnabled(True)
        self.file_list.sortByColumn(-1, Qt.AscendingOrder)
        header = self.file_list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(NAME_COL, QHeaderView.Stretch)
        header.setSectionResizeMode(TYPE_COL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(SIZE_COL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(MODIFIED_COL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(FOLDER_COL, QHeaderView.Interactive)
        header.setSectionResizeMode(STATUS_COL, QHeaderView.ResizeToContents)
        header.resizeSection(FOLDER_COL, 160)
        # right-click the header to choose which columns are shown
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_columns_menu)
        layout.addWidget(self.file_list, 1)

        self.total_label = QLabel()
        self.total_label.setStyleSheet("color: gray;")
        layout.addWidget(self.total_label)

        files_row = QHBoxLayout()
        self.add_btn = QPushButton()
        self.add_btn.clicked.connect(self.add_files)
        self.open_file_btn = QPushButton()
        self.open_file_btn.clicked.connect(self.open_selected)
        self.remove_btn = QPushButton()
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self.clear_files)
        # the same dialog the History menu opens; it is about past runs rather
        # than the loaded files, so it sits across the stretch from them
        self.history_btn = QPushButton()
        self.history_btn.clicked.connect(self.show_history_dialog)
        files_row.addWidget(self.add_btn)
        files_row.addWidget(self.open_file_btn)
        files_row.addWidget(self.remove_btn)
        files_row.addWidget(self.clear_btn)
        files_row.addStretch(1)
        files_row.addWidget(self.history_btn)
        layout.addLayout(files_row)

        # --- output folder ---
        self.output_dir = None
        out_row = QHBoxLayout()
        self.out_btn = QPushButton()
        self.out_btn.clicked.connect(self.choose_output_dir)
        self.output_label = QLabel()
        self.output_label.setStyleSheet("color: gray;")
        self.open_out_btn = QToolButton()
        self.open_out_btn.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.open_out_btn.setEnabled(False)
        self.open_out_btn.clicked.connect(self.open_output_dir)
        self.reset_out_btn = QToolButton()
        self.reset_out_btn.setText("×")
        self.reset_out_btn.clicked.connect(self.reset_output_dir)
        out_row.addWidget(self.out_btn)
        out_row.addWidget(self.output_label, 1)
        out_row.addWidget(self.open_out_btn)
        out_row.addWidget(self.reset_out_btn)
        layout.addLayout(out_row)

        # --- skip already-translated files ---
        self.skip_existing_cb = QCheckBox()
        self.skip_existing_cb.toggled.connect(
            lambda checked: QSettings().setValue("skip_existing", checked)
        )
        layout.addWidget(self.skip_existing_cb)

        # --- progress and action ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.clear_status_btn = QToolButton()
        self.clear_status_btn.setVisible(False)
        self.clear_status_btn.clicked.connect(self.clear_status)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.clear_status_btn, 0, Qt.AlignTop)
        layout.addLayout(status_row)

        action_row = QHBoxLayout()
        self.translate_btn = QPushButton()
        self.translate_btn.setDefault(True)
        self.translate_btn.clicked.connect(self.start_translation)
        self.cancel_btn = QPushButton()
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_translation)
        action_row.addWidget(self.translate_btn, 1)
        action_row.addWidget(self.cancel_btn, 1)
        layout.addLayout(action_row)

        self.setCentralWidget(central)

        self.engine_label = QLabel()
        self.engine_label.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(self.engine_label)

        # --- CPU/RAM usage of this process ---
        self.process = psutil.Process()
        self.process.cpu_percent()  # prime the counter; the first call is always 0
        self.resource_label = QLabel()
        self.resource_label.setStyleSheet("color: gray;")
        self.statusBar().addPermanentWidget(self.resource_label)
        self._resource_timer = QTimer(self)
        self._resource_timer.timeout.connect(self.update_resource_usage)
        self._resource_timer.start(2000)
        self.update_resource_usage()

        # every control that acts on something follows what is there to act
        # on. Connected here rather than where the widgets are built: these
        # fire on the way past and the handlers touch the whole window
        self.file_list.rows_changed.connect(self.refresh_file_buttons)
        self.file_list.rows_changed.connect(self._refresh_ready_state)
        self.file_list.itemSelectionChanged.connect(self.refresh_file_buttons)
        self.from_combo.currentIndexChanged.connect(self._refresh_ready_state)
        self.to_combo.currentIndexChanged.connect(self._refresh_ready_state)
        self.refresh_file_buttons()

        self.retranslate_ui()
        self.restore_settings()

    # --- settings persistence ---
    def restore_settings(self):
        settings = QSettings()
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        src = settings.value("from_lang", "")
        if src:  # empty = "Detect language" (index 0, already selected)
            self._select_language(self.from_combo, src, first=1)
        dst = settings.value("to_lang", "")
        if dst:
            self._select_language(self.to_combo, dst, first=0)
        out = settings.value("output_dir", "")
        if out and os.path.isdir(out):
            self.output_dir = out
            self.output_label.setText(out)
            self.output_label.setStyleSheet("")
            self.open_out_btn.setEnabled(True)
        self.skip_existing_cb.setChecked(
            settings.value("skip_existing", False, type=bool)
        )
        self.restore_columns()

    def _fill_radio_menu(self, menu, options, current, on_choose):
        """Fills a menu with a mutually exclusive set of checkable actions,
        one per (value, label) option, and returns them in order. An empty
        label is filled in later by retranslate_ui."""
        group = QActionGroup(self)  # parented, so it outlives this call
        group.setExclusive(True)
        actions = []
        for value, label in options:
            action = QAction(label, self, checkable=True)
            action.setChecked(value == current)
            action.triggered.connect(lambda _, v=value: on_choose(v))
            group.addAction(action)
            menu.addAction(action)
            actions.append(action)
        return actions

    def closeEvent(self, event):
        running = self.running_workers()
        if running:
            # waiting here (QThread.wait) would freeze the interface until the
            # current engine call returns, and giving up leaves a window that
            # refuses to close and looks hung: ask the threads to stop, keep
            # the window alive and close it from close_when_idle once they are
            # actually done
            self._closing = True
            for worker in running:
                worker.cancel()
            self._cancelling = True
            self.set_busy(True)
            self.cancel_btn.setEnabled(False)
            self.show_cancelling()
            event.ignore()
            return
        settings = QSettings()
        settings.setValue("geometry", self.saveGeometry())
        src = self.from_combo.currentData()
        settings.setValue("from_lang", src.code if src else "")
        dst = self.to_combo.currentData()
        settings.setValue("to_lang", dst.code if dst else "")
        settings.setValue("output_dir", self.output_dir or "")
        super().closeEvent(event)

    def close_when_idle(self):
        """Completes a close the user asked for while a thread was still
        running. Destroying a QThread that has not finished aborts the
        process, so the window only closes once every worker has stopped."""
        if not self._closing:
            return
        if self.running_workers():
            # the thread that just finished was not the last one (or Qt has
            # not flipped isRunning() yet): try again shortly
            QTimer.singleShot(50, self.close_when_idle)
            return
        self.close()

    def update_resource_usage(self):
        # normalized to total capacity so it reads like a system monitor
        # (100% = all cores busy) even when the engine uses every core
        cpu = self.process.cpu_percent() / (psutil.cpu_count() or 1)
        ram = self.process.memory_info().rss >> 20
        self.resource_label.setText(f"CPU {cpu:.0f}%  ·  RAM {ram} MB")

    # --- appearance ---
    def change_theme(self, name):
        QSettings().setValue("theme", name)
        apply_theme(name)

    # --- interface language ---
    def change_language(self, code):
        set_language(code)
        self.retranslate_ui()

    def retranslate_ui(self):
        """Applies the current language to all static texts."""
        self.setWindowTitle(tr("app_title"))
        self.file_menu.setTitle(tr("menu_file"))
        self.add_files_action.setText(tr("file_add_files"))
        self.add_folder_action.setText(tr("file_add_folder"))
        self.open_output_action.setText(tr("file_open_output"))
        self.quit_action.setText(tr("file_quit"))
        self.settings_menu.setTitle(tr("menu_settings"))
        self.prefs_menu.setTitle(tr("menu_preferences"))
        self.lang_menu.setTitle(tr("menu_language"))
        self.engine_menu.setTitle(tr("menu_engine"))
        self.nllb_action.setText(tr("engine_nllb"))
        self.threads_menu.setTitle(tr("menu_threads"))
        self.quality_menu.setTitle(tr("menu_quality"))
        for (_, key), action in zip(self._quality_options, self.quality_actions):
            action.setText(tr(key))
        self.theme_menu.setTitle(tr("menu_theme"))
        for (_, key), action in zip(self._theme_options, self.theme_actions):
            action.setText(tr(key))
        self.speed_menu.setTitle(tr("menu_speed_units"))
        for (_, key), action in zip(self._speed_options, self.speed_actions):
            action.setText(tr(key))
        self.pkg_install_action.setText(tr("pkg_install"))
        self.nllb_remove_action.setText(tr("nllb_remove"))
        self.cache_menu.setTitle(tr("menu_cache"))
        self.cache_enable_action.setText(tr("cache_enabled"))
        self.cache_ttl_menu.setTitle(tr("cache_ttl"))
        for (_, key), action in zip(self._cache_ttl_options, self.cache_ttl_actions):
            action.setText(tr(key))
        self.cache_clear_action.setText(tr("cache_clear"))
        self.refresh_cache_menu()
        self.history_menu.setTitle(tr("menu_history"))
        self.history_enable_action.setText(tr("hist_enabled"))
        self.history_ttl_menu.setTitle(tr("cache_ttl"))
        for (_, key), action in zip(self._history_ttl_options, self.history_ttl_actions):
            action.setText(tr(key))
        self.history_show_action.setText(tr("hist_show"))
        self.history_clear_action.setText(tr("hist_clear"))
        self.refresh_history_menu()
        self.update_engine_label()
        self.help_menu.setTitle(tr("menu_help"))
        self.manual_action.setText(tr("help_manual"))
        self.report_action.setText(tr("help_report"))
        self.about_action.setText(tr("about"))
        # the language names are translated too, so the combos are rebuilt
        # rather than just relabelled: the alphabetical order is another
        self.reload_language_combos()
        self.swap_btn.setToolTip(tr("swap_tooltip"))
        self.file_list.setHeaderLabels([tr(key) for key in COLUMN_KEYS])
        for i in range(self.file_list.topLevelItemCount()):
            item = self.file_list.topLevelItem(i)
            state = item.data(STATUS_COL, FILE_STATE_ROLE)
            if state:
                item.setText(STATUS_COL, tr(f"status_{state}"))
            self._render_cache_tooltip(item)
        self.file_list.retranslate()  # the empty-list placeholder inside it
        self.update_total_size()
        self._render_batch_cache_tooltip()
        self.add_btn.setText(tr("add"))
        self.open_file_btn.setText(tr("open"))
        self.open_file_btn.setToolTip(tr("open_tooltip"))
        self.remove_btn.setText(tr("remove"))
        self.clear_btn.setText(tr("clear"))
        self.history_btn.setText(tr("hist_show"))
        self.out_btn.setText(tr("output"))
        self.out_btn.setToolTip(tr("output_tooltip"))
        self.open_out_btn.setToolTip(tr("output_open_tooltip"))
        self.reset_out_btn.setToolTip(tr("output_reset_tooltip"))
        self.skip_existing_cb.setText(tr("skip_existing"))
        self.skip_existing_cb.setToolTip(tr("skip_existing_tooltip"))
        if self.output_dir is None:
            self.output_label.setText(tr("output_default"))
        self.clear_status_btn.setText(tr("clear_status"))
        self.clear_status_btn.setToolTip(tr("clear_status_tooltip"))
        self.translate_btn.setText(tr("translate"))
        self.cancel_btn.setText(tr("cancel"))
        if self.worker is not None and self.worker.isRunning():
            # re-render the live progress message in the new language
            self.refresh_status()
        else:
            self._refresh_ready_state()

    # --- help ---
    def show_manual(self):
        """Opens the project's README, which is the user manual. It lives
        online rather than in the application: translation runs offline,
        but reading the documentation is one of the few things that
        genuinely benefits from being the current version."""
        QDesktopServices.openUrl(QUrl(MANUAL_URL))

    def report_bug(self):
        QDesktopServices.openUrl(QUrl(ISSUES_URL))

    def show_about(self):
        AboutDialog(self.backend, len(self.languages), parent=self).exec_()
