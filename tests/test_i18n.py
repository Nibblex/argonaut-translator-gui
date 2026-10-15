import re

import pytest

from argonaut_gui import i18n
from argonaut_gui.i18n import (
    DEFAULT,
    LANGUAGES,
    STRINGS,
    current_language,
    lang_text,
    language_name,
    language_names,
    load_language,
    name_sort_key,
    set_language,
    sorted_languages,
    tr,
)
from tests.conftest import FakeLanguage


def test_default_language_is_english():
    assert current_language() == "en"
    assert tr("translate") == "Translate"


def test_set_language_switches_strings():
    set_language("es")
    assert current_language() == "es"
    assert tr("translate") == "Traducir"


def test_set_language_persists_and_reloads():
    set_language("fr")
    assert load_language() == "fr"


def test_unknown_language_is_ignored():
    set_language("xx")
    assert current_language() == DEFAULT


def test_missing_key_falls_back_to_english(monkeypatch):
    set_language("es")
    monkeypatch.delitem(STRINGS["es"], "translate")
    assert tr("translate") == "Translate"


def test_unknown_key_returns_the_key():
    assert tr("nonexistent_key") == "nonexistent_key"


def test_menu_lists_every_language_shipped():
    """STRINGS reports the locale files that are actually there, so this
    catches both a menu entry with no strings behind it and a translation
    that was added without being offered."""
    assert sorted(code for code, _ in LANGUAGES) == sorted(STRINGS)


def test_strings_are_read_only_when_their_language_is_used():
    fresh = type(STRINGS)()
    assert "es" in fresh and not fresh._loaded  # knowing it exists reads nothing
    fresh["es"]
    assert set(fresh._loaded) == {"es"}


def test_a_language_with_no_locale_file_is_a_missing_key():
    """Asked for a language it does not ship, STRINGS raises rather than
    going looking for a file that is not there."""
    with pytest.raises(KeyError):
        STRINGS["xx"]


@pytest.mark.parametrize("code", [code for code, _ in LANGUAGES])
def test_all_languages_have_all_english_keys(code):
    assert set(STRINGS[code]) == set(STRINGS["en"])


@pytest.mark.parametrize("code", [code for code, _ in LANGUAGES])
def test_placeholders_match_english(code):
    placeholders = lambda s: sorted(re.findall(r"{(\w+)}", s))
    for key, english in STRINGS["en"].items():
        assert placeholders(STRINGS[code][key]) == placeholders(english), key


@pytest.mark.parametrize("code", [code for code, _ in LANGUAGES])
def test_about_text_renders(code):
    set_language(code)
    text = tr("about_text", version="9.9.9", formats=".txt .pdf")
    assert "Argonaut 9.9.9" in text
    assert ".txt .pdf" in text
    assert "github.com/Nibblex/Argonaut" in text


# menus that share a menu bar (or a parent menu) compete for the same
# keyboard accelerators, so each group must use a distinct letter
ACCELERATOR_GROUPS = (
    ("menu bar", ["menu_file", "menu_settings", "menu_help"]),
    ("Settings", ["menu_preferences", "menu_engine", "menu_cache",
                  "menu_history"]),
    # what the user prefers, gathered apart from how translation is configured
    ("Preferences", ["menu_language", "menu_theme"]),
)


def accelerator(label):
    """The letter Qt underlines, or None when the label declares none."""
    marker = label.find("&")
    return label[marker + 1].lower() if 0 <= marker < len(label) - 1 else None


@pytest.mark.parametrize("code", [code for code, _ in LANGUAGES])
@pytest.mark.parametrize("group,keys", ACCELERATOR_GROUPS)
def test_menu_accelerators_are_present_and_unique(code, group, keys):
    """A duplicate accelerator silently breaks keyboard navigation: Qt gives
    the key to one menu and the other becomes unreachable."""
    letters = []
    for key in keys:
        letter = accelerator(STRINGS[code][key])
        assert letter is not None, f"{code}/{key} declares no accelerator"
        letters.append(letter)
    assert len(set(letters)) == len(letters), (
        f"{code} {group}: duplicate accelerator in {dict(zip(keys, letters))}"
    )


# --- names of the translation languages ---

def test_language_names_follow_the_interface_language():
    set_language("es")
    assert language_name("fr", "French") == "Francés"
    set_language("ja")
    assert language_name("fr", "French") == "フランス語"


def test_language_names_stay_english_in_english():
    assert current_language() == "en"
    assert language_name("fr", "French") == "French"


def test_an_unknown_code_keeps_the_engine_name():
    """A language the engines add later must still read as it always did
    rather than turning into its bare code."""
    set_language("es")
    assert language_name("xx", "Klingon") == "Klingon"


def test_lang_text_reads_the_language_object():
    set_language("de")
    assert lang_text(FakeLanguage("pt", "Portuguese")) == "Portugiesisch"


def test_argos_only_codes_are_covered():
    """Argos names Brazilian Portuguese "pb" and traditional Chinese "zt",
    and NLLB says "no" where Argos says "nb": none of them is ISO 639-1,
    so nothing but the table can name them."""
    set_language("es")
    assert language_name("pb", "Portuguese (Brazil)") == "Portugués (Brasil)"
    assert language_name("zt", "Chinese (traditional)") == "Chino (tradicional)"
    assert language_name("nb", "Norwegian") == language_name("no", "Norwegian")


@pytest.mark.parametrize("code", [code for code, _ in LANGUAGES if code != DEFAULT])
def test_every_interface_language_names_the_same_languages(code):
    assert set(language_names()[code]) == set(language_names()["es"])


def test_english_has_no_table():
    """It would only repeat what the engines already report in English."""
    assert DEFAULT not in language_names()


def test_every_nllb_language_has_a_name():
    from argonaut_cli import nllb

    missing = {code for code, _, _ in nllb.LANGUAGES} - set(language_names()["es"])
    assert not missing


def test_languages_sort_by_their_translated_name():
    langs = [
        FakeLanguage("de", "German"),
        FakeLanguage("en", "English"),
        FakeLanguage("es", "Spanish"),
    ]
    assert [str(lang) for lang in sorted_languages(langs)] == [
        "English", "German", "Spanish",
    ]
    set_language("es")
    # Alemán, Español, Inglés — the English order would be German first
    assert [str(lang) for lang in sorted_languages(langs)] == [
        "German", "Spanish", "English",
    ]


def test_accents_sort_with_their_base_letter():
    assert name_sort_key("Árabe") < name_sort_key("Bengalí")
    assert name_sort_key("Árabe") == "arabe"


# --- picking the language at startup ---

class FakeLocale:
    """Stands in for QLocale.system(), which reports the desktop's ordered
    language preferences."""

    def __init__(self, tags):
        self.tags = tags

    def uiLanguages(self):
        return list(self.tags)


def fake_system_locale(monkeypatch, tags):
    monkeypatch.setattr(
        i18n, "QLocale", type("QLocale", (), {"system": staticmethod(lambda: FakeLocale(tags))})
    )


def forget_chosen_language():
    """Clears the stored preference, standing in for a first launch. The
    fixture that resets the language between tests goes through
    set_language, which saves it, so the automatic path needs it gone."""
    from PyQt5.QtCore import QSettings

    QSettings().remove("ui_language")


def test_system_language_takes_the_first_one_it_speaks(monkeypatch):
    fake_system_locale(monkeypatch, ["ca-ES", "fr-FR", "en-US"])
    # Catalan has no strings, so the next preference wins over plain English
    assert i18n.system_language() == "fr"


def test_system_language_ignores_region_and_script(monkeypatch):
    fake_system_locale(monkeypatch, ["pt-BR"])
    assert i18n.system_language() == "pt"
    fake_system_locale(monkeypatch, ["zh-Hant-TW"])
    assert i18n.system_language() == "zh"  # the one Chinese translation there is


def test_system_language_is_none_when_unsupported(monkeypatch):
    fake_system_locale(monkeypatch, ["ca-ES", "eu", "C"])
    assert i18n.system_language() is None


def test_startup_follows_the_desktop_when_nothing_was_chosen(monkeypatch):
    forget_chosen_language()
    fake_system_locale(monkeypatch, ["ja-JP"])
    assert load_language() == "ja"
    assert current_language() == "ja"


def test_startup_falls_back_to_english_for_an_unknown_locale(monkeypatch):
    forget_chosen_language()
    fake_system_locale(monkeypatch, ["is-IS"])
    assert load_language() == DEFAULT


def test_a_chosen_language_beats_the_desktop(monkeypatch):
    """Once the user states a preference it holds, whatever the locale says."""
    fake_system_locale(monkeypatch, ["ja-JP"])
    set_language("pl")
    assert load_language() == "pl"


def test_the_detected_language_is_not_written_to_the_settings(monkeypatch):
    """Saving it would freeze the first locale seen: a machine whose language
    changes should be followed until the user picks one on purpose."""
    from PyQt5.QtCore import QSettings

    forget_chosen_language()
    fake_system_locale(monkeypatch, ["de-DE"])
    assert load_language() == "de"
    assert not QSettings().value("ui_language", "")

    set_language("de")  # choosing it explicitly does save it
    assert QSettings().value("ui_language", "") == "de"


def test_a_saved_language_that_no_longer_exists_falls_back(monkeypatch):
    from PyQt5.QtCore import QSettings

    QSettings().setValue("ui_language", "xx")  # e.g. removed in a later version
    fake_system_locale(monkeypatch, ["it-IT"])
    assert load_language() == "it"
