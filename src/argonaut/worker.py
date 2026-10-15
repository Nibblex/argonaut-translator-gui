"""Worker threads that keep translation, downloads and the package index
off the interface thread."""

import time

from PyQt5.QtCore import QSettings, QThread, pyqtSignal

from argonaut import nllb, packages
from argonaut.batch import AlreadyInLanguage, Batch, LanguageNotDetected, Listener, NoModel
from argonaut.history import TranslationHistory
from argonaut.i18n import lang_text, pair_text, sorted_packages, tr
from argonaut.translation import CancelledError, TranslationCache


class CancellableThread(QThread):
    """A worker the window can ask to stop. Nothing is interrupted by force:
    the thread checks ``was_cancelled`` at its own safe points, so a call
    already in flight still has to return first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def was_cancelled(self):
        return self._cancelled


def speed_units():
    """The configured family for download-speed labels: network bits
    ("bits", the default) or the bytes a download manager shows."""
    saved = QSettings().value("speed_units", "bits")
    return saved if saved in ("bits", "bytes") else "bits"


def quality_mode():
    """The configured speed/quality trade-off: "quality" (each engine's
    default beam width, the default) or "fast" (greedy decoding)."""
    saved = QSettings().value("quality_mode", "quality")
    return saved if saved in ("quality", "fast") else "quality"


def speed_text(bytes_per_second, units="bits"):
    """Download speed label; empty while the speed is still unknown.
    "bits" renders network units ("16.8 Mbps", "400 kbps"); "bytes" what a
    download manager shows ("2.0 MB/s", "400 KB/s"), binary like the MB
    figures next to it."""
    if bytes_per_second <= 0:
        return ""
    if units == "bytes":
        if bytes_per_second >= 2**20:
            return f"{bytes_per_second / 2**20:.1f} MB/s"
        return f"{max(1, round(bytes_per_second / 1024))} KB/s"
    bits = bytes_per_second * 8
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f} Mbps"
    return f"{max(1, round(bits / 1000))} kbps"


class MegabyteProgress:
    """Forwards byte counts to a (done MB, total MB, bytes per second)
    signal, but only when the megabyte figure actually changes: a download
    otherwise reports thousands of times for a bar that can show a few
    hundred steps. The speed is measured over a short sliding window, so
    it follows the connection's current pace rather than the download's
    lifetime average (0.0 until the first window completes)."""

    WINDOW = 1.0  # seconds per speed sample

    def __init__(self, signal, clock=time.monotonic):
        self._signal = signal
        self._clock = clock
        self.reset()

    def reset(self):
        self._last = -1
        self._speed = 0.0
        self._anchor = None  # (time, bytes) the current window started at

    def __call__(self, done, total):
        now = self._clock()
        if self._anchor is None:
            self._anchor = (now, done)
        t0, done0 = self._anchor
        if now - t0 >= self.WINDOW:
            self._speed = (done - done0) / (now - t0)
            self._anchor = (now, done)
        mb = done >> 20
        if mb != self._last:
            self._last = mb
            self._signal.emit(mb, max(1, total >> 20), self._speed)


class ModelDownloadWorker(CancellableThread):
    """Downloads the NLLB model without blocking the UI."""

    progress = pyqtSignal(int, int, float)  # done MB, total MB, bytes/sec
    download_finished = pyqtSignal(bool, str)  # ok, error ("" when cancelled)

    def run(self):
        try:
            nllb.download_model(
                on_progress=MegabyteProgress(self.progress),
                is_cancelled=self.was_cancelled,
            )
        except CancelledError:
            self.download_finished.emit(False, "")
        except Exception as exc:  # noqa: BLE001
            self.download_finished.emit(False, str(exc))
        else:
            self.download_finished.emit(True, "")


def available_packages():
    """The Argos package index in the order the user reads it. The engine
    sorts by the English names, which a translated list would show as if
    shuffled."""
    return sorted_packages(packages.get_available())


class PackageListWorker(QThread):
    """Fetches the Argos package index without blocking the UI."""

    listed = pyqtSignal(list, str)  # available packages, error ("" = ok)

    def run(self):
        try:
            available = available_packages()
        except Exception as exc:  # noqa: BLE001
            self.listed.emit([], str(exc))
        else:
            self.listed.emit(available, "")


class PackageSizeWorker(CancellableThread):
    """Fetches package archive sizes with HEAD requests, a few at a time."""

    size_ready = pyqtSignal(int, int)  # list row, bytes (0 = unknown)

    def __init__(self, to_measure, parent=None):
        super().__init__(parent)
        self.to_measure = to_measure  # list of (row, package)

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(packages.get_size, pkg): row
                for row, pkg in self.to_measure
            }
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return
                self.size_ready.emit(futures[future], future.result())


class PackageInstallWorker(CancellableThread):
    """Downloads and installs a list of Argos packages."""

    package_started = pyqtSignal(int, int, str)  # index, total, "English → Spanish"
    progress = pyqtSignal(int, int, float)  # done MB, total MB, bytes/sec
    package_failed = pyqtSignal(str, str)  # description, error
    install_finished = pyqtSignal(int)  # packages installed

    def __init__(self, to_install, parent=None):
        super().__init__(parent)
        self.to_install = to_install
        self._report = MegabyteProgress(self.progress)

    def run(self):
        installed = 0
        for i, pkg in enumerate(self.to_install):
            if self._cancelled:
                break
            self._report.reset()  # each package has its own byte count
            self.package_started.emit(i, len(self.to_install), pair_text(pkg))
            try:
                packages.install(
                    pkg, on_progress=self._report, is_cancelled=self.was_cancelled
                )
            except CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self.package_failed.emit(pair_text(pkg), str(exc))
            else:
                installed += 1
        self.install_finished.emit(installed)


def error_text(error):
    """The message for a file the batch could not translate, in the
    interface language. The batch's own errors carry the file and the
    languages rather than a sentence, so the names read in the interface
    language too; anything else is shown as it came."""
    if isinstance(error, LanguageNotDetected):
        return tr("err_detect", name=error.name)
    if isinstance(error, AlreadyInLanguage):
        return tr("err_already", name=error.name, lang=lang_text(error.lang))
    if isinstance(error, NoModel):
        return tr(
            "err_no_model",
            name=error.name,
            src=lang_text(error.src),
            dst=lang_text(error.dst),
        )
    return str(error)


class TranslateWorker(CancellableThread, Listener):
    """Runs a :class:`~argonaut.batch.Batch` on its own thread and turns
    what it reports into signals for the window. The settings that shape
    the run (cache, history, quality) are read here, on the interface
    thread, when the run is set up."""

    file_started = pyqtSignal(int, str)
    file_done = pyqtSignal(int, str, float)  # index, output path, seconds
    file_failed = pyqtSignal(int, str)
    file_skipped = pyqtSignal(int, str)  # index, existing output path
    # index, segments this file reused from the cache, batch reuse so far and
    # the segments that reuse is out of (so the window can show a rate)
    file_cache_stats = pyqtSignal(int, int, int, int)
    progress_update = pyqtSignal(int, int)  # chunks done, total (0 = unknown)
    # current phase as a (translation key, kwargs) pair, so the window can
    # re-render it if the interface language changes mid-translation
    phase_changed = pyqtSignal(str, dict)
    language_detected = pyqtSignal(int, str)  # file index, detected language
    finished_all = pyqtSignal()

    YIELD_EVERY = 0.05  # seconds between hand-overs to the interface thread

    def __init__(self, src_lang, dst_lang, languages, files, output_dir=None,
                 skip_existing=False, engine_id="argos", parent=None):
        super().__init__(parent)
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.languages = languages
        self.files = files
        self.output_dir = output_dir  # None = next to each original
        self.skip_existing = skip_existing
        self.engine_id = engine_id
        self._last_yield = 0.0
        cache_on = QSettings().value("cache_enabled", True, type=bool)
        self.greedy = quality_mode() == "fast"
        self._cache_ttl_days = QSettings().value("cache_ttl_days", 90, type=int)
        self._cache_db_path = TranslationCache.default_db_path() if cache_on else None
        history_on = QSettings().value("history_enabled", True, type=bool)
        self._history_ttl_days = QSettings().value("history_ttl_days", 90, type=int)
        self._history_db_path = (
            TranslationHistory.default_db_path() if history_on else None
        )
        self.batch = None

    def run(self):
        try:
            self.batch = Batch(
                self.src_lang, self.dst_lang, self.languages, self.files,
                output_dir=self.output_dir,
                skip_existing=self.skip_existing,
                engine_id=self.engine_id,
                greedy=self.greedy,
                cache_db_path=self._cache_db_path,
                cache_ttl_days=self._cache_ttl_days,
                history_db_path=self._history_db_path,
                history_ttl_days=self._history_ttl_days,
                listener=self,
                is_cancelled=self.was_cancelled,
            )
            self.batch.run()
        finally:
            # the window leaves its busy state on this signal, so it is
            # emitted from a finally: an unexpected error must never leave the
            # interface stuck with every control disabled
            self.finished_all.emit()

    # --- what the batch reports, as signals ---
    def on_file_started(self, index, path):
        self.file_started.emit(index, path)

    def on_language_detected(self, index, lang):
        self.language_detected.emit(index, lang_text(lang))

    def on_progress(self, done, total):
        self.progress_update.emit(done, total)

    def on_phase(self, key, kwargs):
        self.phase_changed.emit(key, kwargs)
        self._yield_to_ui()

    def on_file_done(self, index, out_path, seconds):
        self.file_done.emit(index, out_path, seconds)

    def on_file_failed(self, index, error):
        self.file_failed.emit(index, error_text(error))

    def on_file_skipped(self, index, out_path):
        self.file_skipped.emit(index, out_path)

    def on_cache_stats(self, index, file_reused, batch_reused, batch_segments):
        self.file_cache_stats.emit(index, file_reused, batch_reused, batch_segments)

    def _yield_to_ui(self):
        """Hands the interpreter over to the interface thread for a moment.

        Not on every page: reading a 400-page book takes about as long as
        one millisecond per page would, so sleeping each time paced the
        phase rather than the work. Yielding at most every YIELD_EVERY
        seconds still gives the window dozens of chances a second to
        repaint, while costing a fixed fraction of the run instead of a
        toll per page."""
        now = time.monotonic()
        if now - self._last_yield >= self.YIELD_EVERY:
            self._last_yield = now
            time.sleep(0.001)
