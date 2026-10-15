"""A translation run as the main window sees it: launching and cancelling
the worker, live progress and phase messages, the remaining-time estimate
and the final summary."""

import os
import time

from PyQt5.QtWidgets import QMessageBox

from argonaut_gui.i18n import lang_text, tr
from argonaut_gui.window.file_list import FILE_REUSED_ROLE, FILE_STATE_ROLE, STATUS_COL
from argonaut_gui.worker import TranslateWorker


class TranslationRunMixin:
    """Batch-run half of MainWindow. Drives the TranslateWorker and keeps
    the progress bar, status label and per-file states in step with it."""

    def release_worker(self):
        """Drops the finished worker of the previous run. Only ever called
        when it is no longer running, and the reference goes with it so
        nothing is left pointing at a deleted object."""
        if self.worker is not None and not self.worker.isRunning():
            self.worker.deleteLater()
            self.worker = None

    def running_workers(self):
        return [
            worker
            for worker in (self.worker, self.downloader)
            if worker is not None and worker.isRunning()
        ]

    def start_translation(self):
        files = self.paths()
        if not files:
            QMessageBox.information(
                self, tr("no_files_title"), tr("no_files_msg")
            )
            return

        src = self.from_combo.currentData()  # None = automatic detection
        dst = self.to_combo.currentData()
        if src is not None:
            if src is dst:
                QMessageBox.warning(
                    self, tr("languages_title"), tr("same_language")
                )
                return
            if src.get_translation(dst) is None:
                QMessageBox.warning(
                    self,
                    tr("no_model_title"),
                    tr("no_model_msg", src=lang_text(src), dst=lang_text(dst)),
                )
                return

        self.results = []
        self.detected = {}  # file index -> detected language
        self._batch_reused = 0
        self._batch_segments = 0
        for path in files:
            item = self._set_file_state(path, "pending")
            if item is not None:
                item.setData(STATUS_COL, FILE_REUSED_ROLE, 0)
                self._render_cache_tooltip(item)
        self._render_batch_cache_tooltip()
        self._cancelling = False
        self.set_busy(True)
        self.progress.setRange(0, 0)  # indeterminate until the first update
        self.progress.setFormat("%p%")

        # the previous run's thread object is a child of this window, so
        # without this every translation would leave one behind for the rest
        # of the session (holding its file list and its batch cache)
        self.release_worker()
        self.worker = TranslateWorker(
            src, dst, self.languages, files,
            output_dir=self.output_dir,
            skip_existing=self.skip_existing_cb.isChecked(),
            engine_id=self.backend,
            parent=self,
        )
        self._status_parts = []
        self.worker.file_started.connect(self.on_file_started)
        self.worker.progress_update.connect(self.on_progress)
        self.worker.phase_changed.connect(self.on_phase_changed)
        self.worker.language_detected.connect(self.on_language_detected)
        self.worker.file_done.connect(self.on_file_done)
        self.worker.file_failed.connect(self.on_file_failed)
        self.worker.file_skipped.connect(self.on_file_skipped)
        self.worker.file_cache_stats.connect(self.on_file_cache_stats)
        self.worker.finished_all.connect(self.on_finished)
        # QThread.finished (not finished_all) fires once the thread has really
        # stopped, which is what a deferred close has to wait for
        self.worker.finished.connect(self.close_when_idle)
        self.worker.start()

    def clear_status(self):
        self._status_parts = []
        # hidden first: what replaces the summary is whatever the window has
        # to say for itself now, which _refresh_ready_state will not write
        # while a summary is still on screen
        self.clear_status_btn.setVisible(False)
        self._refresh_ready_state()

    def set_busy(self, busy):
        if busy:
            self.clear_status_btn.setVisible(False)
        # the running batch keeps the pair it was started with, so leaving
        # these editable lets the window claim a translation that is not the
        # one under way
        self.from_combo.setEnabled(not busy)
        self.to_combo.setEnabled(not busy)
        # coming out of a run does not mean these are usable again: the files
        # or the languages may have changed underneath them
        self.swap_btn.setEnabled(not busy and self.can_swap_languages())
        self.translate_btn.setEnabled(not busy and self.can_translate())
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self.progress.setVisible(busy)

    def cancel_translation(self):
        if self.downloader is not None and self.downloader.isRunning():
            self.downloader.cancel()
            self.cancel_btn.setEnabled(False)
            self.status.setText(tr("cancelling"))
        elif self.worker is not None and self.worker.isRunning():
            # isRunning() guards the click that lands just as the batch ends:
            # without it the window would stay stuck on "cancelling…" for a
            # worker that has already emitted its summary
            self.worker.cancel()
            self._cancelling = True
            self.cancel_btn.setEnabled(False)
            self.show_cancelling()

    def on_file_started(self, index, path):
        if self._cancelling:
            return
        self.reset_eta()
        self._set_file_state(path, "translating")
        self._status_parts = [
            ("translating", {
                "name": os.path.basename(path),
                "index": index + 1,
                "total": len(self.worker.files),
            })
        ]
        self.refresh_status()

    def on_language_detected(self, index, name):
        self.detected[index] = name
        self._status_parts.append(("detected", {"name": name}))
        self.refresh_status()

    def on_phase_changed(self, key, kwargs):
        if self._cancelling:
            return
        # a phase (generating a page, saving) replaces the "translating" base,
        # but not the detected language: that belongs to the file rather than
        # to the phase, and detection happens before the first phase, so
        # dropping it here left the notice on screen for a few milliseconds
        detected = [part for part in self._status_parts if part[0] == "detected"]
        self._status_parts = [(key, kwargs)] + detected
        self.refresh_status()

    def show_cancelling(self):
        """Announces the cancellation without dropping the phase message.
        An engine call already in flight cannot be interrupted — a page of a
        dense book can take a minute to come back — and the page it is on is
        what tells the user the wait is finite rather than a freeze."""
        phase = [part for part in self._status_parts if part[0] != "cancelling"]
        self._status_parts = [("cancelling", {})] + phase
        self.refresh_status()

    def refresh_status(self):
        """Renders the live translation status in the current language."""
        if self._status_parts:
            self.status.setText(
                " — ".join(tr(key, **kwargs) for key, kwargs in self._status_parts)
            )

    def on_progress(self, done, total):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            eta = self.estimate_eta(done, total)
            text = f"%p% — {done}/{total}"
            if eta is not None:
                text += f" — ~{self.format_duration(eta)}"
            self.progress.setFormat(text)
        else:
            self.progress.setRange(0, 0)
            self.reset_eta()

    # --- remaining-time estimation ---
    def reset_eta(self):
        self._eta_total = None

    def estimate_eta(self, done, total):
        """Estimated seconds left, from the average pace since this phase
        started. None while there is not enough signal (or on the sample
        that resets the estimator when the file or phase changes)."""
        now = time.monotonic()
        if self._eta_total != total or done < self._eta_done0:
            self._eta_total = total
            self._eta_t0 = now
            self._eta_done0 = done
            return None
        progressed = done - self._eta_done0
        elapsed = now - self._eta_t0
        if progressed <= 0 or elapsed < 1.0 or done >= total:
            return None
        return (total - done) * elapsed / progressed

    def on_file_done(self, index, out_path, seconds):
        self._set_file_state(self.worker.files[index], "done")
        text = f"{out_path}  ({self.format_duration(seconds)})"
        if index in self.detected:
            text += f"  ({tr('detected', name=self.detected[index])})"
        self.results.append(("ok", text))

    @staticmethod
    def format_duration(seconds):
        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days:
            return f"{days}d {hours}h" if hours else f"{days}d"
        if hours:
            return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"
        return f"{minutes:02d}:{secs:02d}"

    def on_file_failed(self, index, error):
        self._set_file_state(self.worker.files[index], "failed")
        self.results.append(("error", error))

    def on_file_skipped(self, index, out_path):
        self._set_file_state(self.worker.files[index], "skipped")
        self.results.append(("skipped", out_path))

    def on_file_cache_stats(self, index, file_reused, batch_reused, batch_segments):
        """Records how many segments a finished file reused from the shared
        cache, as a tooltip on its Status cell, and the running batch totals
        on the file-list summary. The batch figures are what the summary
        reports once the run ends."""
        item = self._item_for_path(self.worker.files[index])
        if item is not None:
            item.setData(STATUS_COL, FILE_REUSED_ROLE, file_reused)
            self._render_cache_tooltip(item)
        self._batch_reused = batch_reused
        self._batch_segments = batch_segments
        self._render_batch_cache_tooltip()

    def cache_summary_line(self):
        """The batch's cache reuse as a line for the final summary, or None
        when nothing was reused (the cache switched off, or a first run with
        no repeated text, where a "0 of 340" line would be pure noise)."""
        if not self._batch_reused or not self._batch_segments:
            return None
        return tr(
            "cache_reused_summary",
            reused=self._batch_reused,
            total=self._batch_segments,
            percent=round(100 * self._batch_reused / self._batch_segments),
        )

    def _render_cache_tooltip(self, item):
        reused = item.data(STATUS_COL, FILE_REUSED_ROLE)
        item.setToolTip(
            STATUS_COL,
            tr("cache_reused_file", count=reused) if reused else "",
        )

    def _render_batch_cache_tooltip(self):
        self.total_label.setToolTip(
            tr("cache_reused_batch", count=self._batch_reused)
            if self._batch_reused else ""
        )

    def on_finished(self):
        self._cancelling = False
        cancelled = self.worker is not None and self.worker.was_cancelled()
        if cancelled:
            # the in-progress file and any not-yet-started ones never got an
            # outcome, so they'd stay stuck on "translating"/"pending"
            for path in self.worker.files:
                item = self._item_for_path(path)
                if item is not None and item.data(
                    STATUS_COL, FILE_STATE_ROLE
                ) in ("pending", "translating"):
                    self._set_file_state(path, "cancelled")
        self._status_parts = []  # the live message is replaced by the summary
        self.set_busy(False)
        ok = [r for kind, r in self.results if kind == "ok"]
        skipped = [r for kind, r in self.results if kind == "skipped"]
        errors = [r for kind, r in self.results if kind == "error"]
        lines = []
        if cancelled:
            lines.append(tr("cancelled"))
        if ok:
            lines.append(tr("translated_header"))
            lines.extend(f"  → {p}" for p in ok)
        if skipped:
            lines.append(tr("skipped_header"))
            lines.extend(f"  ↷ {p}" for p in skipped)
        if errors:
            lines.append(tr("errors_header"))
            lines.extend(f"  ✗ {e}" for e in errors)
        # last line, and only when there is something to report: how much of
        # the work the cache answered. A cancelled run keeps it — the segments
        # it did reuse are as real as the pages it produced
        cache_line = self.cache_summary_line()
        if cache_line is not None and lines:
            lines.append(cache_line)
        self.status.setText("\n".join(lines) or tr("ready"))
        self.clear_status_btn.setVisible(bool(lines))
