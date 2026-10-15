# argonaut-translator

Argonaut is now published as two packages:

- [argonaut-translator-gui](https://pypi.org/project/argonaut-translator-gui/)
  — the desktop application, which brings along
- [argonaut-translator-cli](https://pypi.org/project/argonaut-translator-cli/)
  — the translation engine and the `argonaut-cli` command, which can also be
  installed on its own.

This package installs nothing of its own. It depends on
argonaut-translator-gui, so upgrading it moves an existing installation to
the new packages, and the `argonaut` command keeps working:

```bash
pip install -U argonaut-translator
```

New installations should use the new name directly:

```bash
pip install argonaut-translator-gui
```

Your language packages, the NLLB model, the translation cache, the history
and your settings stay where they were.
