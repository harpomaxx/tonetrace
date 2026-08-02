"""Shared pytest fixtures.

The GUI theme is a process-global (see gui/theme.py), and constructing a
MainWindow restores the persisted theme from QSettings. Both can leak a
non-default active theme across tests and change the colors a later test
asserts on. Reset to the default theme before every test so each starts from a
known palette regardless of order.
"""

from __future__ import annotations

import os
import tempfile

import pytest


# Point Qt's QSettings at a throwaway config dir for the whole test session so
# MainWindow's theme persistence never reads or writes the developer's real
# settings (which would otherwise leak a non-default theme across runs).
_TMP_CONFIG = tempfile.mkdtemp(prefix="notegrabber-test-config-")
os.environ["XDG_CONFIG_HOME"] = _TMP_CONFIG


@pytest.fixture(autouse=True)
def _reset_active_theme():
    try:
        from notegrabber.gui.theme import set_active_theme
    except Exception:  # noqa: BLE001 - PySide6 not installed: nothing to reset
        yield
        return
    set_active_theme("default")
    yield
    set_active_theme("default")
