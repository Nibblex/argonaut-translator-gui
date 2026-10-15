from argonaut_gui.i18n import tr
from argonaut_gui.window.about_dialog import AboutDialog, diagnostic_report


def make_dialog(qtbot, backend="argos", language_count=4):
    dialog = AboutDialog(backend, language_count)
    qtbot.addWidget(dialog)
    return dialog


def test_diagnostic_report_covers_the_environment(qapp):
    report = diagnostic_report("nllb", 32)
    lines = report.splitlines()
    assert lines[0].startswith("Argonaut")
    assert "Python " in report and "Qt " in report and "PyQt " in report
    assert "OS: " in report
    assert "argos-translate-files" in report and "pymupdf" in report
    assert "Engine: NLLB-200 · Languages: 32" in report
    assert "NLLB model installed: no" in report  # tests run sandboxed
    assert "Cache: " in report
    assert "History: " in report
    assert "Argos data: " in report


def test_missing_dependency_reports_an_unknown_version():
    from argonaut_gui.window.about_dialog import _dist_version

    # a broken environment must not break the About dialog itself
    assert _dist_version("argonaut-nonexistent-dep") == "?"


def test_about_tab_shows_the_summary_and_support_links(qtbot):
    dialog = make_dialog(qtbot)
    assert dialog.tabs.count() == 3
    assert dialog.tabs.tabText(0) == tr("about_tab_about")
    assert dialog.tabs.tabText(1) == tr("about_tab_details")
    assert dialog.tabs.tabText(2) == tr("about_tab_credits")
    assert ".pdf" in dialog.about_label.text()
    assert "github.com/Nibblex/Argonaut/issues" in dialog.links_label.text()
    assert "github.com/Nibblex/Argonaut/releases" in dialog.links_label.text()


def test_credits_list_the_licenses_that_matter(qtbot):
    dialog = make_dialog(qtbot)
    text = dialog.credits_label.text()
    # the two attributions with legal weight
    assert "PyMuPDF" in text and "AGPL-3.0" in text
    assert "NLLB-200" in text and "CC-BY-NC" in text
    # and the rest of the stack
    for name in ("Argos Translate", "PyQt5", "CTranslate2", "langdetect"):
        assert name in text


def test_copy_button_copies_the_report_and_confirms(qtbot, monkeypatch):
    from PyQt5.QtWidgets import QApplication

    monkeypatch.setattr(AboutDialog, "COPY_FEEDBACK_MS", 10)
    dialog = make_dialog(qtbot)
    dialog.copy_btn.click()
    assert QApplication.clipboard().text() == dialog.report.toPlainText()
    assert dialog.copy_btn.text() == tr("about_copied")
    # the confirmation is temporary: the button offers to copy again
    qtbot.waitUntil(lambda: dialog.copy_btn.text() == tr("about_copy"), timeout=2000)


def test_dialog_renders_in_the_current_language(qtbot):
    from argonaut_gui import i18n

    i18n.set_language("es")
    dialog = make_dialog(qtbot)
    assert dialog.tabs.tabText(1) == "Detalles"
    assert dialog.copy_btn.text() == "Copiar información de diagnóstico"
    assert "se apoya en estos proyectos" in dialog.credits_label.text()
