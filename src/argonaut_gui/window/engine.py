"""Engine side of the main window: backend and CPU-thread choice, the
language combos, the cache menu, package management and the NLLB model
download."""

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QMessageBox

import argostranslate.settings
import argostranslate.translate

from argonaut_cli import nllb
from argonaut_cli.history import TranslationHistory
from argonaut_cli.translation import TranslationCache

from argonaut_gui.i18n import lang_text, sorted_languages, tr
from argonaut_gui.package_dialog import PackageDialog
from argonaut_gui.window.file_list import human_size
from argonaut_gui.window.history_dialog import HistoryDialog
from argonaut_gui.worker import ModelDownloadWorker, quality_mode, speed_text, speed_units

# Argos' own beam width, captured before any fast-mode override touches it
ARGOS_DEFAULT_BEAM = argostranslate.settings.beam_size


class EngineMixin:
    """Engine-and-settings half of MainWindow. Operates on the menus and
    combos its __init__ creates."""

    def load_languages(self):
        if self.backend == "nllb":
            return nllb.get_installed_languages(
                threads=self.cpu_threads,
                beam_size=1 if quality_mode() == "fast" else nllb.NllbEngine.DEFAULT_BEAM,
            )
        return argostranslate.translate.get_installed_languages()

    # --- translation quality (beam width) ---
    def apply_quality_mode(self):
        """Points Argos at the beam the current mode asks for. Argos reads
        the setting on every call; the NLLB engine instead receives its beam
        when load_languages constructs it."""
        argostranslate.settings.beam_size = (
            1 if quality_mode() == "fast" else ARGOS_DEFAULT_BEAM
        )

    def change_quality_mode(self, mode):
        if mode == quality_mode():
            return
        QSettings().setValue("quality_mode", mode)
        self.apply_quality_mode()
        self.languages = self.load_languages()  # NLLB picks up its new beam
        self.reload_language_combos()

    def change_cpu_threads(self, n):
        if n == self.cpu_threads:
            return
        self.cpu_threads = n
        QSettings().setValue("cpu_threads", n)
        argostranslate.settings.intra_threads = n
        # already-loaded CTranslate2 translators keep their thread count, so
        # fresh Language objects are created and the value applies on the
        # next translation, when the translator is (re)instantiated
        argostranslate.translate.get_installed_languages.cache_clear()
        self.languages = self.load_languages()
        self.reload_language_combos()

    def show_package_dialog(self):
        dialog = PackageDialog(self)
        dialog.packages_changed.connect(self.on_packages_changed)
        dialog.exec_()

    def on_packages_changed(self):
        self.languages = self.load_languages()
        self.reload_language_combos()
        self._refresh_ready_state()

    def translate_blocker(self):
        """What stands between the window and a translation, as the message
        that says so, or None when nothing does.

        One answer serves both the Translate button and the status line: a
        greyed-out button with "Ready." under it is the window refusing
        without saying why, and two separate rules would eventually disagree
        about which of them was right.

        With the source left on "Detect language" the pair is only known once
        each file has been read, so there is nothing to answer for here beyond
        the target; a file that turns out to have no model fails as that file,
        which is the only place it can be known.

        The engine is asked rather than the installed packages listed, and the
        two are not the same question. Argos composes a pair it has no package
        for out of the ones it has, through English: with the usual en↔X set
        installed, Albanian to Spanish is a CompositeTranslation that really
        does translate, and only a language with no route at all comes back
        None. NLLB, being one model for every pair, never does.
        """
        if not self.languages:
            return "no_packages", {}
        if not self.file_list.topLevelItemCount():
            return "no_files_msg", {}
        src = self.from_combo.currentData()
        dst = self.to_combo.currentData()
        if src is None:  # automatic detection
            return None
        if src is dst:
            return "same_language", {}
        if src.get_translation(dst) is None:
            return "no_model_msg", {"src": lang_text(src), "dst": lang_text(dst)}
        return None

    def can_translate(self):
        return self.translate_blocker() is None

    def pivot_note(self):
        """The warning for a pair that only reaches its target through a third
        language, or None for one that goes straight there.

        Argos composes a pair it has no package for out of two that it has, so
        the text is translated twice and what the first pass loses the second
        cannot put back. It does translate, so this is a note rather than a
        refusal — but left unsaid, the detour reads as the engine being bad at
        the language. NLLB never lands here: one model covers every pair.
        """
        src = self.from_combo.currentData()
        dst = self.to_combo.currentData()
        if src is None or dst is None or src is dst:
            return None
        translation = src.get_translation(dst)
        if not isinstance(translation, argostranslate.translate.CompositeTranslation):
            return None
        return "pivot_pair", {
            "src": lang_text(src),
            "dst": lang_text(dst),
            # the language it goes through, named by the hop that ends there
            "via": lang_text(translation.t1.to_lang),
        }

    def can_swap_languages(self):
        """Swapping needs a source language to swap. On "Detect language"
        there is none to move across, and the button did nothing at all —
        which looked like it was broken rather than inapplicable."""
        return self.from_combo.currentIndex() > 0

    def _refresh_ready_state(self):
        """Reflects whether a translation can start, and why not when it
        cannot. A no-op while a worker runs (the busy state owns the controls)
        and never clobbers a finished batch's summary."""
        if self.running_workers():
            return
        self.swap_btn.setEnabled(self.can_swap_languages())
        blocker = self.translate_blocker()
        self.translate_btn.setEnabled(blocker is None)
        if not self.clear_status_btn.isVisible():
            # a reason it cannot run outranks a caveat about how it will
            key, kwargs = blocker or self.pivot_note() or ("ready", {})
            self.status.setText(tr(key, **kwargs))

    def change_backend(self, name):
        if name == self.backend:
            return
        if name == "nllb" and not nllb.is_model_installed():
            answer = QMessageBox.question(
                self,
                tr("nllb_download_title"),
                tr("nllb_download_msg", size=nllb.MODEL_SIZE_MB),
            )
            if answer != QMessageBox.Yes:
                self.argos_action.setChecked(True)
                return
            self.start_model_download()
            return
        self.apply_backend(name)

    def apply_backend(self, name):
        self.backend = name
        QSettings().setValue("backend", name)
        self.languages = self.load_languages()
        self.reload_language_combos()
        (self.nllb_action if name == "nllb" else self.argos_action).setChecked(True)
        self.nllb_remove_action.setEnabled(nllb.is_model_installed())
        self.update_engine_label()
        self.clear_status_btn.setVisible(False)  # the change replaces any summary
        self._refresh_ready_state()

    def update_engine_label(self):
        name = "NLLB-200" if self.backend == "nllb" else "Argos Translate"
        self.engine_label.setText(tr("engine_status", name=name))

    # --- translation cache ---
    @staticmethod
    def cache_enabled():
        return QSettings().value("cache_enabled", True, type=bool)

    def toggle_cache(self, enabled):
        QSettings().setValue("cache_enabled", enabled)

    def refresh_cache_menu(self):
        """Shows the live size of the cache file, so it is refreshed each
        time the menu opens. Expiry and clearing stay available with the
        cache disabled: the file outlives the setting, and it is precisely
        when it is off that one wants to reclaim its space."""
        count, size = TranslationCache.db_info(TranslationCache.default_db_path())
        self.cache_size_action.setText(
            tr("cache_size_info", size=human_size(size), count=count)
        )

    def change_cache_ttl(self, days):
        QSettings().setValue("cache_ttl_days", days)

    def change_speed_units(self, units):
        QSettings().setValue("speed_units", units)

    def clear_cache(self):
        db_path = TranslationCache.default_db_path()
        count, _ = TranslationCache.db_info(db_path)
        answer = QMessageBox.question(
            self,
            tr("cache_clear_title"),
            tr("cache_clear_msg", count=count),
        )
        if answer != QMessageBox.Yes:
            return
        deleted = TranslationCache.purge_db(db_path)
        self.refresh_cache_menu()
        self.status.setText(tr("cache_cleared", count=deleted))
        self.clear_status_btn.setVisible(True)

    # --- translation history ---
    @staticmethod
    def history_enabled():
        return QSettings().value("history_enabled", True, type=bool)

    def toggle_history(self, enabled):
        QSettings().setValue("history_enabled", enabled)

    def refresh_history_menu(self):
        """Shows the live size of the history file, like the cache menu.
        Viewing and clearing stay available with recording disabled: the
        file outlives the setting."""
        count, size = TranslationHistory.db_info(
            TranslationHistory.default_db_path()
        )
        self.history_size_action.setText(
            tr("hist_size_info", size=human_size(size), count=count)
        )

    def change_history_ttl(self, days):
        QSettings().setValue("history_ttl_days", days)

    def show_history_dialog(self):
        dialog = HistoryDialog(parent=self)
        dialog.history_cleared.connect(self.refresh_history_menu)
        dialog.exec_()
        self.refresh_history_menu()

    def clear_history(self):
        db_path = TranslationHistory.default_db_path()
        count, _ = TranslationHistory.db_info(db_path)
        answer = QMessageBox.question(
            self, tr("hist_title"), tr("hist_clear_msg", count=count)
        )
        if answer != QMessageBox.Yes:
            return
        deleted = TranslationHistory.purge_db(db_path)
        self.refresh_history_menu()
        self.status.setText(tr("hist_cleared", count=deleted))
        self.clear_status_btn.setVisible(True)

    # --- NLLB model ---
    def remove_nllb_model(self):
        answer = QMessageBox.question(
            self,
            tr("nllb_download_title"),
            tr("nllb_remove_msg", size=nllb.MODEL_SIZE_MB),
        )
        if answer != QMessageBox.Yes:
            return
        if self.backend == "nllb":
            self.apply_backend("argos")
        nllb.remove_model()
        self.nllb_remove_action.setEnabled(False)
        self.status.setText(tr("nllb_removed"))

    def start_model_download(self):
        if self.downloader is not None and not self.downloader.isRunning():
            self.downloader.deleteLater()  # same as the translation worker
            self.downloader = None
        self.downloader = ModelDownloadWorker(parent=self)
        self.downloader.progress.connect(self.on_download_progress)
        self.downloader.download_finished.connect(self.on_download_finished)
        self.downloader.finished.connect(self.close_when_idle)
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.status.setText(tr("downloading_model"))
        self.downloader.start()

    def on_download_progress(self, done, total, speed):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        text = f"%p% — {done}/{total} MB"
        rate = speed_text(speed, speed_units())
        if rate:
            text += f" — {rate}"
        self.progress.setFormat(text)

    def on_download_finished(self, ok, error):
        self.set_busy(False)
        if ok:
            self.apply_backend("nllb")
        else:
            self.argos_action.setChecked(True)
            self.status.setText(tr("cancelled") if not error else tr("ready"))
            if error:
                QMessageBox.warning(
                    self,
                    tr("nllb_download_title"),
                    tr("download_failed", error=error),
                )

    # --- language combos ---
    def reload_language_combos(self):
        """Repopulates both combos with the current backend's languages,
        keeping the selection when the language exists in both. Also how
        the combos follow a change of interface language: the names, and
        with them the order, are those of the language now in force."""
        src = self.from_combo.currentData()
        dst = self.to_combo.currentData()
        self.from_combo.clear()
        self.to_combo.clear()
        self.from_combo.addItem(tr("detect_language"), None)
        for lang in sorted_languages(self.languages):
            self.from_combo.addItem(lang_text(lang), lang)
            self.to_combo.addItem(lang_text(lang), lang)
        self.select_defaults()
        if src is not None:
            self._select_language(self.from_combo, src.code, first=1)
        if dst is not None:
            self._select_language(self.to_combo, dst.code, first=0)

    @staticmethod
    def _select_language(combo, code, first):
        for i in range(first, combo.count()):
            if combo.itemData(i).code == code:
                combo.setCurrentIndex(i)
                return

    def select_defaults(self):
        self.from_combo.setCurrentIndex(0)  # Detect language
        self._select_language(self.to_combo, "es", first=0)

    def swap_languages(self):
        # the source combo has "Detect language" at index 0
        fi = self.from_combo.currentIndex()
        ti = self.to_combo.currentIndex()
        if fi == 0:
            return
        self.from_combo.setCurrentIndex(ti + 1)
        self.to_combo.setCurrentIndex(fi - 1)
