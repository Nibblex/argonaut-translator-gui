"""Interface internationalization: current language, preference persistence
with QSettings and the strings themselves.

The strings live in locales/<code>.json, one file per interface language,
read the first time that language is asked for. English is the default and
acts as a fallback: if a language is missing a key, the English string is
used.

Next to them, locales/language_names.json holds the names of the
translation languages in each interface language, so "French" reads
"Francés" in a Spanish interface. Unlike the locale files that one is
generated, by tools/gen_language_names.py, and a code missing from it
keeps the English name the engine reports.
"""

import json
import unicodedata
from collections.abc import Mapping
from importlib.resources import files

from PyQt5.QtCore import QLocale, QSettings

DEFAULT = "en"

# code -> name shown in the menu (in its own language)
LANGUAGES = [
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("ru", "Русский"),
    ("zh", "中文"),
    ("ja", "日本語"),
    ("nl", "Nederlands"),
    ("pl", "Polski"),
    ("tr", "Türkçe"),
]

_LOCALES = files("argonaut_gui") / "locales"
# the one file in there that is not an interface language: it is generated,
# and it holds a section for every language rather than being one of them
_NAMES_FILE = "language_names.json"


def _read(resource):
    return json.loads(resource.read_text(encoding="utf-8"))


class _Strings(Mapping):
    """The strings of every interface language shipped, each file read the
    first time its language is asked for: starting in Polish should not
    also parse the other eleven.

    A Mapping rather than a plain dict so this still reads like the table
    it replaced (`code in STRINGS`, `STRINGS[code][key]`), and so that the
    languages on offer are the files actually shipped rather than a list
    that can drift from them."""

    def __init__(self):
        self._codes = sorted(
            entry.name.removesuffix(".json")
            for entry in _LOCALES.iterdir()
            if entry.name.endswith(".json") and entry.name != _NAMES_FILE
        )
        self._loaded = {}

    def __getitem__(self, code):
        if code not in self._codes:
            raise KeyError(code)
        if code not in self._loaded:
            self._loaded[code] = _read(_LOCALES / f"{code}.json")
        return self._loaded[code]

    def __contains__(self, code):
        # Mapping would answer this by reading the file: checking whether a
        # language exists is exactly what happens for each of the desktop's
        # preferences at startup, and none of them needs the strings
        return code in self._codes

    def __iter__(self):
        return iter(self._codes)

    def __len__(self):
        return len(self._codes)


STRINGS = _Strings()

_names = None


def language_names():
    """The whole generated table, read once on first use. English is
    absent from it on purpose: there the engine's own name is used."""
    global _names
    if _names is None:
        _names = _read(_LOCALES / _NAMES_FILE)
    return _names


_current = DEFAULT


def current_language():
    return _current


def system_language():
    """The interface language the desktop asks for, or None when it asks
    for one Argonaut does not speak.

    Qt reports the user's ordered preferences rather than a single locale
    ("es-AR", "es", "en-US"), so the first entry with strings behind it
    wins and the rest fall through. Region and script are dropped: the
    strings are per language, so es-AR and es-ES read the same, and every
    Chinese variant gets the one Chinese translation there is."""
    for tag in QLocale.system().uiLanguages():
        code = tag.replace("-", "_").split("_")[0].lower()
        if code in STRINGS:
            return code
    return None


def load_language():
    """Resolves the language at startup: the one the user chose, otherwise
    the desktop's, otherwise English.

    An automatic match is deliberately not saved. Only choosing from the
    Language menu writes the setting, so a machine whose locale changes is
    followed until the user states a preference — and from then on that
    preference is what holds."""
    global _current
    saved = QSettings().value("ui_language", "")
    if saved in STRINGS:
        _current = saved
    else:
        _current = system_language() or DEFAULT
    return _current


def set_language(code):
    global _current
    if code in STRINGS:
        _current = code
        QSettings().setValue("ui_language", code)


def tr(key, **kwargs):
    text = STRINGS[_current].get(key) or STRINGS[DEFAULT].get(key, key)
    return text.format(**kwargs) if kwargs else text


def language_name(code, english):
    """The name of a translation language in the interface language.

    `english` is the name the engine gives it, which is what's returned
    when the interface is in English and whenever the table has no entry
    for the code — a language the engines add later still reads as it
    always did instead of disappearing behind its code."""
    return language_names().get(_current, {}).get(code) or english


def lang_text(lang):
    """language_name() for an Argos or NLLB Language: anything with a
    `.code` whose `str()` is its English name."""
    return language_name(lang.code, str(lang))


def name_sort_key(text):
    """Sort key that files accents under their base letter (Á with A), so
    a list of names stays alphabetical in Spanish, French and the rest."""
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()


def sorted_languages(languages):
    """Languages ordered by the name the user will read."""
    return sorted(languages, key=lambda lang: name_sort_key(lang_text(lang)))


def pair_text(pkg):
    """An Argos package's language pair as the user reads it, in the
    interface language."""
    return (
        f"{language_name(pkg.from_code, pkg.from_name)}"
        f" → {language_name(pkg.to_code, pkg.to_name)}"
    )


def sorted_packages(pkgs):
    """Argos packages ordered by their language pair as the user reads it."""
    return sorted(
        pkgs,
        key=lambda p: (
            name_sort_key(language_name(p.from_code, p.from_name)),
            name_sort_key(language_name(p.to_code, p.to_name)),
        ),
    )
