# Argonaut

[![PyPI](https://img.shields.io/pypi/v/argonaut-translator-gui)](https://pypi.org/project/argonaut-translator-gui/)
[![Python versions](https://img.shields.io/pypi/pyversions/argonaut-translator-gui)](https://pypi.org/project/argonaut-translator-gui/)
[![License: GPL-3.0](https://img.shields.io/github/license/Nibblex/argonaut-translator-gui)](LICENSE)
[![Release](https://img.shields.io/github/v/tag/Nibblex/argonaut-translator-gui?label=release)](https://github.com/Nibblex/argonaut-translator-gui/tags)

Argonaut translates documents — `.txt` `.docx` `.odt` `.odp` `.pptx` `.epub`
`.html` `.srt` `.pdf` — without sending them anywhere. Translation runs
entirely on your own machine, so it works with no connection and your
documents never leave the computer.

This page is the user manual. If you want to build or contribute to
Argonaut, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Installing

```bash
pip install argonaut-translator-gui
```

That provides the `argonaut` command, and with it `argonaut-cli`, the same
engine from the command line (see [below](#from-the-command-line)). You need
Python 3.10 or newer.

Argonaut used to be published as `argonaut-translator`. If you have that
installed, upgrading it moves you over by itself, keeping the `argonaut`
command:

```bash
pip install -U argonaut-translator
```

Your language packages, the NLLB model, the translation cache, the history
and your settings stay where they were.

Before translating anything you need at least one **language package**.
Open *Settings → Manage language packages…*, pick the pairs you want and
press "Install selected". The dialog lists every package available with its
version and download size, can filter by what you already have, and flags
the ones with a newer version so you can update them in place.

## Translating documents

1. **Choose the languages.** The ⇄ button swaps them. By default the source
   is "Detect language": each document's language is worked out on its own,
   so you can mix files in different languages in the same batch.
2. **Add the documents.** Drag files or whole folders onto the window —
   folders are searched all the way down — or use the "Add…" button, or
   *File → Add files…* and *File → Add folder…*. While the list is empty it
   tells you so, and a drag held over the window outlines where the files
   will land. Each file shows its size, and the line below the list shows
   the total for the batch.
3. **Choose where the translations go**, with "Output…", or leave it and
   each translation is saved next to its original. Tick "Skip files already
   translated" to leave existing translations alone, which makes an
   interrupted batch safe to run again.
4. **Press "Translate".** Each file is saved with the target language added
   to its name, like `report_es.docx`.
5. You can press **"Cancel"** at any point, even in the middle of a file.
   Everything already translated is kept.

The **Translate** button stays greyed out until there is something to do,
and the line above it says what is missing — no documents yet, the same
language on both sides, or no model for the pair you picked.

### A note on PDFs

PDFs are translated paragraph by paragraph with the layout preserved, so a
long one takes a while. The bar shows the real percentage of paragraphs
done and an estimate of the time left, and the line beside it names what is
happening and which page it is on, so a long book shows progress instead of
an apparently idle bar. Text that runs sideways, like vertical watermarks,
is left alone, and links from the original are not carried over.

## The two engines

Chosen from *Settings → Engine*.

**Argos Translate** (the default) uses small models, one per language pair,
that you install from the application. A pair with no model of its own is
still translated, by going through English: Albanian to Spanish is done as
Albanian → English → Spanish. That works, but the text is translated twice
and the second pass cannot recover what the first one lost, so the window
tells you which language it is going through whenever that happens.

**NLLB-200** is a single larger model that translates directly between any
two of its 32 languages, with noticeably better results and no detour
through English. It is about 630 MB, downloaded the first time you choose
it, and can be deleted again from *Settings → Delete the NLLB-200 model…*.
Its weights are licensed for non-commercial use.

## Settings

Everything here is remembered between sessions and applies as soon as you
change it.

### Preferences

- **Interface language** — see below.
- **Theme** — System (your desktop's own look, the default), Light or Dark.
- **Download speed units** — network units (`16.8 Mbps`) or the ones a
  download manager shows (`2.0 MB/s`).

### How translation runs

- **Engine** — Argos Translate or NLLB-200 (see above).
- **CPU threads** — up to the number of cores you have, and using all of
  them by default. Holding threads back only makes you wait longer.
- **Translation quality** — *Best quality*, or *Fast*, which is roughly
  1.7× quicker for a small loss in accuracy. The two never share cached
  translations, since what they produce differs.

### Translation cache

A paragraph translated once is reused for the rest of the batch and, unless
you turn it off, in later sessions too — so the same paragraph repeated
across several files reaches the engine only once. The window reports how
much each file and the batch as a whole took from the cache.

From *Settings → Cache* you can switch it off, choose how long unused
entries are kept (never, 30 days, 90 days — the default — or a year), see
how big it has grown, and clear it. Entries are tied to the engine, the
model version and the language pair, so updating a package stops it serving
the old model's work.

### Translation history

Every file a batch finishes is recorded: what was translated and from which
folder, into which language, where the result was written, with which
engine, how long it took and how much of it came from the cache.
*Settings → History → View history…* lists them newest first in sortable
columns. Double-clicking a row, or the "Open translation" button, opens the
translated file; rows whose file has since been moved or deleted stay in
the list, greyed out, because the history is a record of what happened
rather than a file browser.

The same submenu lets you stop recording, choose how long entries are kept,
see the size and clear it. Viewing and clearing keep working while
recording is off, and only successful translations are recorded: a file
that failed is not history, it is an error.

## Interface language

Argonaut starts in the language your desktop is set to, falling back to
English when that is one it does not speak. In *Settings → Preferences →
Interface language* you can switch to Spanish, French, German, Italian,
Portuguese, Russian, Chinese, Japanese, Dutch, Polish or Turkish. The
change applies at once, and *that* is what gets remembered — until you pick
one, the desktop keeps deciding, so changing your system language changes
Argonaut's too.

It says *interface* because it is the language of the buttons and menus,
not of the documents. The languages you translate between are the two
boxes above the file list.

Those are named in your interface language as well: French reads "Francés"
in Spanish and "フランス語" in Japanese, in the language boxes, in the
package dialog and in the messages — each list in alphabetical order by the
name you actually see.

## What is remembered

When you close the window, Argonaut stores the interface language, the
source and target languages, the output folder and the window's size and
position, and restores them next time. If a saved language is no longer
installed, or the folder is gone, it falls back to the default.

## From the command line

Everything that translates here is also a command, `argonaut-cli`, which is
installed along with Argonaut — or on its own, without the window, from
[argonaut-translator-cli](https://github.com/Nibblex/argonaut-translator-cli),
whose page is its manual:

```bash
argonaut-cli translate report.pdf --to es
argonaut-cli packages install en-es
```

The two share the language packages, the NLLB model, the translation cache
and the history, so a document translated in one is answered from the cache
in the other, and both appear in the same history. How long cache and
history entries are kept is set here; the command line never discards them
by itself.

## Help

The **Help** menu opens this manual and **Report a bug…**, which goes
straight to the issue tracker. Both open in your browser: translation is
offline, but documentation and bug reports are the two things worth having
current.

"About Argonaut…" has three tabs: *About* (version, supported formats and
links), *Details* (a plain-text report with the versions of everything, the
active engine and where the cache lives — copyable with one button, ready
to paste into a bug report) and *Credits and licenses*.

## Author and license

© 2026 Sergio Rodríguez.

This project is distributed under the [GNU GPL v3](LICENSE) license, in
line with the license of PyQt5, which the interface depends on.
