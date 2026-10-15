import time

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox

from argonaut_cli.history import TranslationHistory

from argonaut_gui.i18n import tr
from argonaut_gui.window import history_dialog
from argonaut_gui.window.history_dialog import (
    CACHE_COL,
    COLUMN_KEYS,
    DATE_COL,
    ENGINE_COL,
    FILE_COL,
    FOLDER_COL,
    PAIR_COL,
    TIME_COL,
    HistoryDialog,
)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "history.db")


def fill(db, entries):
    """entries: (source, output, from, to, engine, seconds, when)."""
    history = TranslationHistory(db, ttl_days=0)
    for entry in entries:
        history.record(*entry[:6], when=entry[6])
    history.close()


def fill_cache_figures(db, rows):
    """rows: (name, reused, segments), newest last."""
    history = TranslationHistory(db, ttl_days=0)
    now = int(time.time())
    for i, (name, reused, segments) in enumerate(rows):
        history.record(
            f"/docs/{name}", "", "en", "es", "argos", 1.0,
            reused=reused, segments=segments, when=now - len(rows) + i,
        )
    history.close()


def make_dialog(qtbot, db):
    dialog = HistoryDialog(db)
    qtbot.addWidget(dialog)
    return dialog


def test_empty_history_says_so(qtbot, db):
    dialog = make_dialog(qtbot, db)
    assert dialog.tree.topLevelItemCount() == 0
    assert dialog.summary.text() == tr("hist_empty")
    assert not dialog.clear_btn.isEnabled()
    assert not dialog.open_btn.isEnabled()


def test_rows_show_what_was_translated(qtbot, db, tmp_path):
    out = tmp_path / "report_es.docx"
    out.write_text("translated")
    when = int(time.mktime((2026, 7, 26, 15, 30, 0, 0, 0, -1)))
    fill(db, [(str(tmp_path / "report.docx"), str(out), "en", "es", "nllb", 75, when)])

    dialog = make_dialog(qtbot, db)
    item = dialog.tree.topLevelItem(0)
    assert item.text(DATE_COL) == "2026-07-26 15:30"
    assert item.text(FILE_COL) == "report.docx"
    assert item.text(FOLDER_COL) == str(tmp_path)  # the source's folder
    assert item.text(PAIR_COL) == "en → es"
    assert item.text(ENGINE_COL) == "NLLB-200"  # the engine id is shown by name
    assert item.text(TIME_COL) == "01:15"
    assert str(out) in item.toolTip(FILE_COL)
    assert not item.isDisabled()
    assert dialog.summary.text() == tr("hist_count", count=1)


def test_a_row_shows_how_much_of_it_the_cache_answered(qtbot, db):
    fill_cache_figures(db, [("doc.txt", 30, 120)])
    dialog = make_dialog(qtbot, db)
    item = dialog.tree.topLevelItem(0)

    assert item.text(CACHE_COL) == "25 %"  # the rate in the cell…
    assert item.toolTip(CACHE_COL) == tr(   # …the figures behind it in full
        "cache_reused_summary", reused=30, total=120, percent=25
    )


def test_a_row_with_no_cache_figures_leaves_the_cell_empty(qtbot, db, tmp_path):
    """An entry recorded before the figures were kept knows nothing about the
    cache, which is not the same as a run that reused none of it."""
    fill(db, [
        (str(tmp_path / "old.txt"), "", "en", "es", "argos", 1, int(time.time())),
    ])
    fill_cache_figures(db, [("none.txt", 0, 80)])
    dialog = make_dialog(qtbot, db)
    rows = {
        dialog.tree.topLevelItem(i).text(FILE_COL): dialog.tree.topLevelItem(i)
        for i in range(2)
    }

    assert rows["old.txt"].text(CACHE_COL) == ""       # unknown
    assert rows["old.txt"].toolTip(CACHE_COL) == ""
    assert rows["none.txt"].text(CACHE_COL) == "0 %"   # known to be none


def test_the_cache_column_sorts_by_the_rate_not_by_its_text(qtbot, db, tmp_path):
    fill(db, [  # a row with no figures at all
        (str(tmp_path / "unknown.txt"), "", "en", "es", "argos", 1, int(time.time())),
    ])
    fill_cache_figures(db, [("full.txt", 10, 10), ("third.txt", 1, 3)])
    dialog = make_dialog(qtbot, db)

    def order():
        return [
            dialog.tree.topLevelItem(i).text(FILE_COL)
            for i in range(dialog.tree.topLevelItemCount())
        ]

    dialog.tree.sortItems(CACHE_COL, Qt.AscendingOrder)
    # "100 %" would sort before "33 %" as a string; and the row that recorded
    # no rate groups below both rather than among them
    assert order() == ["unknown.txt", "third.txt", "full.txt"]

    dialog.tree.sortItems(CACHE_COL, Qt.DescendingOrder)
    assert order() == ["full.txt", "third.txt", "unknown.txt"]


def test_the_header_names_every_column(qtbot, db):
    dialog = make_dialog(qtbot, db)
    assert dialog.tree.columnCount() == len(COLUMN_KEYS)
    header = dialog.tree.headerItem()
    assert [header.text(col) for col in range(len(COLUMN_KEYS))] == [
        tr(key) for key in COLUMN_KEYS
    ]


def test_same_named_files_are_told_apart_by_their_folder(qtbot, db, tmp_path):
    """The file column carries only the base name, so the folder is what says
    which of two same-named documents a row is about."""
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    now = int(time.time())
    fill(db, [
        (str(tmp_path / "one" / "doc.txt"), "", "en", "es", "argos", 1, now - 60),
        (str(tmp_path / "two" / "doc.txt"), "", "en", "es", "argos", 1, now),
    ])
    dialog = make_dialog(qtbot, db)
    rows = [dialog.tree.topLevelItem(i) for i in range(2)]
    assert [row.text(FILE_COL) for row in rows] == ["doc.txt", "doc.txt"]
    assert [row.text(FOLDER_COL) for row in rows] == [
        str(tmp_path / "two"), str(tmp_path / "one")  # newest first
    ]


def test_newest_translation_comes_first(qtbot, db, tmp_path):
    now = int(time.time())
    fill(db, [
        (str(tmp_path / "old.txt"), "", "en", "es", "argos", 1, now - 60),
        (str(tmp_path / "new.txt"), "", "en", "es", "argos", 1, now),
    ])
    dialog = make_dialog(qtbot, db)
    assert dialog.tree.topLevelItem(0).text(FILE_COL) == "new.txt"


def test_a_translation_that_no_longer_exists_is_greyed_out(qtbot, db, tmp_path):
    """The history records what happened, so a deleted output stays listed
    instead of vanishing — just marked as gone and not openable."""
    fill(db, [
        (str(tmp_path / "gone.txt"), str(tmp_path / "gone_es.txt"),
         "en", "es", "argos", 1, int(time.time())),
    ])
    dialog = make_dialog(qtbot, db)
    item = dialog.tree.topLevelItem(0)
    assert item.isDisabled()
    assert tr("hist_missing") in item.toolTip(FILE_COL)

    dialog.tree.setCurrentItem(item)
    assert dialog.selected_output() is None
    assert not dialog.open_btn.isEnabled()


def test_selecting_an_existing_translation_enables_opening(qtbot, db, tmp_path):
    out = tmp_path / "there_es.txt"
    out.write_text("x")
    fill(db, [
        (str(tmp_path / "there.txt"), str(out), "en", "es", "argos", 1,
         int(time.time())),
    ])
    dialog = make_dialog(qtbot, db)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert dialog.open_btn.isEnabled()
    assert dialog.selected_output() == str(out)


def test_opening_a_translation_asks_the_desktop_to_open_it(qtbot, db, tmp_path, monkeypatch):
    out = tmp_path / "open_es.txt"
    out.write_text("x")
    fill(db, [
        (str(tmp_path / "open.txt"), str(out), "en", "es", "argos", 1,
         int(time.time())),
    ])
    opened = []
    monkeypatch.setattr(
        history_dialog.QDesktopServices, "openUrl",
        lambda url: opened.append(url.toLocalFile()),
    )
    dialog = make_dialog(qtbot, db)
    item = dialog.tree.topLevelItem(0)

    dialog.open_item(item, FILE_COL)  # double-click
    dialog.tree.setCurrentItem(item)
    dialog.open_selected()            # and the button
    assert opened == [str(out), str(out)]


def test_opening_does_nothing_when_the_file_is_gone(qtbot, db, tmp_path, monkeypatch):
    fill(db, [
        (str(tmp_path / "gone.txt"), str(tmp_path / "gone_es.txt"),
         "en", "es", "argos", 1, int(time.time())),
    ])
    opened = []
    monkeypatch.setattr(
        history_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url)
    )
    dialog = make_dialog(qtbot, db)
    dialog.open_item(dialog.tree.topLevelItem(0), FILE_COL)
    dialog.open_selected()  # nothing selected either
    assert opened == []


def test_clearing_empties_the_list_and_announces_it(qtbot, db, tmp_path, monkeypatch):
    fill(db, [
        (str(tmp_path / "a.txt"), "", "en", "es", "argos", 1, int(time.time())),
    ])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dialog = make_dialog(qtbot, db)
    cleared = []
    dialog.history_cleared.connect(lambda: cleared.append(True))

    dialog.clear_history()
    assert dialog.tree.topLevelItemCount() == 0
    assert dialog.summary.text() == tr("hist_empty")
    assert cleared == [True]  # the menu's size label is told to refresh
    assert TranslationHistory.db_info(db)[0] == 0


def test_declining_the_confirmation_keeps_the_history(qtbot, db, tmp_path, monkeypatch):
    fill(db, [
        (str(tmp_path / "a.txt"), "", "en", "es", "argos", 1, int(time.time())),
    ])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    dialog = make_dialog(qtbot, db)
    dialog.clear_history()
    assert dialog.tree.topLevelItemCount() == 1
    assert TranslationHistory.db_info(db)[0] == 1


def test_viewing_the_history_never_prunes_it(qtbot, db, tmp_path):
    """Opening the dialog must not apply the expiry the worker uses, or
    simply looking at an old history would delete it."""
    old = int(time.time()) - 3650 * 86400
    fill(db, [(str(tmp_path / "ancient.txt"), "", "en", "es", "argos", 1, old)])
    dialog = make_dialog(qtbot, db)
    assert dialog.tree.topLevelItemCount() == 1
    assert TranslationHistory.db_info(db)[0] == 1


def test_the_dialog_defaults_to_the_standard_database(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    dialog = HistoryDialog()
    qtbot.addWidget(dialog)
    assert dialog.db_path == TranslationHistory.default_db_path()
