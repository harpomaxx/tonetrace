"""The progress strip must never shift the widgets below it.

Hiding the progress bar collapsed its row, moving the waveform and piano roll
up and down every time a job started or finished. That made editing notes
jumpy: the click target moved mid-gesture. The bar now lives in a fixed-height
slot that stays in the layout whether or not the bar is drawn, so these tests
pin the piano roll's position across job start/end and across status messages
of very different lengths.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _window():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()
    return app, window


def _roll_top(window):
    """Absolute y of the piano-roll pane inside the window."""

    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    scroll = window.piano_scroll
    return scroll.mapTo(window, scroll.rect().topLeft()).y()


def test_progress_bar_starts_idle_in_a_reserved_slot():
    _app, window = _window()
    try:
        # Idle (so not drawn) from construction, but its slot still holds height.
        assert window.progress_bar.property("idle") is True
        assert not window.progress_bar.isVisible()
        # The slot holds a non-zero, fixed height even with the bar undrawn.
        slot = window.progress_bar.parentWidget()
        assert slot.height() > 0
        assert slot.minimumHeight() == slot.maximumHeight()
    finally:
        window.close()


def test_roll_does_not_move_when_progress_starts_and_stops():
    _app, window = _window()
    try:
        baseline = _roll_top(window)

        window._show_progress(indeterminate=True)
        assert window.progress_bar.property("idle") is False
        assert _roll_top(window) == baseline, "roll moved when progress started"

        window._hide_progress()
        assert window.progress_bar.property("idle") is True
        assert _roll_top(window) == baseline, "roll moved when progress stopped"
    finally:
        window.close()


def test_roll_does_not_move_across_status_message_lengths():
    _app, window = _window()
    try:
        window._set_status("Ready")
        baseline = _roll_top(window)

        for message in (
            "MIDI preview re-rendered.",
            "Added MIDI 67 at 1.00s. Export writes 78 edited notes. MIDI preview re-rendered.",
            "Exported 77 notes as MIDI to /home/user/a/deliberately/long/output/path/file.mid",
            "Ready",
        ):
            window._set_status(message)
            assert _roll_top(window) == baseline, f"roll moved for status: {message!r}"
    finally:
        window.close()


def test_idle_toggle_is_idempotent():
    """Repeated toggles keep one state and do not thrash the style."""

    _app, window = _window()
    try:
        window._set_progress_idle(False)
        window._set_progress_idle(False)
        assert window.progress_bar.property("idle") is False

        window._set_progress_idle(True)
        window._set_progress_idle(True)
        assert window.progress_bar.property("idle") is True
    finally:
        window.close()


def test_progress_bar_is_drawn_only_while_active():
    """Idle hides the bar itself; the reserved slot keeps the row's height."""

    _app, window = _window()
    try:
        slot = window.progress_bar.parentWidget()
        slot_height = slot.height()

        window._show_progress(indeterminate=True)
        assert window.progress_bar.isVisible()

        window._hide_progress()
        assert not window.progress_bar.isVisible()
        assert slot.height() == slot_height
    finally:
        window.close()
