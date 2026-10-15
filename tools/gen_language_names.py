"""Writes src/argonaut_gui/locales/language_names.json from CLDR data: the name of
every language Argonaut can translate, in every language its interface
speaks.

Run by hand when an interface language or a translation language is added:

    pip install babel && python tools/gen_language_names.py

The result is committed, so Babel stays out of the runtime dependencies.
The interface strings are not generated: those are the hand-written
locales/<code>.json files this never touches.

Keys are the codes Argos and NLLB use, which are ISO 639-1 except for
Argos' "pb" (Brazilian Portuguese) and "zt" (traditional Chinese), and
NLLB's "no" where Argos says "nb". English gets no table: there the
engine's own name is used, which is already English.
"""

import json
import os

from babel import Locale

OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "argonaut_gui", "locales", "language_names.json",
)

UI = ["es", "fr", "de", "it", "pt", "ru", "zh", "ja", "nl", "pl", "tr"]
UI_LOCALE = {"zh": "zh_Hans"}

CODES = [
    ("ar", "ar"), ("az", "az"), ("bg", "bg"), ("bn", "bn"), ("ca", "ca"),
    ("cs", "cs"), ("da", "da"), ("de", "de"), ("el", "el"), ("en", "en"),
    ("eo", "eo"), ("es", "es"), ("et", "et"), ("eu", "eu"), ("fa", "fa"),
    ("fi", "fi"), ("fr", "fr"), ("ga", "ga"), ("gl", "gl"), ("he", "he"),
    ("hi", "hi"), ("hu", "hu"), ("id", "id"), ("it", "it"), ("ja", "ja"),
    ("ko", "ko"), ("ky", "ky"), ("lt", "lt"), ("lv", "lv"), ("ms", "ms"),
    ("nb", "no"), ("nl", "nl"), ("no", "no"), ("pb", "pt_BR"), ("pl", "pl"),
    ("pt", "pt"), ("ro", "ro"), ("ru", "ru"), ("sk", "sk"), ("sl", "sl"),
    ("sq", "sq"), ("sv", "sv"), ("sw", "sw"), ("th", "th"), ("tl", "fil"),
    ("tr", "tr"), ("uk", "uk"), ("ur", "ur"), ("vi", "vi"), ("zh", "zh"),
    ("zt", "zh_Hant"),
]

# CLDR composes "<language> (<script>)" without agreeing in gender, and puts
# a Western space before the parenthesis in Chinese and Japanese
OVERRIDES = {
    "ru": {"zt": "Китайский (традиционный)"},
    "pl": {"zt": "Chiński (tradycyjny)"},
    "de": {"zt": "Chinesisch (traditionell)"},
    "zh": {"pb": "葡萄牙语（巴西）", "zt": "中文（繁体）"},
    "ja": {"pb": "ポルトガル語（ブラジル）", "zt": "中国語（繁体字）"},
}

def capitalize(text, ui):
    if ui == "tr" and text[:1] == "i":
        return "İ" + text[1:]  # Turkish dotted capital I
    return text[:1].upper() + text[1:]


names = {}
for ui in UI:
    loc = Locale.parse(UI_LOCALE.get(ui, ui))
    names[ui] = {
        code: OVERRIDES.get(ui, {}).get(code)
        or capitalize(Locale.parse(cldr).get_display_name(loc), ui)
        for code, cldr in CODES
    }

with open(OUTPUT, "w", encoding="utf-8") as out:
    json.dump(names, out, ensure_ascii=False, indent=2)
    out.write("\n")
