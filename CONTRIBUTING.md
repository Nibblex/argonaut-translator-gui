# Contributing to Argonaut

[![CI](https://github.com/Nibblex/Argonaut/actions/workflows/ci.yml/badge.svg)](https://github.com/Nibblex/Argonaut/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/Nibblex/Argonaut/badges/coverage.svg)](https://github.com/Nibblex/Argonaut/actions/workflows/ci.yml)

`README.md` is the user manual — the Help menu opens it — so it stays about
using Argonaut. Everything about building, testing and releasing it is here.

This repository is the desktop application, `argonaut-translator-gui`. The
engine it drives — detection, the cache, the history, PDFs, NLLB, the Argos
packages and the `argonaut-cli` command — is
[argonaut-translator-cli](https://github.com/Nibblex/argonaut-translator-cli),
a dependency published on its own.

## Running from a checkout

```bash
pip install -e .
python3 -m argonaut_gui
```

That installs the engine from PyPI. To work on both at once, install a
checkout of the engine first, so this one uses it instead:

```bash
pip install -e ../argonaut-translator-cli
pip install -e .
```

Python 3.10 or newer (the pymupdf wheels need it). You also need at least one
Argos language package; install it from the running application, or with
`argonaut-cli packages install en-es`.

## Tests

```bash
pip install -e .[test]     # pytest-cov and pytest-qt are both required
pytest                     # the whole suite
pytest tests/test_i18n.py::test_placeholders_match_english   # one test
pytest -o addopts="" tests/test_window.py                    # without coverage
```

`pyproject.toml` puts `--cov=argonaut_gui` in `addopts`, so a bare `pytest`
fails outright unless `pytest-cov` is installed — the `[test]` extra brings
it, along with `pytest-qt` for the `qtbot` fixture.

The suite runs Qt headless and needs no language models: `tests/conftest.py`
sets `QT_QPA_PLATFORM=offscreen` itself, sandboxes `XDG_CONFIG_HOME` and
`XDG_DATA_HOME` per test, and fakes the translation engines. It also makes
every modal dialog raise instead of blocking, so a test that expects one has
to patch it. The engine's own tests live in its repository.

CI runs the suite on Python 3.10 through 3.14; that matrix, `requires-python`
and the classifiers in `pyproject.toml` have to stay in step.

## Layout

All modules live in the `src/argonaut_gui/` package:

- `__init__.py` — package version (`__version__`).
- `main.py` — entry point; silences dependency warnings and launches the window.
- `window/` — the main window, split by responsibility: `main_window.py`
  (widgets, menus and window state), `engine.py` (backend, threads, quality,
  the cache and history menus and the NLLB model), `files.py` and
  `file_list.py` (the file list, its columns and its empty state),
  `translation_run.py` (driving a batch and reporting its progress),
  `theme.py` (light/dark/system palettes), `about_dialog.py` and
  `history_dialog.py`.
- `worker.py` — the threads that keep work off the interface:
  `TranslateWorker` runs the engine's `Batch` and turns what it reports into
  signals, reading the settings that shape the run; `error_text` renders the
  engine's `FileError`s in the interface language; the rest download the
  NLLB model and list, measure and install Argos packages.
- `package_dialog.py` — dialog to browse, install and remove packages.
- `i18n.py` — interface language: which one is in force, how it is chosen and
  saved, and lazy access to the data files below; also the language names,
  package pairs and sort orders as the user reads them.
- `locales/<code>.json` — the interface strings, one file per language.
- `locales/language_names.json` — the translation languages' names in each of
  those interface languages (generated; see below).

Packaging lives at the top level: `pyproject.toml` is the PyPI package, and
`transition/` the last release of `argonaut-translator`, the name Argonaut
was published under before the split (see Releasing). `data/` keeps the
desktop entry and the icon, which the wheel does not install; for a menu
entry, copy them by hand under `~/.local/share` (`applications/` and
`icons/hicolor/scalable/apps/`).

If you work with a coding agent, point it at this file: the repository ships
no `AGENTS.md`.

## Interface strings

Every user-facing string goes through `i18n.tr("key")`, with the keys in
`src/argonaut_gui/locales/<code>.json` — twelve hand-written files. Adding a
key to `en.json` alone fails the suite: `tests/test_i18n.py` requires every
language to carry the same keys and the same `{placeholders}`, so a new
string has to be added to all twelve in the same change. Missing keys fall
back to English at runtime.

The engine speaks English and passes data rather than sentences where the
window needs its own words: a document it turns down raises a `FileError`
carrying the file and the languages, which `worker.error_text` renders.

Menu titles carry `&` accelerators, and the suite also checks that the ones
competing in the same menu are present and distinct in every language. The
groups it checks are listed in `ACCELERATOR_GROUPS`; moving a menu means
moving its key to the group it now competes in.

To add an interface language: copy `en.json`, translate the values, list the
code in `LANGUAGES` in `i18n.py` (which sets the menu order and the name
shown), add it to `UI` in `tools/gen_language_names.py` and regenerate.

`locales/language_names.json` is generated from CLDR and should never be
hand-edited:

```bash
pip install babel && python tools/gen_language_names.py
```

Babel is a development tool, not a runtime dependency; the result is
committed. A language with no entry keeps the English name the engine reports.

## Releasing

Work reaches `main` through branches, not directly: a topic branch merges
`--no-ff` into `dev`, `dev` merges `--no-ff` into `main`, and the annotated
tag goes on `main` **after** that merge, so it points at the tree actually
released rather than at the branch head before it.

Bump `__version__` in `src/argonaut_gui/__init__.py`; `pyproject.toml` reads
the version from there, so nothing else needs touching.

A release that needs something new from the engine comes after the engine's
own release: CI installs `argonaut-translator-cli` from PyPI, so `main`
cannot pass until the version it requires is published.

```bash
git checkout dev  && git merge --no-ff my-branch && git push origin dev
git checkout main && git merge --no-ff dev       && git push origin main
git tag -a v2.0.0 -m "Argonaut 2.0.0"
git push origin v2.0.0
gh release create v2.0.0 --title "Argonaut 2.0.0" --notes-file notes.md
```

Publishing the release triggers `.github/workflows/publish.yml`, which builds
the wheel and the sdist and uploads them via trusted publishing. Pushing the
tag alone publishes nothing, so the CI run on `main` can be used as a gate: a
version is permanent on PyPI once uploaded. CI never runs on `dev` — it
watches `main` and pull requests only — so the local suite is what gates the
merge.

The same workflow builds `transition/` and uploads it with `skip-existing`:
`argonaut-translator` 2.0.0 goes up once, with the first release after the
split, and every later release finds it already there. It installs nothing
but depends on `argonaut-translator-gui`, so `pip install -U
argonaut-translator` moves an old installation over; it also declares the
`argonaut` command, because pip removes the old version last, after the new
packages are in, and the old version's record lists that command. PyPI
trusts this workflow for both `argonaut-translator-gui` and
`argonaut-translator`.
