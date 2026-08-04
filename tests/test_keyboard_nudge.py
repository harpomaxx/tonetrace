"""Keyboard nudging of the note selection (issue #65).

Arrows nudge time and pitch, +/- adjust velocity, with Shift finer/larger and
Ctrl coarser/octave. Each press is one undo step, reusing the batch-edit path
from #36 -- which also means the group clamps by its most-constrained member,
so a selection meeting a boundary keeps its spacing and intervals.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.4, velocity=90):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity
    )


def _window(notes=None):
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1400, 900)
    window.show()
    app.processEvents()

    midi_notes = list(range(48, 84))
    frames = [i * 0.1 for i in range(600)]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frames,
        activations=[[0.0] * len(midi_notes) for _ in frames],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    if notes is None:
        notes = [_note(60, 2.0), _note(64, 3.0)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def _press(window, key, *modifier_names):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    modifiers = Qt.KeyboardModifier.NoModifier
    for name in modifier_names:
        modifiers |= getattr(Qt.KeyboardModifier, name)
    window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()


def _starts(window):
    return [round(note.start_seconds, 4) for note in window.state.current_notes]


def _pitches(window):
    return [note.pitch for note in window.state.current_notes]


def _velocities(window):
    return [note.velocity for note in window.state.current_notes]


# --- time -------------------------------------------------------------------


def test_arrows_nudge_time_in_both_directions():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        _press(window, Qt.Key.Key_Right)
        assert _starts(window) == [2.05, 3.05]

        _press(window, Qt.Key.Key_Left)
        assert _starts(window) == [2.0, 3.0]
    finally:
        window.close()
        app.processEvents()


def test_shift_and_ctrl_change_the_time_step():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        _press(window, Qt.Key.Key_Right, "ShiftModifier")
        assert _starts(window)[0] == pytest.approx(2.0 + window.NUDGE_SECONDS_FINE)

        _press(window, Qt.Key.Key_Right, "ControlModifier")
        assert _starts(window)[0] == pytest.approx(
            2.0 + window.NUDGE_SECONDS_FINE + window.NUDGE_SECONDS_COARSE
        )
    finally:
        window.close()
        app.processEvents()


def test_time_nudge_clamps_at_zero_keeping_spacing():
    from PySide6.QtCore import Qt

    app, window = _window([_note(60, 0.10), _note(64, 1.10)])
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        for _ in range(5):
            _press(window, Qt.Key.Key_Left)

        starts = _starts(window)
        assert min(starts) == pytest.approx(0.0)
        assert starts[1] - starts[0] == pytest.approx(1.0), "spacing must survive the clamp"
    finally:
        window.close()
        app.processEvents()


# --- pitch ------------------------------------------------------------------


def test_arrows_nudge_pitch_by_a_semitone():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        _press(window, Qt.Key.Key_Up)
        assert _pitches(window) == [61, 65]

        _press(window, Qt.Key.Key_Down)
        assert _pitches(window) == [60, 64]
    finally:
        window.close()
        app.processEvents()


def test_ctrl_arrows_nudge_pitch_by_an_octave():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        _press(window, Qt.Key.Key_Up, "ControlModifier")
        assert _pitches(window)[0] == 72
    finally:
        window.close()
        app.processEvents()


def test_pitch_nudge_stops_at_the_drawn_rows_keeping_intervals():
    """Clamping to 0..127 would push the top note onto a row that is not drawn."""

    from PySide6.QtCore import Qt

    app, window = _window([_note(55, 2.0), _note(79, 3.0)])
    try:
        roll = window.piano_roll
        roll.set_selected_indices({0, 1})
        app.processEvents()

        for _ in range(10):
            _press(window, Qt.Key.Key_Up)

        pitches = _pitches(window)
        assert pitches[1] - pitches[0] == 24, "interval must survive the clamp"
        low, high = roll._drawable_pitch_range()
        assert all(low <= pitch <= high for pitch in pitches)
        assert all(roll._note_rect(note) is not None for note in window.state.current_notes)
    finally:
        window.close()
        app.processEvents()


# --- velocity ---------------------------------------------------------------


def test_plus_and_minus_adjust_velocity():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        _press(window, Qt.Key.Key_Plus)
        assert _velocities(window) == [91, 91]

        _press(window, Qt.Key.Key_Minus)
        assert _velocities(window) == [90, 90]
    finally:
        window.close()
        app.processEvents()


def test_shift_makes_velocity_steps_larger():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        _press(window, Qt.Key.Key_Plus, "ShiftModifier")
        assert _velocities(window)[0] == 90 + window.NUDGE_VELOCITY_LARGE
    finally:
        window.close()
        app.processEvents()


def test_equal_key_also_raises_velocity():
    """'+' is Shift+'=' on most layouts, so bare '=' must work too."""

    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        _press(window, Qt.Key.Key_Equal)
        assert _velocities(window)[0] == 91
    finally:
        window.close()
        app.processEvents()


def test_velocity_stays_in_the_midi_range():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        for _ in range(20):
            _press(window, Qt.Key.Key_Plus, "ShiftModifier")
        assert all(velocity <= 127 for velocity in _velocities(window))

        for _ in range(40):
            _press(window, Qt.Key.Key_Minus, "ShiftModifier")
        assert all(velocity >= 1 for velocity in _velocities(window))
    finally:
        window.close()
        app.processEvents()


# --- undo and wiring --------------------------------------------------------


def test_each_press_is_one_undo_step():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()
        before = _starts(window)

        _press(window, Qt.Key.Key_Right)
        _press(window, Qt.Key.Key_Right)
        assert _starts(window) == [2.10, 3.10]

        window._undo_edit()
        app.processEvents()
        assert _starts(window) == [2.05, 3.05], "one undo rewinds one press"

        window._undo_edit()
        app.processEvents()
        assert _starts(window) == before
    finally:
        window.close()
        app.processEvents()


def test_selection_survives_a_nudge():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        _press(window, Qt.Key.Key_Right)

        assert window.selected_indices == {0, 1}
        assert window.piano_roll.selected_indices == {0, 1}
    finally:
        window.close()
        app.processEvents()


def test_nudge_works_for_a_single_selection():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({1})
        app.processEvents()

        _press(window, Qt.Key.Key_Right)

        assert _starts(window) == [2.0, 3.05], "only the selected note moves"
    finally:
        window.close()
        app.processEvents()


def test_arrows_are_not_consumed_without_a_selection():
    """Otherwise the arrow keys would stop scrolling the view."""

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices(set())
        app.processEvents()

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
        assert window._nudge_from_key(event) is False
    finally:
        window.close()
        app.processEvents()


def test_unrelated_keys_are_not_consumed():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
        assert window._nudge_from_key(event) is False
    finally:
        window.close()
        app.processEvents()


def test_nudge_at_a_boundary_is_still_consumed():
    """A no-op nudge must not fall through and scroll the view instead."""

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    app, window = _window([_note(60, 0.0)])
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
        assert window._nudge_from_key(event) is True
        assert _starts(window) == [0.0]
    finally:
        window.close()
        app.processEvents()


def test_nudge_reports_itself_in_the_status_line():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        _press(window, Qt.Key.Key_Up)

        assert "Nudged 2 notes" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_delete_key_still_deletes():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        _press(window, Qt.Key.Key_Delete)

        assert len(window.state.current_notes) == 1
    finally:
        window.close()
        app.processEvents()
