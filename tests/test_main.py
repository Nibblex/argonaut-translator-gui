import importlib
import os
import warnings


def test_importing_main_configures_warning_silencing():
    import argonaut_gui.main as main

    assert callable(main.main)
    assert "QT_LOGGING_RULES" in os.environ

    # pytest saves and restores warnings.filters around each test, so the
    # filter the first import registered is no longer there to look at:
    # import the module again and watch it register the filter
    with warnings.catch_warnings():
        warnings.resetwarnings()
        importlib.reload(main)
        assert any(
            "supported version" in str(entry[1]) for entry in warnings.filters
        )
