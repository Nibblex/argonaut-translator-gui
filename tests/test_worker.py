"""The worker threads: how a batch's reports become signals, the settings
the window hands a batch, and the download and package workers."""

import types

import pytest

from argonaut import nllb, packages
from argonaut.batch import AlreadyInLanguage, LanguageNotDetected, NoModel
from argonaut.i18n import set_language, tr
from argonaut.translation import CancelledError
from argonaut.worker import (
    MegabyteProgress,
    ModelDownloadWorker,
    PackageInstallWorker,
    PackageListWorker,
    PackageSizeWorker,
    TranslateWorker,
    error_text,
    speed_text,
)
from tests.conftest import FakeLanguage, FakeTranslation
from tests.test_translation import ENGLISH_TEXT


def make_langs():
    english = FakeLanguage("en", "English")
    spanish = FakeLanguage("es", "Spanish")
    english._translations["es"] = FakeTranslation(english, spanish)
    return english, spanish


def make_worker(files, src, dst, languages=(), output_dir=None, skip_existing=False):
    return TranslateWorker(
        src, dst, list(languages), files,
        output_dir=output_dir, skip_existing=skip_existing,
    )


def test_run_signals_each_file_and_the_end_of_the_batch(qapp, tmp_path):
    english, spanish = make_langs()
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    started, done, stats, failed, finished = [], [], [], [], []
    worker = make_worker([str(doc)], english, spanish)
    worker.file_started.connect(lambda i, path: started.append((i, path)))
    worker.file_done.connect(lambda i, out, secs: done.append(out))
    worker.file_cache_stats.connect(lambda *args: stats.append(args))
    worker.file_failed.connect(lambda i, err: failed.append(err))
    worker.finished_all.connect(lambda: finished.append(True))
    worker.run()

    assert started == [(0, str(doc))]
    assert done == [str(tmp_path / "doc_es.txt")]
    assert stats == [(0, 0, 0, 1)]
    assert failed == []
    assert finished == [True]


def test_skipped_files_are_signalled(qapp, tmp_path):
    english, spanish = make_langs()
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    (tmp_path / "doc_es.txt").write_text("already translated")
    skipped = []
    worker = make_worker([str(doc)], english, spanish, skip_existing=True)
    worker.file_skipped.connect(lambda i, out: skipped.append(out))
    worker.run()
    assert skipped == [str(tmp_path / "doc_es.txt")]


def test_language_names_in_messages_follow_the_interface(qapp, tmp_path):
    english, spanish = make_langs()
    doc = tmp_path / "doc.txt"
    doc.write_text(ENGLISH_TEXT)
    set_language("es")

    detected = []
    worker = make_worker([str(doc)], None, spanish, languages=[english, spanish])
    worker.language_detected.connect(lambda i, name: detected.append(name))
    worker.run()
    assert detected == ["Inglés"]

    failed = []
    worker = make_worker([str(doc)], spanish, english)  # no es->en model
    worker.file_failed.connect(lambda i, err: failed.append(err))
    worker.run()
    assert len(failed) == 1 and "Español → Inglés" in failed[0]


def test_a_file_whose_language_cannot_be_detected_fails_in_words(qapp, tmp_path):
    _, spanish = make_langs()
    doc = tmp_path / "doc.txt"
    doc.write_text("")  # nothing to detect a language from
    failed = []
    worker = make_worker([str(doc)], None, spanish, languages=[spanish])
    worker.file_failed.connect(lambda i, err: failed.append(err))
    worker.run()
    assert failed == [tr("err_detect", name="doc.txt")]


def test_error_text_renders_the_batch_errors_in_the_interface_language(qapp):
    english, spanish = make_langs()
    set_language("es")
    assert error_text(LanguageNotDetected("/d/a.txt")) == tr("err_detect", name="a.txt")
    assert error_text(AlreadyInLanguage("/d/a.txt", spanish)) == tr(
        "err_already", name="a.txt", lang="Español"
    )
    assert error_text(NoModel("/d/a.txt", spanish, english)) == tr(
        "err_no_model", name="a.txt", src="Español", dst="Inglés"
    )
    # anything else is shown as it came: an engine or file error is not ours
    assert error_text(RuntimeError("disk full")) == "disk full"


def test_the_settings_shape_the_batch(qapp, tmp_path):
    from PyQt5.QtCore import QSettings

    english, spanish = make_langs()
    worker = make_worker([], english, spanish)
    worker.run()
    assert not worker.batch.greedy
    assert worker._cache_db_path and worker._history_db_path  # both kept on disk

    QSettings().setValue("quality_mode", "fast")
    QSettings().setValue("cache_enabled", False)
    QSettings().setValue("history_enabled", False)
    worker = make_worker([], english, spanish)
    assert worker.greedy
    assert worker._cache_db_path is None and worker._history_db_path is None


def test_history_can_be_switched_off(qapp, tmp_path):
    from PyQt5.QtCore import QSettings

    from argonaut.history import TranslationHistory

    QSettings().setValue("history_enabled", False)
    english, spanish = make_langs()
    source = tmp_path / "note.txt"
    source.write_text("hello world")
    worker = make_worker([str(source)], english, spanish)
    worker.run()

    assert TranslationHistory.db_info(TranslationHistory.default_db_path())[0] == 0
    assert (tmp_path / "note_es.txt").exists()  # the translation still happened


def test_history_is_kept_by_default(qapp, tmp_path):
    from argonaut.history import TranslationHistory

    english, spanish = make_langs()
    source = tmp_path / "note.txt"
    source.write_text("hello world")
    make_worker([str(source)], english, spanish).run()
    assert TranslationHistory.db_info(TranslationHistory.default_db_path())[0] == 1


def test_cancelled_run_stops_before_translating(qapp, tmp_path):
    english, spanish = make_langs()
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world")
    done, finished = [], []
    worker = make_worker([str(doc)], english, spanish)
    worker.file_done.connect(lambda i, out, secs: done.append(out))
    worker.finished_all.connect(lambda: finished.append(True))
    worker.cancel()
    worker.run()

    assert worker.was_cancelled()
    assert done == []
    assert finished == [True]


def test_unexpected_error_still_reports_the_batch_as_finished(qapp, tmp_path):
    """finished_all is what takes the window out of its busy state: without
    it the interface stays disabled forever, looking hung."""
    english, spanish = make_langs()
    worker = make_worker([str(tmp_path / "doc.txt")], english, spanish)
    worker.files = None  # anything that breaks the loop itself
    finished = []
    worker.finished_all.connect(lambda: finished.append(True))

    with pytest.raises(TypeError):
        worker.run()
    assert finished == [True]


def test_ui_yield_is_throttled(qapp, monkeypatch):
    """The interpreter is handed to the interface thread on a timer, not on
    every page: reading a long book takes about as long as one millisecond
    per page would, so yielding each time paced the phase, not the work."""
    from argonaut import worker as worker_mod

    english, spanish = make_langs()
    worker = make_worker([], english, spanish)
    clock, slept = [1000.0], []
    monkeypatch.setattr(worker_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker_mod.time, "sleep", slept.append)

    worker._yield_to_ui()
    worker._yield_to_ui()  # same instant
    assert len(slept) == 1

    clock[0] += worker.YIELD_EVERY / 2
    worker._yield_to_ui()
    assert len(slept) == 1  # still inside the window

    clock[0] += worker.YIELD_EVERY
    worker._yield_to_ui()
    assert len(slept) == 2


def test_every_page_is_still_reported_while_yields_are_throttled(qapp, monkeypatch):
    """Throttling the hand-over must not throttle the page number: it is
    what tells the user a long book is still moving."""
    from argonaut import worker as worker_mod

    english, spanish = make_langs()
    worker = make_worker([], english, spanish)
    clock, slept = [1000.0], []
    monkeypatch.setattr(worker_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker_mod.time, "sleep", slept.append)

    reported = []
    worker.phase_changed.connect(lambda key, kw: reported.append(kw["done"]))
    for page in range(1, 6):
        worker.on_phase("generating", {"name": "book.pdf", "done": page, "total": 5})

    assert reported == [1, 2, 3, 4, 5]
    assert len(slept) == 1  # one hand-over for the five pages


def test_speed_text_picks_network_units():
    assert speed_text(0) == ""  # unknown yet: no label
    assert speed_text(100) == "1 kbps"  # tiny but nonzero: never "0 kbps"
    assert speed_text(50_000) == "400 kbps"
    assert speed_text(2 * 2**20) == "16.8 Mbps"


def test_speed_text_can_render_byte_units():
    assert speed_text(0, "bytes") == ""
    assert speed_text(100, "bytes") == "1 KB/s"
    assert speed_text(400 * 1024, "bytes") == "400 KB/s"
    assert speed_text(2 * 2**20, "bytes") == "2.0 MB/s"


def test_speed_units_setting_defaults_to_bits(qapp):
    from PyQt5.QtCore import QSettings

    from argonaut.worker import speed_units

    assert speed_units() == "bits"
    QSettings().setValue("speed_units", "bytes")
    assert speed_units() == "bytes"
    QSettings().setValue("speed_units", "parsecs")  # e.g. a newer version
    assert speed_units() == "bits"


def test_quality_mode_setting_defaults_to_quality(qapp):
    from PyQt5.QtCore import QSettings

    from argonaut.worker import quality_mode

    assert quality_mode() == "quality"
    QSettings().setValue("quality_mode", "fast")
    assert quality_mode() == "fast"
    QSettings().setValue("quality_mode", "turbo")  # e.g. a newer version
    assert quality_mode() == "quality"


def test_megabyte_progress_measures_speed_over_a_sliding_window():
    class FakeSignal:
        def __init__(self):
            self.emitted = []

        def emit(self, *args):
            self.emitted.append(args)

    t = [0.0]
    signal = FakeSignal()
    report = MegabyteProgress(signal, clock=lambda: t[0])
    total = 10 * 2**20

    report(0, total)
    assert signal.emitted == [(0, 10, 0.0)]  # no window measured yet

    t[0] = 1.0  # one full window later, 2 MB came in: 2 MB/s
    report(2 * 2**20, total)
    assert signal.emitted[-1] == (2, 10, float(2 * 2**20))

    t[0] = 1.5  # mid-window: the previous measurement is kept
    report(3 * 2**20, total)
    assert signal.emitted[-1] == (3, 10, float(2 * 2**20))

    report.reset()  # a new file starts a fresh window
    report(0, total)
    assert signal.emitted[-1] == (0, 10, 0.0)


def test_model_download_worker_reports_success(qapp, monkeypatch):
    monkeypatch.setattr(
        nllb, "download_model", lambda on_progress=None, is_cancelled=None: None
    )
    results = []
    worker = ModelDownloadWorker()
    worker.download_finished.connect(lambda ok, err: results.append((ok, err)))
    worker.run()
    assert results == [(True, "")]


def test_model_download_worker_reports_cancellation_without_error(qapp, monkeypatch):
    def cancelled(on_progress=None, is_cancelled=None):
        raise CancelledError()

    monkeypatch.setattr(nllb, "download_model", cancelled)
    results = []
    worker = ModelDownloadWorker()
    worker.download_finished.connect(lambda ok, err: results.append((ok, err)))
    worker.run()
    assert results == [(False, "")]  # cancelled, but nothing went wrong


def test_model_download_worker_reports_errors(qapp, monkeypatch):
    def broken(on_progress=None, is_cancelled=None):
        raise RuntimeError("offline")

    monkeypatch.setattr(nllb, "download_model", broken)
    results = []
    worker = ModelDownloadWorker()
    worker.download_finished.connect(lambda ok, err: results.append((ok, err)))
    worker.run()
    assert results == [(False, "offline")]


def fake_pkg(from_name="English", to_name="Spanish"):
    return types.SimpleNamespace(
        from_code=from_name[:2].lower(),
        to_code=to_name[:2].lower(),
        from_name=from_name,
        to_name=to_name,
    )


def test_package_list_worker_reports_packages(qapp, monkeypatch):
    available = [fake_pkg()]
    monkeypatch.setattr(packages, "get_available", lambda: available)
    results = []
    worker = PackageListWorker()
    worker.listed.connect(lambda pkgs, err: results.append((pkgs, err)))
    worker.run()
    assert results == [(available, "")]


def test_package_list_worker_reports_errors(qapp, monkeypatch):
    def broken():
        raise RuntimeError("offline")

    monkeypatch.setattr(packages, "get_available", broken)
    results = []
    worker = PackageListWorker()
    worker.listed.connect(lambda pkgs, err: results.append((pkgs, err)))
    worker.run()
    assert results == [([], "offline")]


def test_package_size_worker_reports_each_row(qapp, monkeypatch):
    sizes = {"Spanish": 10 * 2**20, "French": 0}
    monkeypatch.setattr(packages, "get_size", lambda pkg: sizes[pkg.to_name])
    results = []
    worker = PackageSizeWorker([(0, fake_pkg()), (3, fake_pkg(to_name="French"))])
    worker.size_ready.connect(lambda row, size: results.append((row, size)))
    worker.run()
    assert sorted(results) == [(0, 10 * 2**20), (3, 0)]


def test_package_size_worker_stops_on_cancel(qapp, monkeypatch):
    monkeypatch.setattr(packages, "get_size", lambda pkg: 5)
    results = []
    worker = PackageSizeWorker([(0, fake_pkg()), (1, fake_pkg(to_name="French"))])
    worker.size_ready.connect(lambda row, size: results.append(row))
    worker.cancel()
    worker.run()
    assert results == []  # nothing reported after the cancel


def test_package_install_worker_pre_cancelled_installs_nothing(qapp, monkeypatch):
    installed = []
    monkeypatch.setattr(
        packages, "install",
        lambda pkg, on_progress=None, is_cancelled=None: installed.append(pkg),
    )
    finished = []
    worker = PackageInstallWorker([fake_pkg()])
    worker.install_finished.connect(lambda n: finished.append(n))
    worker.cancel()
    worker.run()
    assert installed == []
    assert finished == [0]  # the summary still arrives


def test_package_install_worker_continues_after_failures(qapp, monkeypatch):
    good, bad = fake_pkg(), fake_pkg(to_name="French")

    def install(pkg, on_progress=None, is_cancelled=None):
        if pkg is bad:
            raise RuntimeError("boom")
        on_progress(2 * 2**20, 4 * 2**20)

    monkeypatch.setattr(packages, "install", install)
    started, progress, failed, finished = [], [], [], []
    worker = PackageInstallWorker([bad, good])
    worker.package_started.connect(lambda i, n, d: started.append((i, n, d)))
    worker.progress.connect(lambda done, total, speed: progress.append((done, total)))
    worker.package_failed.connect(lambda d, e: failed.append((d, e)))
    worker.install_finished.connect(lambda n: finished.append(n))
    worker.run()

    assert started == [
        (0, 2, "English → French"),
        (1, 2, "English → Spanish"),
    ]
    assert progress == [(2, 4)]
    assert failed == [("English → French", "boom")]
    assert finished == [1]


def test_package_install_worker_stops_on_cancel(qapp, monkeypatch):
    def install(pkg, on_progress=None, is_cancelled=None):
        raise CancelledError()

    monkeypatch.setattr(packages, "install", install)
    finished = []
    worker = PackageInstallWorker([fake_pkg(), fake_pkg(to_name="French")])
    worker.install_finished.connect(lambda n: finished.append(n))
    worker.run()
    assert finished == [0]


def test_package_list_worker_sorts_by_the_translated_names(qapp, monkeypatch):
    """The order has to follow the names the dialog will show, or a
    translated list reads as if it were shuffled."""
    from argonaut.i18n import pair_text

    def pkg(from_code, to_code, from_name, to_name):
        return types.SimpleNamespace(
            from_code=from_code, to_code=to_code, from_name=from_name, to_name=to_name
        )

    available = [
        pkg("en", "fr", "English", "French"),
        pkg("es", "en", "Spanish", "English"),
        pkg("en", "es", "English", "Spanish"),
    ]
    monkeypatch.setattr(packages, "get_available", lambda: available)
    set_language("es")
    results = []
    worker = PackageListWorker()
    worker.listed.connect(lambda pkgs, err: results.append(pkgs))
    worker.run()
    assert [pair_text(p) for p in results[0]] == [
        "Español → Inglés",
        "Inglés → Español",
        "Inglés → Francés",
    ]
