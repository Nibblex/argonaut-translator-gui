import os
import re

import pytest
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QPushButton

from argonaut_cli.translation import SUPPORTED_EXTS

import argonaut_gui.window
from argonaut_gui.i18n import LANGUAGES, set_language, tr
from argonaut_gui.window import (
    FOLDER_COL,
    HIDEABLE_COLS,
    MODIFIED_COL,
    NAME_COL,
    SIZE_COL,
    STATUS_COL,
    TYPE_COL,
    MainWindow,
)
from argonaut_gui.worker import TranslateWorker
from tests.conftest import FakeLanguage, FakeTranslation


def fake_installed_languages(languages):
    """Mimics argostranslate's lru_cached get_installed_languages."""
    def get():
        return list(languages)

    get.cache_clear = lambda: None
    return get


@pytest.fixture
def langs(monkeypatch):
    english = FakeLanguage("en", "English")
    spanish = FakeLanguage("es", "Spanish")
    english._translations["es"] = FakeTranslation(english, spanish)
    monkeypatch.setattr(
        argonaut_gui.window.argostranslate.translate,
        "get_installed_languages",
        fake_installed_languages([english, spanish]),
    )
    return english, spanish


@pytest.fixture
def window(qtbot, langs):
    win = MainWindow()
    qtbot.addWidget(win)
    return win


def test_no_installed_languages_disables_translation(qtbot, monkeypatch):
    monkeypatch.setattr(
        argonaut_gui.window.argostranslate.translate,
        "get_installed_languages",
        fake_installed_languages([]),
    )
    win = MainWindow()
    qtbot.addWidget(win)
    assert not win.translate_btn.isEnabled()
    assert win.status.text() == tr("no_packages")


def test_defaults_detect_language_and_spanish_target(window):
    assert window.from_combo.currentIndex() == 0
    assert window.from_combo.currentData() is None
    assert window.to_combo.currentText() == "Spanish"


def test_add_paths_filters_and_deduplicates(window):
    window.add_paths(["/a/doc.txt", "/a/doc.txt", "/a/image.jpg", "/a/book.epub"])
    assert window.paths() == ["/a/doc.txt", "/a/book.epub"]


def test_remove_selected(window):
    window.add_paths(["/a/one.txt", "/a/two.txt"])
    window.file_list.topLevelItem(0).setSelected(True)
    window.remove_selected()
    assert window.paths() == ["/a/two.txt"]


def test_human_size():
    from argonaut_gui.window import human_size

    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(3 * 2**20) == "3.0 MB"
    assert human_size(2 * 2**30) == "2.0 GB"


def test_file_list_shows_individual_and_total_sizes(window, tmp_path):
    from argonaut_gui.window import human_size

    small = tmp_path / "small.txt"
    small.write_bytes(b"x" * 1024)  # 1 KB
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (2 * 2**20))  # 2 MB
    window.add_paths([str(small), str(big)])

    # name (bare file name) and size live in independent columns
    assert window.file_list.topLevelItem(0).text(NAME_COL) == "small.txt"
    assert window.file_list.topLevelItem(0).text(SIZE_COL) == "1.0 KB"
    assert window.file_list.topLevelItem(1).text(NAME_COL) == "big.txt"
    assert window.file_list.topLevelItem(1).text(SIZE_COL) == "2.0 MB"
    # total sums both, in a translated summary line
    assert window.total_label.text() == tr(
        "batch_total", count=2, size=human_size(1024 + 2 * 2**20)
    )

    window.file_list.topLevelItem(0).setSelected(True)
    window.remove_selected()
    assert window.total_label.text() == tr("batch_total", count=1, size="2.0 MB")
    window.clear_files()
    assert window.total_label.text() == ""  # cleared with the list


def test_name_column_shows_bare_name_and_folder_holds_the_path(window):
    window.add_paths(["/a/b/doc.txt"])
    item = window.file_list.topLevelItem(0)
    assert item.text(NAME_COL) == "doc.txt"
    assert item.text(FOLDER_COL) == "/a/b"
    # the full path is still available for opening/translating
    assert window.paths() == ["/a/b/doc.txt"]


def test_columns_can_be_hidden_and_the_choice_persists(window):
    from PyQt5.QtCore import QSettings

    # every column starts visible
    assert not any(window.file_list.isColumnHidden(c) for c in HIDEABLE_COLS)

    window.set_column_visible(FOLDER_COL, False)
    window.set_column_visible(SIZE_COL, False)
    assert window.file_list.isColumnHidden(FOLDER_COL)
    assert window.file_list.isColumnHidden(SIZE_COL)
    # the name column can never be hidden (not offered in the menu)
    assert NAME_COL not in HIDEABLE_COLS
    stored = set(QSettings().value("hidden_columns", "").split(","))
    assert stored == {"col_folder", "col_size"}

    # showing one again updates the stored set
    window.set_column_visible(SIZE_COL, True)
    assert not window.file_list.isColumnHidden(SIZE_COL)
    assert QSettings().value("hidden_columns", "") == "col_folder"


def test_hidden_columns_are_restored(qtbot, langs):
    from PyQt5.QtCore import QSettings

    QSettings().setValue("hidden_columns", "col_modified,col_status")
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.file_list.isColumnHidden(MODIFIED_COL)
    assert win.file_list.isColumnHidden(STATUS_COL)
    assert not win.file_list.isColumnHidden(TYPE_COL)


def test_columns_menu_lists_only_hideable_columns(window):
    from PyQt5.QtWidgets import QMenu

    window.set_column_visible(TYPE_COL, False)
    menus = []
    # exec_ would block; capture the menu the handler builds instead
    orig = QMenu.exec_
    QMenu.exec_ = lambda self, *a, **k: menus.append(self)
    try:
        window.show_columns_menu(window.file_list.header().rect().topLeft())
    finally:
        QMenu.exec_ = orig

    entries = {a.text(): a.isChecked() for a in menus[0].actions()}
    assert set(entries) == {tr("col_type"), tr("col_size"), tr("col_modified"),
                            tr("col_folder"), tr("col_status")}
    assert entries[tr("col_type")] is False  # reflects the hidden state


def test_header_click_sorts_by_name_and_size(window, tmp_path):
    from PyQt5.QtCore import Qt

    # three files whose alphabetical and size orders differ
    sizes = {"beta.txt": 3000, "alpha.txt": 1000, "gamma.txt": 2000}
    for name, size in sizes.items():
        (tmp_path / name).write_bytes(b"x" * size)
    window.add_paths([str(tmp_path / n) for n in sizes])

    def names():
        return [
            os.path.basename(window.file_list.topLevelItem(i).text(NAME_COL))
            for i in range(window.file_list.topLevelItemCount())
        ]

    # clicking a header sorts; sortByColumn is what the click triggers
    window.file_list.sortByColumn(NAME_COL, Qt.AscendingOrder)
    assert names() == ["alpha.txt", "beta.txt", "gamma.txt"]
    window.file_list.sortByColumn(NAME_COL, Qt.DescendingOrder)
    assert names() == ["gamma.txt", "beta.txt", "alpha.txt"]

    # the size column sorts numerically, not by its "3.0 KB" text
    window.file_list.sortByColumn(SIZE_COL, Qt.AscendingOrder)
    assert names() == ["alpha.txt", "gamma.txt", "beta.txt"]
    window.file_list.sortByColumn(SIZE_COL, Qt.DescendingOrder)
    assert names() == ["beta.txt", "gamma.txt", "alpha.txt"]
    # paths() follows the visible order after sorting
    assert [os.path.basename(p) for p in window.paths()] == names()


def test_metadata_columns_populate(window, tmp_path):
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    window.add_paths([str(doc)])

    item = window.file_list.topLevelItem(0)
    assert item.text(TYPE_COL) == "PDF"
    assert item.text(FOLDER_COL) == str(tmp_path)
    assert item.text(MODIFIED_COL)  # a formatted date, non-empty
    assert item.text(STATUS_COL) == ""  # blank until a translation runs


def test_sort_by_modified_is_numeric(window, tmp_path):
    import os as _os
    from PyQt5.QtCore import Qt

    older, newer = tmp_path / "older.txt", tmp_path / "newer.txt"
    older.write_text("a")
    newer.write_text("b")
    # make the modification times unambiguous regardless of write order
    _os.utime(older, (1000, 1000))
    _os.utime(newer, (2000, 2000))
    window.add_paths([str(newer), str(older)])

    window.file_list.sortByColumn(MODIFIED_COL, Qt.AscendingOrder)
    assert [_os.path.basename(p) for p in window.paths()] == ["older.txt", "newer.txt"]
    window.file_list.sortByColumn(MODIFIED_COL, Qt.DescendingOrder)
    assert [_os.path.basename(p) for p in window.paths()] == ["newer.txt", "older.txt"]


def test_status_column_sorts_by_state_rank(window):
    from PyQt5.QtCore import Qt

    window.add_paths(["/a/one.txt", "/a/two.txt", "/a/three.txt"])
    window._set_file_state("/a/one.txt", "failed")
    window._set_file_state("/a/two.txt", "pending")
    window._set_file_state("/a/three.txt", "done")

    # the column sorts by the state's rank in the batch lifecycle, not by
    # the translated label's alphabetical order
    window.file_list.sortByColumn(STATUS_COL, Qt.AscendingOrder)
    assert [os.path.basename(p) for p in window.paths()] == [
        "two.txt", "three.txt", "one.txt"  # pending < done < failed
    ]


def test_status_column_tracks_translation(window, qtbot, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    window.add_paths([str(doc)])
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(1)  # Spanish

    window.start_translation()
    # every queued file starts as "pending"
    assert window.file_list.topLevelItem(0).text(STATUS_COL) == tr("status_pending")
    qtbot.waitUntil(lambda: not window.worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: window.translate_btn.isEnabled(), timeout=5000)

    assert window.file_list.topLevelItem(0).text(STATUS_COL) == tr("status_done")
    # the outcome re-translates when the interface language changes
    window.change_language("es")
    assert window.file_list.topLevelItem(0).text(STATUS_COL) == "Hecho"


def test_cache_reuse_is_shown_per_file_and_for_the_batch(window):
    window.add_paths(["/a/one.txt", "/a/two.txt"])

    class FakeWorker:
        files = ["/a/one.txt", "/a/two.txt"]

        def isRunning(self):
            return False

    window.worker = FakeWorker()
    window._batch_reused = 0

    # first file reused nothing; its tooltip stays blank, no batch note yet
    window.on_file_cache_stats(0, 0, 0, 10)
    assert window._item_for_path("/a/one.txt").toolTip(STATUS_COL) == ""
    assert window.total_label.toolTip() == ""

    # second file reused 3 segments, lifting the batch total to 3
    window.on_file_cache_stats(1, 3, 3, 20)
    two = window._item_for_path("/a/two.txt")
    assert two.toolTip(STATUS_COL) == tr("cache_reused_file", count=3)
    assert window.total_label.toolTip() == tr("cache_reused_batch", count=3)

    # the tooltips follow an interface-language change
    window.change_language("es")
    assert two.toolTip(STATUS_COL) == "Reutilizados 3 segmentos de la caché"
    assert window.total_label.toolTip() == (
        "Caché: 3 segmentos reutilizados en el lote"
    )
    window.change_language("en")


def test_cancelling_marks_unfinished_files_as_cancelled(window):
    window.add_paths(["/a/one.txt", "/a/two.txt", "/a/three.txt"])
    # mid-batch: one finished, one in progress, one still queued
    window._set_file_state("/a/one.txt", "done")
    window._set_file_state("/a/two.txt", "translating")
    window._set_file_state("/a/three.txt", "pending")

    class FakeWorker:
        files = ["/a/one.txt", "/a/two.txt", "/a/three.txt"]

        def isRunning(self):
            return False

        def was_cancelled(self):
            return True

    window.worker = FakeWorker()
    window.results = []
    window.on_finished()

    def status(path):
        return window._item_for_path(path).text(STATUS_COL)

    assert status("/a/one.txt") == tr("status_done")  # a real outcome is kept
    assert status("/a/two.txt") == tr("status_cancelled")  # was stuck translating
    assert status("/a/three.txt") == tr("status_cancelled")  # never started


def test_cache_stays_manageable_while_disabled(window, monkeypatch):
    """Turning the cache off does not delete the file it already filled, so
    its expiry and the clear action have to keep working."""
    from PyQt5.QtCore import QSettings

    window.toggle_cache(False)
    window.refresh_cache_menu()

    assert window.cache_ttl_menu.isEnabled()
    assert window.cache_clear_action.isEnabled()

    window.change_cache_ttl(30)
    assert QSettings().value("cache_ttl_days", type=int) == 30

    purged = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    monkeypatch.setattr(
        argonaut_gui.window.TranslationCache, "purge_db",
        staticmethod(lambda path: purged.append(path) or 7),
    )
    window.clear_cache()
    assert len(purged) == 1
    assert tr("cache_cleared", count=7) in window.status.text()


def test_closing_mid_translation_never_blocks_the_interface(window, qtbot):
    """The close is deferred instead of waiting on the thread: an engine call
    can take seconds to return, and blocking there froze the whole window."""
    class SlowWorker:
        files = ["/a/doc.txt"]
        cancelled = False
        stopped = False

        def isRunning(self):
            return not self.stopped

        def cancel(self):
            self.cancelled = True

    worker = SlowWorker()
    window.worker = worker
    window.show()
    window.close()

    assert worker.cancelled  # the thread was asked to stop
    assert window._closing  # ...and the close is pending, not abandoned
    assert window.isVisible()  # the window outlives the still-running thread

    # nothing happens while the worker is still stopping
    window.close_when_idle()
    assert window.isVisible()

    # once it has really stopped, the window closes on its own
    worker.stopped = True
    window.close_when_idle()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=1000)


def test_cancelling_keeps_the_phase_next_to_the_message(window):
    """A cancelled engine call can take a minute to come back; showing which
    page it is finishing is what distinguishes waiting from a freeze."""
    class SlowWorker:
        files = ["/a/doc.pdf"]

        def isRunning(self):
            return True

        def cancel(self):
            pass

    window.worker = SlowWorker()
    window.on_phase_changed("generating", {"name": "doc.pdf", "done": 2, "total": 17})
    window.cancel_translation()

    text = window.status.text()
    assert tr("cancelling") in text
    assert tr("generating", name="doc.pdf", done=2, total=17) in text

    window.cancel_translation()  # a second click must not stack the message
    assert window.status.text().count(tr("cancelling")) == 1


def test_language_choice_is_locked_while_translating(window):
    """The batch keeps the pair it started with, so an editable combo would
    have the window claim a translation that is not the one running."""
    window.from_combo.setCurrentIndex(1)  # a source, so swapping applies at all
    window.set_busy(True)
    assert not window.from_combo.isEnabled()
    assert not window.to_combo.isEnabled()
    assert not window.swap_btn.isEnabled()

    window.set_busy(False)
    assert window.from_combo.isEnabled()
    assert window.to_combo.isEnabled()
    assert window.swap_btn.isEnabled()


def test_cancel_click_landing_after_the_batch_ends_is_ignored(window):
    """Otherwise the window stayed stuck on "cancelling…" forever: the
    summary had already been shown, so nothing reset the busy state."""
    class DoneWorker:
        files = ["/a/doc.txt"]
        cancelled = False

        def isRunning(self):
            return False

        def cancel(self):
            self.cancelled = True

    worker = DoneWorker()
    window.worker = worker
    window.cancel_translation()

    assert not worker.cancelled
    assert not window._cancelling
    assert window.status.text() != tr("cancelling")


def test_language_change_updates_the_live_status_mid_translation(window):
    class FakeWorker:
        files = ["/a/doc.txt"]
        cancelled = False

        def isRunning(self):
            return not self.cancelled

        def cancel(self):
            self.cancelled = True

    window.worker = FakeWorker()

    window.on_file_started(0, "/a/doc.txt")
    english = window.status.text()
    assert english == tr("translating", name="doc.txt", index=1, total=1)

    # switching language re-renders the message below the progress bar
    window.change_language("es")
    spanish = window.status.text()
    assert spanish == tr("translating", name="doc.txt", index=1, total=1)
    assert spanish != english  # it actually changed, not left stale

    # the detected-language suffix is re-rendered too
    window.on_language_detected(0, "English")
    window.change_language("en")
    assert window.status.text() == (
        f'{tr("translating", name="doc.txt", index=1, total=1)} — '
        f'{tr("detected", name="English")}'
    )

    # and so are the per-page phase messages, which keep the suffix
    window.on_phase_changed("generating", {"name": "doc.txt", "done": 2, "total": 5})
    window.change_language("es")
    assert window.status.text() == (
        f'{tr("generating", name="doc.txt", done=2, total=5)} — '
        f'{tr("detected", name="English")}'
    )


def test_the_detected_language_survives_the_phase_messages(window):
    class FakeWorker:
        files = ["/a/doc.pdf", "/a/other.pdf"]

        def isRunning(self):
            return True

        def cancel(self):
            pass

    window.worker = FakeWorker()

    window.on_file_started(0, "/a/doc.pdf")
    window.on_language_detected(0, "English")
    # the phases of a PDF start within milliseconds of the detection, so the
    # notice has to outlive them to be read at all
    for phase in ("reading", "translating_page", "generating"):
        window.on_phase_changed(phase, {"name": "doc.pdf", "done": 1, "total": 3})
        assert tr("detected", name="English") in window.status.text()

    window.on_phase_changed("saving", {"name": "doc.pdf"})
    assert window.status.text() == (
        f'{tr("saving", name="doc.pdf")} — {tr("detected", name="English")}'
    )

    # but it does not follow the batch on to the next file
    window.on_file_started(1, "/a/other.pdf")
    assert window.status.text() == tr(
        "translating", name="other.pdf", index=2, total=2
    )


def test_expand_dirs_walks_folders_recursively(window, tmp_path):
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "b.txt").write_text("x")
    (docs / "image.jpg").write_text("x")  # filtered later by add_paths
    (docs / "sub" / "a.epub").write_text("x")
    loose = tmp_path / "loose.txt"
    loose.write_text("x")

    window.add_paths(window.expand_dirs([str(docs), str(loose)]))
    assert window.paths() == [
        str(docs / "b.txt"),
        str(docs / "sub" / "a.epub"),
        str(loose),
    ]


def test_open_selected_files(window, monkeypatch, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello")
    other = tmp_path / "other.txt"
    other.write_text("bye")
    window.add_paths([str(doc), str(other), "/missing/gone.txt"])

    opened = []
    monkeypatch.setattr(
        argonaut_gui.window.QDesktopServices,
        "openUrl",
        staticmethod(lambda url: opened.append(url.toLocalFile())),
    )

    window.open_file_btn.click()  # nothing selected: nothing opens
    assert opened == []

    window.file_list.topLevelItem(0).setSelected(True)
    window.file_list.topLevelItem(2).setSelected(True)  # missing file: skipped
    window.open_file_btn.click()
    assert opened == [str(doc)]

    window.change_language("es")
    assert window.open_file_btn.text() == "Abrir"
    window.change_language("en")


def test_swap_languages(window):
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(1)  # Spanish
    window.swap_languages()
    assert window.from_combo.currentText() == "Spanish"
    assert window.to_combo.currentText() == "English"


def test_swap_does_nothing_on_detect_language(window):
    window.from_combo.setCurrentIndex(0)
    before = window.to_combo.currentIndex()
    window.swap_languages()
    assert window.from_combo.currentIndex() == 0
    assert window.to_combo.currentIndex() == before


def test_change_language_retranslates_ui(window):
    window.change_language("es")
    assert window.translate_btn.text() == "Traducir"
    assert window.windowTitle() == "Argonaut — Documentos"
    window.change_language("en")
    assert window.translate_btn.text() == "Translate"


def test_language_combos_follow_the_interface_language(window):
    assert window.from_combo.itemText(1) == "English"
    window.change_language("es")
    assert window.from_combo.itemText(0) == "Detectar idioma"
    assert [
        window.to_combo.itemText(i) for i in range(window.to_combo.count())
    ] == ["Español", "Inglés"]  # translated, and alphabetical in Spanish
    window.change_language("en")
    assert [
        window.to_combo.itemText(i) for i in range(window.to_combo.count())
    ] == ["English", "Spanish"]


def test_the_chosen_pair_survives_a_language_change(window):
    """The combos are rebuilt to retranslate them, so what the user picked
    has to be found again — by code, since the name has just changed."""
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(1)  # Spanish
    window.change_language("es")
    assert window.from_combo.currentText() == "Inglés"
    assert window.to_combo.currentText() == "Español"
    assert window.from_combo.currentData().code == "en"
    assert window.to_combo.currentData().code == "es"


def test_output_dir_choose_and_reset(window, tmp_path):
    window.output_dir = str(tmp_path)
    window.output_label.setText(str(tmp_path))
    window.reset_output_dir()
    assert window.output_dir is None
    assert window.output_label.text() == tr("output_default")


def test_output_dir_is_restored_between_windows(qtbot, langs, tmp_path):
    win = MainWindow()
    qtbot.addWidget(win)
    win.output_dir = str(tmp_path)
    win.close()

    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.output_dir == str(tmp_path)
    assert win2.output_label.text() == str(tmp_path)
    assert win2.open_out_btn.isEnabled()


def test_missing_output_dir_is_not_restored(qtbot, langs, tmp_path):
    from PyQt5.QtCore import QSettings

    QSettings().setValue("output_dir", str(tmp_path / "gone"))
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.output_dir is None  # a deleted folder falls back to the default
    assert win.output_label.text() == tr("output_default")


def test_open_output_dir_opens_only_a_chosen_folder(window, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(
        argonaut_gui.window.QDesktopServices,
        "openUrl",
        staticmethod(lambda url: opened.append(url.toLocalFile())),
    )
    window.open_output_dir()  # no folder chosen: nothing to open
    assert opened == []
    window.output_dir = str(tmp_path)
    window.open_output_dir()
    assert opened == [str(tmp_path)]


def url_mime(path):
    from PyQt5.QtCore import QMimeData, QUrl

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def drag_enter(window, mime):
    """Delivers a drag entering the window and returns the event, so a test
    can see whether it was taken."""
    from PyQt5.QtCore import QPoint, Qt
    from PyQt5.QtGui import QDragEnterEvent

    event = QDragEnterEvent(
        QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    window.dragEnterEvent(event)
    return event


def drop_on(window, mime):
    """Delivers the drop that follows the drag."""
    from PyQt5.QtCore import QPoint, Qt
    from PyQt5.QtGui import QDropEvent

    window.dropEvent(
        QDropEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    )


def test_drag_and_drop_adds_local_files(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    mime = url_mime(doc)

    assert drag_enter(window, mime).isAccepted()
    drop_on(window, mime)
    assert window.paths() == [str(doc)]


# --- the empty list ---

def test_the_empty_list_explains_itself_instead_of_heading_nothing(window):
    """An empty table is a blank box under a row of column headings that head
    no columns: until there is a file, the list carries an icon, the message
    and the formats it takes instead."""
    tree = window.file_list
    assert not tree.placeholder.isHidden()
    assert tree.isHeaderHidden()
    assert tree.hint.text() == tr("hint")
    assert not tree.drop_icon.pixmap().isNull()
    assert " ".join(SUPPORTED_EXTS) in tree.formats_hint.text()


def test_the_placeholder_gives_way_to_the_rows(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    assert window.file_list.placeholder.isHidden()
    assert not window.file_list.isHeaderHidden()


def test_the_placeholder_returns_with_the_last_file_removed(window, tmp_path):
    tree = window.file_list
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    tree.setCurrentItem(tree.topLevelItem(0))
    window.remove_selected()
    assert not tree.placeholder.isHidden()
    assert tree.isHeaderHidden()


def test_clearing_the_list_returns_the_placeholder(window, tmp_path):
    """clear() resets the model rather than removing the rows one by one,
    which reports through a different signal: the empty state follows both."""
    tree = window.file_list
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    assert tree.placeholder.isHidden()
    window.clear_files()
    assert not tree.placeholder.isHidden()
    assert tree.isHeaderHidden()


def test_the_placeholder_carries_no_button_of_its_own(window):
    """Adding files is the row of buttons below the list; the placeholder
    explains the empty box rather than duplicating one of them inside it."""
    tree = window.file_list
    assert not hasattr(tree, "add_btn")
    assert not tree.placeholder.findChildren(QPushButton)


def test_the_placeholder_follows_the_interface_language(window):
    set_language("es")
    window.retranslate_ui()
    tree = window.file_list
    assert tree.hint.text() == tr("hint") == "Arrastra archivos o carpetas aquí"
    assert tree.formats_hint.text() == tr(
        "hint_formats", formats=" ".join(SUPPORTED_EXTS)
    )


def test_a_short_list_sheds_its_parts_rather_than_clipping_the_message(window, qtbot):
    """The list is one widget among many and at the smallest window it is
    barely three rows tall. Centred in it, a placeholder that does not fit
    loses its top and bottom, cutting the message in half.

    Needs a shown window: a hidden one never lays its viewport out, so there
    is no height to fit anything to.
    """
    tree = window.file_list
    window.resize(900, 900)
    with qtbot.waitExposed(window):
        window.show()

    parts = (tree.drop_icon, tree.formats_hint)
    seen = []
    for height in (400, 150, 60):
        tree.resize(tree.width(), height)
        seen.append([not widget.isHidden() for widget in parts])
        assert not tree.hint.isHidden()  # the message itself never goes

    assert seen[0] == [True, True]  # room for the whole placeholder
    assert seen[-1] == [False, False]  # room for the message alone
    # each step only ever takes parts away, in that order, never puts them back
    for before, after in zip(seen, seen[1:]):
        assert all(was >= now for was, now in zip(before, after))


# --- drag feedback ---

def test_dragging_files_over_the_window_lights_up_the_list(window, tmp_path):
    """Without it the only sign that a drop will land is the cursor, which
    looks the same over a window that would refuse it."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    mime = url_mime(doc)

    assert drag_enter(window, mime).isAccepted()
    assert window.file_list._drag_active

    drop_on(window, mime)
    assert not window.file_list._drag_active


def test_the_highlight_goes_out_when_the_drag_leaves(window, tmp_path):
    """A drag carried back out of the window leaves nothing behind: without
    this the list stays lit until the next drop."""
    from PyQt5.QtGui import QDragLeaveEvent

    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    drag_enter(window, url_mime(doc))
    assert window.file_list._drag_active

    window.dragLeaveEvent(QDragLeaveEvent())
    assert not window.file_list._drag_active


def test_a_drag_carrying_no_files_does_not_light_the_list(window):
    """Dragging selected text over the window is not a drop it can take, so
    it must not promise one."""
    from PyQt5.QtCore import QMimeData

    mime = QMimeData()
    mime.setText("not a file")
    assert not drag_enter(window, mime).isAccepted()
    assert not window.file_list._drag_active


def test_the_highlight_is_actually_painted(window, qtbot, tmp_path):
    """The flag is only worth setting if it reaches the screen: with a drag
    overhead the list has to come out looking different. Needs a shown
    window, since a hidden one never lays its viewport out to paint."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    window.resize(800, 600)  # at the minimum size the list has no height to paint
    with qtbot.waitExposed(window):
        window.show()
    tree = window.file_list

    quiet = tree.grab().toImage()
    drag_enter(window, url_mime(doc))
    assert tree.grab().toImage() != quiet


# --- buttons follow what there is to act on ---

def test_an_empty_list_leaves_its_buttons_disabled(window):
    """Remove, Clear and Translate all act on files: with none loaded there is
    nothing for any of them to do, and offering them says otherwise."""
    assert not window.remove_btn.isEnabled()
    assert not window.open_file_btn.isEnabled()
    assert not window.clear_btn.isEnabled()
    assert not window.translate_btn.isEnabled()
    assert window.add_btn.isEnabled()  # the one way out of an empty list


def test_adding_a_file_enables_clear_and_translate_but_not_remove(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    assert window.clear_btn.isEnabled()
    assert window.translate_btn.isEnabled()
    assert not window.remove_btn.isEnabled()  # nothing selected yet
    assert not window.open_file_btn.isEnabled()


def test_selecting_a_row_enables_remove_and_open(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    tree = window.file_list
    tree.setCurrentItem(tree.topLevelItem(0))
    assert window.remove_btn.isEnabled()
    assert window.open_file_btn.isEnabled()
    tree.clearSelection()
    assert not window.remove_btn.isEnabled()
    assert not window.open_file_btn.isEnabled()


def test_emptying_the_list_disables_them_again(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    tree = window.file_list
    tree.setCurrentItem(tree.topLevelItem(0))
    window.clear_files()
    assert not window.clear_btn.isEnabled()
    assert not window.remove_btn.isEnabled()
    assert not window.translate_btn.isEnabled()


def test_translate_is_off_for_a_language_with_itself(window, tmp_path):
    """The pair is answered for before the run rather than after: pressing
    Translate only to be told the two are the same is a wasted press."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(0)  # English
    assert not window.translate_btn.isEnabled()


def test_translate_is_off_for_a_pair_with_no_model(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    window.from_combo.setCurrentIndex(2)  # Spanish: no es->en model registered
    window.to_combo.setCurrentIndex(0)  # English
    assert not window.translate_btn.isEnabled()
    window.from_combo.setCurrentIndex(1)  # English -> Spanish is installed
    window.to_combo.setCurrentIndex(1)
    assert window.translate_btn.isEnabled()


def test_detect_language_leaves_translate_on(window, tmp_path):
    """With the source detected per file the pair is not known until the file
    has been read, so the button cannot answer for it and must not pretend
    to; a file it turns out to have no model for fails as that file."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    window.from_combo.setCurrentIndex(0)  # Detect language
    window.to_combo.setCurrentIndex(0)  # English, which no installed pair reaches
    assert window.translate_btn.isEnabled()


def test_swap_is_off_while_the_source_is_detected(window):
    """There is no source language to move across, and the button was already
    a no-op there: it read as broken rather than as inapplicable."""
    assert window.from_combo.currentIndex() == 0  # Detect language
    assert not window.swap_btn.isEnabled()

    window.from_combo.setCurrentIndex(1)  # English
    assert window.swap_btn.isEnabled()

    window.from_combo.setCurrentIndex(0)
    assert not window.swap_btn.isEnabled()


def test_the_status_says_why_translate_is_off(window, tmp_path):
    """A greyed-out button with "Ready." under it is the window refusing
    without saying why. Each reason it refuses for names itself, and the
    strings are the ones the pre-flight dialogs already use."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")

    assert window.status.text() == tr("no_files_msg")

    window.add_paths([str(doc)])
    assert window.status.text() == tr("ready")

    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(0)  # English
    assert window.status.text() == tr("same_language")

    window.from_combo.setCurrentIndex(2)  # Spanish: no es->en model registered
    assert window.status.text() == tr("no_model_msg", src="Spanish", dst="English")

    window.from_combo.setCurrentIndex(1)  # English -> Spanish is installed
    window.to_combo.setCurrentIndex(1)
    assert window.status.text() == tr("ready")


def test_the_reason_and_the_button_cannot_disagree(window, tmp_path):
    """Both read the same answer, so a disabled button always has a reason
    and an enabled one never shows a complaint."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    for step in (
        lambda: None,
        lambda: window.add_paths([str(doc)]),
        lambda: window.from_combo.setCurrentIndex(1),
        lambda: window.to_combo.setCurrentIndex(0),
        lambda: window.from_combo.setCurrentIndex(2),
        lambda: window.clear_files(),
    ):
        step()
        blocked = window.translate_blocker() is not None
        assert window.translate_btn.isEnabled() is not blocked
        assert (window.status.text() != tr("ready")) is blocked


@pytest.fixture
def pivot_window(qtbot, monkeypatch):
    """A window whose Albanian reaches Spanish only through English, which is
    what Argos hands back with the usual en↔X packages and no sq→es one."""
    from argostranslate.translate import CompositeTranslation

    english = FakeLanguage("en", "English")
    spanish = FakeLanguage("es", "Spanish")
    albanian = FakeLanguage("sq", "Albanian")
    english._translations["es"] = FakeTranslation(english, spanish)
    albanian._translations["en"] = FakeTranslation(albanian, english)
    albanian._translations["es"] = CompositeTranslation(
        albanian.get_translation(english), english.get_translation(spanish)
    )
    monkeypatch.setattr(
        argonaut_gui.window.argostranslate.translate,
        "get_installed_languages",
        fake_installed_languages([albanian, english, spanish]),
    )
    win = MainWindow()
    qtbot.addWidget(win)
    win.add_paths(["/a/doc.txt"])
    return win


def test_a_pair_reached_through_a_third_language_says_so(pivot_window):
    """Argos composes a pair it has no package for out of two that it has, so
    the text is translated twice. Left unsaid, the detour reads as the engine
    being bad at the language rather than as a hop nobody mentioned."""
    win = pivot_window
    win.from_combo.setCurrentIndex(1)  # Albanian
    win.to_combo.setCurrentIndex(2)  # Spanish
    assert win.translate_btn.isEnabled()  # it does translate: a note, not a bar
    assert win.status.text() == tr(
        "pivot_pair", src="Albanian", dst="Spanish", via="English"
    )


def test_a_direct_pair_says_nothing(pivot_window):
    win = pivot_window
    win.from_combo.setCurrentIndex(2)  # English
    win.to_combo.setCurrentIndex(2)  # Spanish: a package of its own
    assert win.translate_btn.isEnabled()
    assert win.status.text() == tr("ready")


def test_detected_source_cannot_be_told_it_pivots(pivot_window):
    """The pair is not known until each file is read, so there is no detour
    to warn about yet."""
    win = pivot_window
    win.from_combo.setCurrentIndex(0)  # Detect language
    win.to_combo.setCurrentIndex(2)  # Spanish
    assert win.status.text() == tr("ready")


def test_a_blocked_pair_reports_that_rather_than_the_detour(pivot_window):
    """A reason it cannot run outranks a caveat about how it would."""
    win = pivot_window
    win.from_combo.setCurrentIndex(1)  # Albanian
    win.to_combo.setCurrentIndex(2)  # Spanish, which it reaches through English
    assert win.status.text() != tr("ready")
    win.clear_files()
    assert win.status.text() == tr("no_files_msg")


def test_no_packages_outranks_the_rest(window, monkeypatch, tmp_path):
    """With nothing installed, telling the user to add a document sends them
    the wrong way: the packages are what is missing."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    window.languages = []
    window._refresh_ready_state()
    assert not window.translate_btn.isEnabled()
    assert window.status.text() == tr("no_packages")


def test_clearing_a_summary_restores_the_reason_not_ready(window):
    """The button that dismisses a finished batch's summary used to write
    "Ready." over it whatever the window could actually do."""
    window.status.setText("a summary")
    window.clear_status_btn.setVisible(True)
    window.clear_status()
    assert window.clear_status_btn.isHidden()
    assert window.status.text() == tr("no_files_msg")  # the list is empty


def test_finishing_a_run_does_not_switch_translate_back_on_blindly(window, tmp_path):
    """Leaving the busy state used to enable it regardless; if the files went
    away while the batch ran there is nothing left for it to do."""
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    window.add_paths([str(doc)])
    assert window.translate_btn.isEnabled()

    window.set_busy(True)
    assert not window.translate_btn.isEnabled()
    window.clear_files()
    window.set_busy(False)
    assert not window.translate_btn.isEnabled()


def test_the_drop_icon_is_enlarged_when_the_style_ships_a_small_one():
    """QIcon.pixmap() never enlarges beyond the largest variant a style has,
    so a style offering only a 16-pixel folder would otherwise leave a
    16-pixel icon adrift in the middle of the placeholder."""
    from PyQt5.QtGui import QIcon, QPixmap

    from argonaut_gui.window.file_list import DROP_ICON_SIZE, drop_pixmap

    class TinyIconStyle:
        def standardIcon(self, which):
            small = QPixmap(16, 16)
            small.fill()
            return QIcon(small)

    assert drop_pixmap(TinyIconStyle(), DROP_ICON_SIZE).width() == DROP_ICON_SIZE


def test_each_translation_releases_the_previous_worker(window, qtbot, tmp_path):
    """Worker threads are children of the window: keeping every finished one
    would pile up a file list and a batch cache per translation, for as long
    as the session lasts."""
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    window.add_paths([str(doc)])
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(1)  # Spanish

    window.start_translation()
    first = window.worker
    qtbot.waitUntil(lambda: not first.isRunning(), timeout=5000)

    window.start_translation()
    assert window.worker is not first
    qtbot.waitUntil(
        lambda: len(window.findChildren(TranslateWorker)) == 1, timeout=5000
    )
    qtbot.waitUntil(lambda: not window.worker.isRunning(), timeout=5000)


def test_translation_requires_files(window, monkeypatch):
    boxes = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args: boxes.append(args[2])
    )
    window.start_translation()
    assert boxes == [tr("no_files_msg")]


def test_translation_rejects_same_language(window, monkeypatch, langs):
    english, _ = langs
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: boxes.append(args[2]))
    window.add_paths(["/a/doc.txt"])
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(0)  # English
    window.start_translation()
    assert boxes == [tr("same_language")]


def test_translation_rejects_missing_model(window, monkeypatch):
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: boxes.append(args[2]))
    window.add_paths(["/a/doc.txt"])
    window.from_combo.setCurrentIndex(2)  # Spanish: no es->en model registered
    window.to_combo.setCurrentIndex(0)  # English
    window.start_translation()
    assert len(boxes) == 1
    assert "Spanish" in boxes[0] and "English" in boxes[0]


def test_late_signals_are_ignored_while_cancelling(window):
    class FakeWorker:
        files = ["/a/doc.txt"]

        def isRunning(self):
            return True

        def cancel(self):
            pass

    window.worker = FakeWorker()
    window.cancel_translation()
    before = window.status.text()
    # signals already queued when the user cancelled must not overwrite
    # the "cancelling…" message
    window.on_file_started(0, "/a/doc.txt")
    window.on_phase_changed("generating", {"name": "doc.txt", "done": 1, "total": 2})
    assert window.status.text() == before


def test_finished_file_notes_its_detected_language(window):
    class FakeWorker:
        files = ["/a/doc.txt"]

        def isRunning(self):
            return False

    window.worker = FakeWorker()
    window.results = []
    window.detected = {}
    window.add_paths(["/a/doc.txt"])
    window.on_language_detected(0, "English")
    window.on_file_done(0, "/a/doc_es.txt", 2.0)
    kind, text = window.results[-1]
    assert kind == "ok"
    assert "/a/doc_es.txt" in text
    assert tr("detected", name="English") in text


def test_failed_file_lands_in_the_results_and_the_status_column(window):
    class FakeWorker:
        files = ["/a/doc.txt"]

        def isRunning(self):
            return False

    window.worker = FakeWorker()
    window.results = []
    window.add_paths(["/a/doc.txt"])
    window.on_file_failed(0, "boom")
    assert window.results == [("error", "boom")]
    assert window._item_for_path("/a/doc.txt").text(STATUS_COL) == tr("status_failed")


def test_finished_summary_lists_results_and_errors(window):
    window.worker = None
    window.add_paths(["/a/doc.txt"])  # a batch that finished had files in it
    window.results = [
        ("ok", "/a/doc_es.txt"),
        ("skipped", "/a/done_es.txt"),
        ("error", "boom"),
    ]

    class DoneWorker:
        def isRunning(self):
            return False

        def was_cancelled(self):
            return False

    window.worker = DoneWorker()
    window.on_finished()
    text = window.status.text()
    assert "/a/doc_es.txt" in text
    assert tr("skipped_header") in text and "/a/done_es.txt" in text
    assert "boom" in text
    assert not window.clear_status_btn.isHidden()
    window.clear_status()
    assert window.status.text() == tr("ready")
    assert window.clear_status_btn.isHidden()


def test_finished_summary_reports_how_much_the_cache_answered(window):
    class DoneWorker:
        files = ["/a/doc.txt"]

        def isRunning(self):
            return False

        def was_cancelled(self):
            return False

    window.worker = DoneWorker()
    window.add_paths(["/a/doc.txt"])
    window.results = [("ok", "/a/doc_es.txt")]
    window.on_file_cache_stats(0, 120, 120, 400)
    window.on_finished()

    expected = tr("cache_reused_summary", reused=120, total=400, percent=30)
    assert window.status.text().endswith(expected)  # last line of the summary
    assert "120" in expected and "400" in expected and "30" in expected


def test_the_cache_line_is_left_out_when_nothing_was_reused(window):
    """A first run, or one with the cache switched off, would otherwise end
    on a "0 of 400" line that says nothing."""
    class DoneWorker:
        files = ["/a/doc.txt"]

        def isRunning(self):
            return False

        def was_cancelled(self):
            return False

    window.worker = DoneWorker()
    window.add_paths(["/a/doc.txt"])
    window.results = [("ok", "/a/doc_es.txt")]
    window.on_file_cache_stats(0, 0, 0, 400)
    window.on_finished()

    assert window.cache_summary_line() is None
    assert window.status.text() == f'{tr("translated_header")}\n  → /a/doc_es.txt'


def test_a_cancelled_run_still_reports_its_cache_reuse(window):
    """The segments it did reuse are as real as the pages it produced."""
    class CancelledWorker:
        files = ["/a/doc.txt"]

        def isRunning(self):
            return False

        def was_cancelled(self):
            return True

    window.worker = CancelledWorker()
    window.add_paths(["/a/doc.txt"])
    window.results = []
    window.on_file_cache_stats(0, 7, 7, 21)
    window.on_finished()

    text = window.status.text()
    assert text.startswith(tr("cancelled"))
    assert text.endswith(tr("cache_reused_summary", reused=7, total=21, percent=33))


def test_the_cache_summary_follows_the_interface_language(window):
    window._batch_reused, window._batch_segments = 50, 200
    assert window.cache_summary_line() == tr(
        "cache_reused_summary", reused=50, total=200, percent=25
    )
    window.change_language("es")
    assert window.cache_summary_line() == (
        "Caché: 50 de 200 segmentos reutilizados (25 %)"
    )
    window.change_language("en")


def test_a_new_batch_forgets_the_previous_run_cache_figures(window, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello")
    window._batch_reused, window._batch_segments = 50, 200
    window.add_paths([str(doc)])

    window.start_translation()
    window.worker.cancel()
    window.worker.wait(5000)
    assert (window._batch_reused, window._batch_segments) == (0, 0)


def test_skip_existing_setting_persists_and_reaches_the_worker(qtbot, langs, tmp_path):
    win = MainWindow()
    qtbot.addWidget(win)
    assert not win.skip_existing_cb.isChecked()  # default: overwrite
    win.skip_existing_cb.setChecked(True)
    win.close()

    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.skip_existing_cb.isChecked()

    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    out = tmp_path / "doc_es.txt"
    out.write_text("already there")
    win2.add_paths([str(doc)])
    win2.from_combo.setCurrentIndex(1)  # English
    win2.to_combo.setCurrentIndex(1)  # Spanish
    win2.start_translation()
    qtbot.waitUntil(lambda: not win2.worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: win2.translate_btn.isEnabled(), timeout=5000)

    assert out.read_text() == "already there"  # skipped, not overwritten
    assert tr("skipped_header") in win2.status.text()
    assert win2.file_list.topLevelItem(0).text(STATUS_COL) == tr("status_skipped")


def test_add_files_dialog(window, monkeypatch, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(doc)], ""))
    )
    window.add_files()
    assert window.paths() == [str(doc)]


def test_choose_output_dir_dialog(window, monkeypatch, tmp_path):
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path))
    )
    window.choose_output_dir()
    assert window.output_dir == str(tmp_path)
    assert window.output_label.text() == str(tmp_path)


def test_full_translation_through_the_window(window, qtbot, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    window.add_paths([str(doc)])
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(1)  # Spanish

    window.start_translation()
    qtbot.waitUntil(lambda: not window.worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: window.translate_btn.isEnabled(), timeout=5000)

    out = tmp_path / "doc_es.txt"
    assert out.exists()
    assert "HELLO WORLD" in out.read_text()
    assert str(out) in window.status.text()
    assert re.search(r"\(\d{2}:\d{2}\)", window.status.text())


def test_format_duration():
    assert MainWindow.format_duration(0) == "00:00"
    assert MainWindow.format_duration(65.7) == "01:05"
    assert MainWindow.format_duration(3599) == "59:59"
    assert MainWindow.format_duration(3600) == "1h"
    assert MainWindow.format_duration(3671) == "1h 01m"
    assert MainWindow.format_duration(2 * 3600 + 35 * 60) == "2h 35m"
    assert MainWindow.format_duration(24 * 3600) == "1d"
    assert MainWindow.format_duration(24 * 3600 + 3 * 3600 + 59 * 60) == "1d 3h"
    assert MainWindow.format_duration(72 * 3600) == "3d"


def test_eta_estimation(window, monkeypatch):
    t = [100.0]
    monkeypatch.setattr(argonaut_gui.window.time, "monotonic", lambda: t[0])

    assert window.estimate_eta(0, 100) is None  # first sample only calibrates
    t[0] += 10
    assert window.estimate_eta(20, 100) == pytest.approx(40.0)  # 2/s, 80 left
    t[0] += 10
    assert window.estimate_eta(40, 100) == pytest.approx(30.0)
    assert window.estimate_eta(100, 100) is None  # finished: nothing left
    assert window.estimate_eta(0, 7) is None  # new total resets the estimator
    t[0] += 0.5
    assert window.estimate_eta(3, 7) is None  # too early to be reliable

    window.reset_eta()
    t[0] += 10
    assert window.estimate_eta(50, 100) is None  # reset discards the old pace


def test_progress_format_includes_eta(window, monkeypatch):
    t = [0.0]
    monkeypatch.setattr(argonaut_gui.window.time, "monotonic", lambda: t[0])
    window.on_progress(0, 100)
    assert window.progress.format() == "%p% — 0/100"  # no estimate yet
    t[0] += 60
    window.on_progress(50, 100)
    assert window.progress.format() == "%p% — 50/100 — ~01:00"
    window.on_progress(0, 0)  # indeterminate phase resets the estimator
    t[0] += 60
    window.on_progress(50, 100)
    assert window.progress.format() == "%p% — 50/100"


def test_backend_switch_and_persistence(qtbot, langs, monkeypatch):
    fake_langs = [
        FakeLanguage("en", "English"),
        FakeLanguage("es", "Spanish"),
        FakeLanguage("ja", "Japanese"),
    ]
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: fake_langs,
    )

    win = MainWindow()
    qtbot.addWidget(win)
    assert win.backend == "argos"
    assert win.argos_action.isChecked()

    win.change_backend("nllb")
    assert win.backend == "nllb"
    assert win.nllb_action.isChecked()
    assert win.from_combo.count() == 4  # detect + 3 languages
    assert win.to_combo.currentText() == "Spanish"
    win.close()

    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.backend == "nllb"
    assert win2.nllb_action.isChecked()
    assert win2.from_combo.count() == 4


def test_backend_switch_keeps_language_selection(window, monkeypatch):
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: [
            FakeLanguage("en", "English"),
            FakeLanguage("es", "Spanish"),
        ],
    )
    window.from_combo.setCurrentIndex(1)  # English
    window.to_combo.setCurrentIndex(1)  # Spanish
    window.change_backend("nllb")
    assert window.from_combo.currentText() == "English"
    assert window.to_combo.currentText() == "Spanish"


def test_nllb_offers_every_pair_it_speaks(qtbot, monkeypatch):
    """NLLB-200 is one model that translates between any two of its languages
    rather than a model per pair, so nothing can be missing for it.

    That falls out of asking the engine — its get_translation answers for
    every pair — rather than of a special case on the backend's name, which
    is what a third engine would have to be added to.
    """
    from PyQt5.QtCore import QSettings

    # the engine loads lazily, so its languages exist without a model on disk
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    QSettings().setValue("backend", "nllb")
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.backend == "nllb"
    win.add_paths(["/a/doc.txt"])

    sources = win.from_combo.count() - 1  # minus "Detect language"
    assert sources > 20, "expected the full NLLB set, not a stand-in"
    for fi in range(1, win.from_combo.count()):
        win.from_combo.setCurrentIndex(fi)
        for ti in range(win.to_combo.count()):
            win.to_combo.setCurrentIndex(ti)
            same = win.from_combo.currentData() is win.to_combo.currentData()
            # the one pair that stays off is a language with itself, which no
            # engine makes meaningful
            assert win.translate_btn.isEnabled() is not same
            assert (win.status.text() == tr("same_language")) is same


def test_switching_to_nllb_unblocks_a_pair_argos_has_no_model_for(window, monkeypatch):
    """The pair the Argos packages cannot serve is one NLLB translates like
    any other, so changing engine has to lift the refusal with it."""
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    window.add_paths(["/a/doc.txt"])
    window.from_combo.setCurrentIndex(2)  # Spanish: no es->en package
    window.to_combo.setCurrentIndex(0)  # English
    assert not window.translate_btn.isEnabled()

    window.apply_backend("nllb")
    assert window.from_combo.currentText() == "Spanish"  # the pair is kept
    assert window.to_combo.currentText() == "English"
    assert window.translate_btn.isEnabled()
    assert window.status.text() == tr("ready")

    window.apply_backend("argos")
    assert not window.translate_btn.isEnabled()


def test_backend_falls_back_to_argos_without_model(qtbot, langs, monkeypatch):
    from PyQt5.QtCore import QSettings

    QSettings().setValue("backend", "nllb")
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: False
    )
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.backend == "argos"


def test_backend_download_declined_keeps_argos(window, monkeypatch):
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: False
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )
    window.change_backend("nllb")
    assert window.backend == "argos"
    assert window.argos_action.isChecked()


def test_backend_download_flow(window, monkeypatch, qtbot):
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: False
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )

    def fake_download(path=None, base_url=None, on_progress=None, is_cancelled=None):
        on_progress(2 * 2**20, 4 * 2**20)

    monkeypatch.setattr(argonaut_gui.window.nllb, "download_model", fake_download)
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: [
            FakeLanguage("en", "English"),
            FakeLanguage("es", "Spanish"),
        ],
    )

    window.add_paths(["/a/doc.txt"])  # Translate needs something to translate
    window.change_backend("nllb")
    qtbot.waitUntil(lambda: window.backend == "nllb", timeout=5000)
    qtbot.waitUntil(lambda: window.translate_btn.isEnabled(), timeout=5000)
    assert window.nllb_action.isChecked()
    assert window.to_combo.currentText() == "Spanish"


def test_ready_state_is_left_alone_while_a_worker_runs(window):
    class RunningWorker:
        def isRunning(self):
            return True

        def cancel(self):
            pass

    window.worker = RunningWorker()
    window.set_busy(True)
    window._refresh_ready_state()  # e.g. packages changed mid-translation
    assert not window.translate_btn.isEnabled()  # the busy state stays owner
    window.worker = None  # let the teardown close a worker-free window


def test_same_backend_choice_is_a_no_op(window, monkeypatch):
    applied = []
    monkeypatch.setattr(window, "apply_backend", lambda name: applied.append(name))
    window.change_backend("argos")  # already active
    assert applied == []


def test_same_cpu_thread_count_is_a_no_op(window, monkeypatch):
    reloaded = []
    monkeypatch.setattr(
        window, "reload_language_combos", lambda: reloaded.append(True)
    )
    window.change_cpu_threads(window.cpu_threads)
    assert reloaded == []  # no pointless engine reload


def test_cancel_button_cancels_a_running_model_download(window):
    class FakeDownloader:
        cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

    window.downloader = FakeDownloader()
    window.cancel_translation()
    assert window.downloader.cancelled
    assert not window.cancel_btn.isEnabled()
    assert window.status.text() == tr("cancelling")


def test_model_download_failure_warns_and_keeps_argos(window, monkeypatch):
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args: warned.append(args[2]))
    )
    window.on_download_finished(False, "boom")
    assert window.backend == "argos"
    assert window.argos_action.isChecked()
    assert warned == [tr("download_failed", error="boom")]


def test_model_download_progress_shows_the_speed(window):
    window.on_download_progress(2, 4, 2 * 2**20)
    assert window.progress.format() == "%p% — 2/4 MB — 16.8 Mbps"
    # while the speed is still unknown the label shows only the size
    window.on_download_progress(3, 4, 0.0)
    assert window.progress.format() == "%p% — 3/4 MB"


def test_speed_units_menu_defaults_to_bits_and_applies(window):
    from PyQt5.QtCore import QSettings

    actions = window.speed_menu.actions()
    assert window.speed_menu.title() == tr("menu_speed_units")
    assert [a.text() for a in actions] == [
        tr("speed_units_bits"), tr("speed_units_bytes")
    ]
    assert [a.isChecked() for a in actions] == [True, False]

    window.change_speed_units("bytes")
    assert QSettings().value("speed_units") == "bytes"
    window.on_download_progress(2, 4, 2 * 2**20)
    assert window.progress.format() == "%p% — 2/4 MB — 2.0 MB/s"

    window.change_speed_units("bits")
    window.on_download_progress(2, 4, 2 * 2**20)
    assert window.progress.format() == "%p% — 2/4 MB — 16.8 Mbps"


def test_new_model_download_releases_the_finished_downloader(window, monkeypatch, qtbot):
    from argonaut_cli.translation import CancelledError

    def cancelled_download(path=None, base_url=None, on_progress=None,
                           is_cancelled=None):
        raise CancelledError()

    monkeypatch.setattr(argonaut_gui.window.nllb, "download_model", cancelled_download)
    window.start_model_download()
    first = window.downloader
    qtbot.waitUntil(lambda: not first.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: not window.cancel_btn.isVisible(), timeout=5000)
    assert window.status.text() == tr("cancelled")

    # a second download replaces the finished thread instead of piling up
    window.start_model_download()
    assert window.downloader is not first
    qtbot.waitUntil(lambda: not window.downloader.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: not window.cancel_btn.isVisible(), timeout=5000)


def test_package_dialog_opens_and_refreshes_on_changes(window, monkeypatch):
    from PyQt5.QtCore import QObject, pyqtSignal

    class FakeDialog(QObject):
        packages_changed = pyqtSignal()
        instances = []

        def __init__(self, parent=None):
            super().__init__()
            FakeDialog.instances.append(self)

        def exec_(self):
            self.packages_changed.emit()  # as if the user installed something

    # patched in the module that instantiates it (show_package_dialog)
    monkeypatch.setattr(argonaut_gui.window.engine, "PackageDialog", FakeDialog)
    refreshed = []
    monkeypatch.setattr(window, "on_packages_changed", lambda: refreshed.append(True))
    window.show_package_dialog()
    assert len(FakeDialog.instances) == 1
    assert refreshed == [True]


def test_history_menu_defaults_to_recording(window):
    from PyQt5.QtCore import QSettings

    assert window.history_enabled()
    assert window.history_enable_action.isChecked()
    assert window.history_ttl_actions[2].isChecked()  # 90 days, like the cache

    window.toggle_history(False)
    assert QSettings().value("history_enabled", type=bool) is False
    window.change_history_ttl(30)
    assert QSettings().value("history_ttl_days", type=int) == 30


def test_history_menu_shows_the_live_size(window):
    from argonaut_cli.history import TranslationHistory

    history = TranslationHistory(TranslationHistory.default_db_path(), ttl_days=0)
    history.record("/docs/a.txt", "/docs/a_es.txt", "en", "es", "argos", 1.0)
    history.close()

    window.refresh_history_menu()
    assert "1 entries" in window.history_size_action.text()
    assert not window.history_size_action.isEnabled()  # a read-only label


def test_history_stays_manageable_while_disabled(window, monkeypatch):
    """Turning recording off does not delete what was already recorded, so
    viewing, expiry and clearing have to keep working."""
    window.toggle_history(False)
    window.refresh_history_menu()

    assert window.history_ttl_menu.isEnabled()
    assert window.history_clear_action.isEnabled()
    assert window.history_show_action.isEnabled()

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    purged = []
    monkeypatch.setattr(
        argonaut_gui.window.TranslationHistory, "purge_db",
        staticmethod(lambda path: purged.append(path) or 4),
    )
    window.clear_history()
    assert len(purged) == 1
    assert tr("hist_cleared", count=4) in window.status.text()


def test_clear_history_declined_keeps_it(window, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )
    purged = []
    monkeypatch.setattr(
        argonaut_gui.window.TranslationHistory, "purge_db",
        staticmethod(lambda path: purged.append(path)),
    )
    window.clear_history()
    assert purged == []


def test_history_dialog_opens_and_refreshes_the_menu(window, monkeypatch):
    from argonaut_gui.window.history_dialog import HistoryDialog

    opened = []
    monkeypatch.setattr(HistoryDialog, "exec_", lambda self: opened.append(self))
    refreshed = []
    monkeypatch.setattr(
        window, "refresh_history_menu", lambda: refreshed.append(True)
    )
    window.show_history_dialog()

    assert len(opened) == 1
    # clearing from inside the dialog refreshes the menu, and so does closing
    opened[0].history_cleared.emit()
    assert len(refreshed) == 2


def test_history_menu_is_translated(window):
    window.change_language("es")
    assert window.history_menu.title() == "H&istorial"
    assert window.history_enable_action.text() == "Registrar historial"
    assert window.history_show_action.text() == "Ver historial…"
    assert window.history_clear_action.text() == "Borrar historial…"
    window.change_language("en")


def test_the_history_button_opens_the_same_dialog_as_the_menu(window, monkeypatch):
    from argonaut_gui.window.history_dialog import HistoryDialog

    opened = []
    monkeypatch.setattr(HistoryDialog, "exec_", lambda self: opened.append(self))
    assert window.history_btn.text() == tr("hist_show")

    window.history_btn.click()
    assert len(opened) == 1

    # past runs stay readable with recording switched off, and while one is
    # under way: the dialog only reads the database
    window.history_enable_action.setChecked(False)
    window.set_busy(True)
    assert window.history_btn.isEnabled()
    window.set_busy(False)

    window.change_language("es")
    assert window.history_btn.text() == "Ver historial…"
    window.change_language("en")


def test_clear_cache_declined_leaves_the_cache_alone(window, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )
    purged = []
    monkeypatch.setattr(
        argonaut_gui.window.TranslationCache, "purge_db",
        staticmethod(lambda path: purged.append(path)),
    )
    window.clear_cache()
    assert purged == []


def test_help_menu_offers_the_manual_the_bug_report_and_about(window, monkeypatch):
    from argonaut_gui.window.about_dialog import ISSUES_URL, MANUAL_URL

    opened = []
    monkeypatch.setattr(
        argonaut_gui.window.QDesktopServices, "openUrl",
        staticmethod(lambda url: opened.append(url.toString())),
    )
    entries = [a.text() for a in window.help_menu.actions() if not a.isSeparator()]
    assert entries == [tr("help_manual"), tr("help_report"), tr("about")]

    window.show_manual()
    window.report_bug()
    assert opened == [MANUAL_URL, ISSUES_URL]

    window.change_language("es")
    assert window.manual_action.text() == "Manual de usuario"
    assert window.report_action.text() == "Informar de un error…"
    window.change_language("en")


def test_about_opens_the_dialog_with_the_window_state(window, monkeypatch):
    from argonaut_gui.window.about_dialog import AboutDialog

    opened = []
    monkeypatch.setattr(AboutDialog, "exec_", lambda self: opened.append(self))
    window.show_about()
    assert len(opened) == 1
    dialog = opened[0]
    assert ".pdf" in dialog.about_label.text()
    # the report reflects the running window: its engine and languages
    assert "Engine: Argos Translate · Languages: 2" in dialog.report.toPlainText()


def test_engine_label_shows_active_backend(window, monkeypatch):
    assert window.engine_label.text() == tr("engine_status", name="Argos Translate")
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: [
            FakeLanguage("en", "English"),
            FakeLanguage("es", "Spanish"),
        ],
    )
    window.change_backend("nllb")
    assert window.engine_label.text() == tr("engine_status", name="NLLB-200")
    window.change_language("es")
    assert window.engine_label.text() == "Motor: NLLB-200"
    window.change_language("en")


def test_remove_nllb_model(window, monkeypatch):
    installed = [True]
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: installed[0]
    )
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: [
            FakeLanguage("en", "English"),
            FakeLanguage("es", "Spanish"),
        ],
    )
    removed = []

    def fake_remove(path=None):
        installed[0] = False
        removed.append(True)

    monkeypatch.setattr(argonaut_gui.window.nllb, "remove_model", fake_remove)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )

    window.change_backend("nllb")
    assert window.nllb_remove_action.isEnabled()

    window.remove_nllb_model()
    assert removed == [True]
    assert window.backend == "argos"  # falls back before deleting
    assert window.argos_action.isChecked()
    assert not window.nllb_remove_action.isEnabled()
    assert window.status.text() == tr("nllb_removed")


def test_remove_nllb_model_declined(window, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )
    removed = []
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "remove_model", lambda path=None: removed.append(True)
    )
    window.remove_nllb_model()
    assert removed == []


def test_resource_indicator(window):
    assert re.fullmatch(r"CPU \d+%  ·  RAM \d+ MB", window.resource_label.text())
    ram_before = int(re.search(r"RAM (\d+)", window.resource_label.text()).group(1))
    assert ram_before > 0
    window.update_resource_usage()
    assert re.fullmatch(r"CPU \d+%  ·  RAM \d+ MB", window.resource_label.text())
    assert window._resource_timer.isActive()
    assert window._resource_timer.interval() == 2000


# --- the menu bar ---

def test_the_menu_bar_leads_with_file_and_has_no_language_menu(window):
    """"Language" at the top level named neither the documents the window is
    about nor which of the two kinds of language it meant, with two combos
    for the other kind right below it."""
    titles = [a.text() for a in window.menuBar().actions()]
    assert titles == [tr("menu_file"), tr("menu_settings"), tr("menu_help")]
    assert tr("menu_language") not in titles


def test_the_file_menu_carries_the_document_actions(window):
    entries = [a.text() for a in window.file_menu.actions() if not a.isSeparator()]
    assert entries == [
        tr("file_add_files"),
        tr("file_add_folder"),
        tr("file_open_output"),
        tr("file_quit"),
    ]


def test_preferences_gathers_what_the_user_likes(window):
    """The theme sat between the CPU threads and the download units, as if
    choosing a colour were part of configuring an engine. What the person
    prefers is now one submenu, and how translation runs is the rest."""
    prefs = [a.menu() for a in window.prefs_menu.actions() if a.menu()]
    assert prefs == [window.lang_menu, window.theme_menu, window.speed_menu]

    settings = [a.menu() for a in window.settings_menu.actions() if a.menu()]
    assert settings[0] is window.prefs_menu
    for gone in (window.lang_menu, window.theme_menu, window.speed_menu):
        assert gone not in settings


def test_the_interface_language_lives_under_preferences(window):
    assert window.lang_menu.title() == tr("menu_language")
    codes = [code for code, _ in LANGUAGES]
    assert len(window.lang_menu.actions()) == len(codes)

    spanish = window.lang_menu.actions()[codes.index("es")]
    spanish.trigger()
    assert window.settings_menu.title() == "&Configuración"
    assert window.prefs_menu.title() == "&Preferencias"
    assert window.lang_menu.title() == "I&dioma de la interfaz"


def test_add_files_from_the_menu_opens_the_same_dialog(window, monkeypatch, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("x")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        staticmethod(lambda *a, **k: ([str(doc)], "")),
    )
    window.add_files_action.trigger()
    assert window.paths() == [str(doc)]


def test_add_folder_walks_it_the_way_a_dropped_one_is(window, monkeypatch, tmp_path):
    """Picking a hundred documents by hand in the file dialog is not the same
    act as handing over the folder they are in."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub" / "b.docx").write_text("x")
    (tmp_path / "sub" / "notes.md").write_text("x")  # unsupported: left out
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path))
    )
    window.add_folder_action.trigger()
    assert window.paths() == [
        str(tmp_path / "a.txt"),
        str(tmp_path / "sub" / "b.docx"),
    ]


def test_add_folder_cancelled_adds_nothing(window, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: "")
    )
    window.add_folder_action.trigger()
    assert window.paths() == []


def test_open_output_folder_waits_for_a_folder_to_open(window, tmp_path):
    window.file_menu.aboutToShow.emit()
    assert not window.open_output_action.isEnabled()

    window.output_dir = str(tmp_path)
    window.file_menu.aboutToShow.emit()
    assert window.open_output_action.isEnabled()


def test_quit_closes_the_window(window, qtbot):
    """The shortcut is asserted as the keys themselves: QKeySequence.Quit is
    empty on X11 and Wayland, so comparing against it would hold just as well
    for an action that has no shortcut at all."""
    assert window.quit_action.shortcut().toString() == "Ctrl+Q"
    with qtbot.waitExposed(window):
        window.show()
    assert window.isVisible()
    window.quit_action.trigger()
    assert not window.isVisible()


def test_settings_menu_groups_engine_and_threads(window):
    assert window.settings_menu.title() == tr("menu_settings")
    actions = window.settings_menu.actions()
    # by submenu rather than by index: the separators between the groups are
    # actions too, and which slot each lands in is not what this is about
    submenus = [a.menu() for a in actions if a.menu() is not None]
    assert submenus[:3] == [
        window.prefs_menu, window.engine_menu, window.threads_menu
    ]
    assert window.pkg_install_action in actions
    assert window.nllb_remove_action in actions
    window.change_language("es")
    assert window.settings_menu.title() == "&Configuración"
    assert window.engine_menu.title() == "&Motor"
    window.change_language("en")


def test_theme_menu_defaults_to_system_and_is_translated(window):
    from argonaut_gui.window.theme import current_theme

    assert current_theme() == "system"
    actions = window.theme_menu.actions()
    assert [a.text() for a in actions] == [
        tr("theme_system"), tr("theme_light"), tr("theme_dark")
    ]
    assert [a.isChecked() for a in actions] == [True, False, False]

    window.change_language("es")
    assert window.theme_menu.title() == "&Tema"
    assert [a.text() for a in actions] == ["Sistema", "Claro", "Oscuro"]
    window.change_language("en")


def test_theme_switches_apply_and_persist(window, qtbot, langs):
    from PyQt5.QtCore import QSettings
    from PyQt5.QtGui import QPalette
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    system_window_color = app.palette().color(QPalette.Window)

    window.change_theme("dark")
    assert QSettings().value("theme") == "dark"
    assert app.style().objectName().lower() == "fusion"
    dark = app.palette().color(QPalette.Window)
    assert dark.lightness() < 128  # a dark background

    # a new window applies the saved theme on startup
    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert app.palette().color(QPalette.Window) == dark

    win2.change_theme("light")
    assert app.palette().color(QPalette.Window).lightness() > 128

    # "system" restores what the desktop had before any override
    win2.change_theme("system")
    assert app.palette().color(QPalette.Window) == system_window_color


def test_unknown_saved_theme_falls_back_to_system(qtbot, langs):
    from PyQt5.QtCore import QSettings
    from PyQt5.QtGui import QPalette
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    system_window_color = app.palette().color(QPalette.Window)
    QSettings().setValue("theme", "solarized")  # e.g. from a newer version
    win = MainWindow()
    qtbot.addWidget(win)
    assert app.palette().color(QPalette.Window) == system_window_color
    assert win.theme_menu.actions()[0].isChecked()  # system marked active


def test_package_menu_action_is_translated(window):
    assert window.pkg_install_action.text() == tr("pkg_install")
    window.change_language("es")
    assert window.pkg_install_action.text() == "Gestionar paquetes de idiomas…"
    window.change_language("en")


def test_installed_packages_refresh_the_window(qtbot, monkeypatch):
    english = FakeLanguage("en", "English")
    spanish = FakeLanguage("es", "Spanish")
    installed = fake_installed_languages([])
    monkeypatch.setattr(
        argonaut_gui.window.argostranslate.translate, "get_installed_languages", installed
    )
    win = MainWindow()
    qtbot.addWidget(win)
    win.add_paths(["/a/doc.txt"])  # so the packages are the only thing missing
    assert not win.translate_btn.isEnabled()

    monkeypatch.setattr(
        argonaut_gui.window.argostranslate.translate,
        "get_installed_languages",
        fake_installed_languages([english, spanish]),
    )
    win.on_packages_changed()
    assert win.translate_btn.isEnabled()
    assert win.to_combo.count() == 2
    assert win.status.text() == tr("ready")


def test_quality_mode_default_and_menu(window):
    from argonaut_gui.window.engine import ARGOS_DEFAULT_BEAM
    from argonaut_gui.worker import quality_mode

    assert quality_mode() == "quality"
    assert argonaut_gui.window.argostranslate.settings.beam_size == ARGOS_DEFAULT_BEAM
    actions = window.quality_menu.actions()
    assert len(actions) == 2
    assert actions[0].isChecked()  # "quality" is the default
    window.change_language("es")
    assert window.quality_menu.title() == "Calidad de traducción"
    assert actions[1].text() == "Rápida (calidad algo menor)"
    window.change_language("en")


def test_quality_mode_change_applies_and_persists(qtbot, langs, monkeypatch):
    from argonaut_cli import nllb
    from argonaut_gui.window.engine import ARGOS_DEFAULT_BEAM

    captured = []
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: captured.append(beam_size) or [],
    )
    win = MainWindow()
    qtbot.addWidget(win)
    win.change_quality_mode("quality")  # already active: nothing to change
    win.change_quality_mode("fast")
    assert argonaut_gui.window.argostranslate.settings.beam_size == 1
    win.close()

    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.quality_actions[1].isChecked()  # persisted across sessions
    assert argonaut_gui.window.argostranslate.settings.beam_size == 1  # at startup
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    win2.change_backend("nllb")
    assert captured[-1] == 1  # the NLLB engine goes greedy too
    win2.change_quality_mode("quality")
    assert argonaut_gui.window.argostranslate.settings.beam_size == ARGOS_DEFAULT_BEAM
    assert captured[-1] == nllb.NllbEngine.DEFAULT_BEAM


def test_cpu_threads_default_and_menu(window):
    import os

    max_threads = os.cpu_count() or 1
    assert window.max_threads == max_threads
    assert window.cpu_threads == max_threads  # every core, unless overridden
    assert argonaut_gui.window.argostranslate.settings.intra_threads == window.cpu_threads
    actions = window.threads_menu.actions()
    assert [a.text() for a in actions] == [str(n) for n in range(1, max_threads + 1)]
    assert actions[window.cpu_threads - 1].isChecked()
    window.change_language("es")
    assert window.threads_menu.title() == "Hilos de CPU"
    window.change_language("en")


def test_cpu_threads_change_applies_and_persists(qtbot, langs, monkeypatch):
    captured = []
    monkeypatch.setattr(
        argonaut_gui.window.nllb,
        "get_installed_languages",
        lambda path=None, threads=None, beam_size=None: captured.append(threads) or [],
    )
    win = MainWindow()
    qtbot.addWidget(win)
    win.change_cpu_threads(1)
    assert win.cpu_threads == 1
    assert argonaut_gui.window.argostranslate.settings.intra_threads == 1
    win.close()

    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.cpu_threads == 1
    assert win2.threads_menu.actions()[0].isChecked()
    monkeypatch.setattr(
        argonaut_gui.window.nllb, "is_model_installed", lambda path=None: True
    )
    win2.change_backend("nllb")
    assert captured[-1] == 1  # the NLLB engine receives the configured value


def test_cpu_threads_saved_value_is_clamped(qtbot, langs):
    from PyQt5.QtCore import QSettings

    QSettings().setValue("cpu_threads", 9999)
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.cpu_threads == win.max_threads


def test_settings_persist_between_windows(qtbot, langs):
    win = MainWindow()
    qtbot.addWidget(win)
    win.from_combo.setCurrentIndex(1)  # English
    win.to_combo.setCurrentIndex(1)  # Spanish
    win.close()

    win2 = MainWindow()
    qtbot.addWidget(win2)
    assert win2.from_combo.currentText() == "English"
    assert win2.to_combo.currentText() == "Spanish"
