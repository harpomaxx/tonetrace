"""Group move and resize of a multi-note selection (issue #36).

Dragging any note of a multi-selection moves or resizes the whole group. The
delta is computed once from the dragged note and clamped by the *most
constrained* member before being applied to all of them, so a group meeting a
boundary keeps its relative spacing instead of collapsing as notes clamp
independently. The whole gesture is one undo step.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.4, velocity=90, bends=None):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch,
        start_seconds=start,
        duration_seconds=duration,
        velocity=velocity,
        pitch_bends=bends,
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
    frames = [i * 0.1 for i in range(200)]
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
        notes = [_note(60, 2.0), _note(64, 3.0), _note(67, 4.0)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def _arm_drag(window, mode, index):
    """Put the widget into a group drag of ``index`` as if the user pressed it."""

    roll = window.piano_roll
    note = window.state.current_notes[index]
    rect = roll._note_rect(note)
    roll.drag_mode = mode
    roll.drag_note_index = index
    roll.drag_original_note = note
    roll.drag_start_x = rect.center().x()
    roll.drag_start_y = rect.center().y()
    roll.drag_has_moved = True
    roll.drag_group_originals = {
        i: window.state.current_notes[i] for i in sorted(roll.selected_indices)
    }
    return rect.center().x(), rect.center().y()


def _layout(window):
    return [
        (note.pitch, round(note.start_seconds, 3), round(note.duration_seconds, 3))
        for note in window.state.current_notes
    ]


def _gaps(window):
    starts = [note.start_seconds for note in window.state.current_notes]
    return [round(start - starts[0], 3) for start in starts]


def _y_for_pitch(window, pitch):
    return window.piano_roll._note_rect(_note(pitch, 0.0, 0.1)).center().y()


# --- move -------------------------------------------------------------------


def test_group_move_shifts_every_selected_note():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, _y = _arm_drag(window, "move", 0)

        window.piano_roll._emit_group_edit(x + 200, _y_for_pitch(window, 65), committed=True)
        app.processEvents()

        pitches = [note.pitch for note in window.state.current_notes]
        # Anchor 60 -> 65 is +5; the others follow by the same interval.
        assert pitches == [65, 69, 72]
        assert _gaps(window) == [0.0, 1.0, 2.0], "relative spacing must survive"
    finally:
        window.close()
        app.processEvents()


def test_group_move_clamps_by_the_most_constrained_member():
    """Dragging far left stops at zero without collapsing the group."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "move", 0)

        window.piano_roll._emit_group_edit(x - 99999, y, committed=True)
        app.processEvents()

        starts = [note.start_seconds for note in window.state.current_notes]
        assert min(starts) == pytest.approx(0.0)
        assert _gaps(window) == [0.0, 1.0, 2.0], "clamping must not squash the group"
    finally:
        window.close()
        app.processEvents()


def test_group_move_clamps_pitch_without_collapsing():
    """Dragging above the top of the range keeps the chord's intervals."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, _y = _arm_drag(window, "move", 0)

        # Aim at the highest row available; the top note bounds the group.
        window.piano_roll._emit_group_edit(x, _y_for_pitch(window, 83), committed=True)
        app.processEvents()

        pitches = [note.pitch for note in window.state.current_notes]
        intervals = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]
        assert intervals == [4, 3], "intervals must survive a pitch clamp"
        assert all(0 <= pitch <= 127 for pitch in pitches)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("target_pitch", [83, 48])
def test_group_move_never_pushes_a_note_off_the_drawn_rows(target_pitch):
    """A wide selection dragged to an extreme must stay visible.

    The heatmap only has rows for the pitches it analysed, and _note_rect
    returns None outside them. Clamping the group against 0..127 rather than
    those rows let the outer note move to a valid MIDI pitch with nowhere to be
    drawn: still in the data and draggable back, but invisible on the roll.
    """

    # Two octaves apart, so one end hits the boundary well before the other.
    notes = [_note(55, 2.0), _note(79, 3.0)]
    app, window = _window(notes)
    try:
        roll = window.piano_roll
        roll.set_selected_indices({0, 1})
        app.processEvents()
        x, _y = _arm_drag(window, "move", 0)

        roll._emit_group_edit(x, _y_for_pitch(window, target_pitch), committed=True)
        app.processEvents()

        moved = window.state.current_notes
        assert all(roll._note_rect(note) is not None for note in moved), (
            "every moved note must still have a rectangle to draw"
        )
        low, high = roll._drawable_pitch_range()
        assert all(low <= note.pitch <= high for note in moved)
        # And the interval is untouched by the clamp.
        assert moved[1].pitch - moved[0].pitch == 24
    finally:
        window.close()
        app.processEvents()


def test_drawable_pitch_range_matches_the_heatmap():
    app, window = _window()
    try:
        low, high = window.piano_roll._drawable_pitch_range()
        assert (low, high) == (48, 83)
    finally:
        window.close()
        app.processEvents()


def test_single_selection_drag_still_uses_the_single_note_path():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({1})
        app.processEvents()

        singles = []
        groups = []
        window.piano_roll.note_edited.connect(lambda *a: singles.append(a))
        window.piano_roll.notes_edited.connect(lambda *a: groups.append(a))

        roll = window.piano_roll
        note = window.state.current_notes[1]
        rect = roll._note_rect(note)
        roll.drag_mode = "move"
        roll.drag_note_index = 1
        roll.drag_original_note = note
        roll.drag_start_x = rect.center().x()
        roll.drag_start_y = rect.center().y()
        roll.drag_has_moved = True
        roll.drag_group_originals = {}  # single selection: no group

        edited = roll._edited_drag_note(rect.center().x() + 50, rect.center().y())
        assert edited is not None
        assert groups == [], "a single-note drag must not emit the batch signal"
    finally:
        window.close()
        app.processEvents()


# --- resize -----------------------------------------------------------------


def test_group_resize_end_applies_a_uniform_delta():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "resize_end", 0)

        window.piano_roll._emit_group_edit(x + 100, y, committed=True)
        app.processEvents()

        durations = [note.duration_seconds for note in window.state.current_notes]
        assert len(set(round(value, 3) for value in durations)) == 1
        assert durations[0] > 0.4
        # Starts are untouched by an end-edge resize.
        assert [note.start_seconds for note in window.state.current_notes] == [2.0, 3.0, 4.0]
    finally:
        window.close()
        app.processEvents()


def test_group_resize_end_cannot_shrink_past_the_minimum():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "resize_end", 0)

        window.piano_roll._emit_group_edit(x - 99999, y, committed=True)
        app.processEvents()

        durations = [note.duration_seconds for note in window.state.current_notes]
        assert all(value > 0 for value in durations)
    finally:
        window.close()
        app.processEvents()


def test_group_resize_start_moves_the_front_edge_together():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "resize_start", 0)

        window.piano_roll._emit_group_edit(x - 40, y, committed=True)
        app.processEvents()

        notes = window.state.current_notes
        # Each note starts earlier and is correspondingly longer; ends hold.
        assert all(note.start_seconds < original for note, original in zip(notes, (2.0, 3.0, 4.0)))
        assert [round(note.end_seconds, 3) for note in notes] == [2.4, 3.4, 4.4]
    finally:
        window.close()
        app.processEvents()


# --- commit semantics -------------------------------------------------------


def _mouse(kind, x, y, button, buttons):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        kind, QPointF(x, y), QPointF(x, y), button, buttons, Qt.KeyboardModifier.NoModifier
    )


def test_group_drag_is_a_single_undo_step():
    """A real press/move/release gesture collapses into one history entry.

    Driven through the actual event handlers rather than _emit_group_edit: the
    widget's note list is the *same object* as the model's, so a preview that
    ran before the handler snapshotted would leave undo rewinding only to
    mid-drag. Only the full gesture exercises that ordering.
    """

    from PySide6.QtCore import QEvent, Qt

    app, window = _window()
    try:
        roll = window.piano_roll
        roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        before = _layout(window)

        rect = roll._note_rect(window.state.current_notes[0])
        x, y = rect.center().x(), rect.center().y()

        roll.mousePressEvent(
            _mouse(
                QEvent.Type.MouseButtonPress,
                x,
                y,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
            )
        )
        for offset in (20, 40, 60, 80, 100):
            roll.mouseMoveEvent(
                _mouse(
                    QEvent.Type.MouseMove,
                    x + offset,
                    y,
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton,
                )
            )
            app.processEvents()
        roll.mouseReleaseEvent(
            _mouse(
                QEvent.Type.MouseButtonRelease,
                x + 100,
                y,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
            )
        )
        app.processEvents()
        assert _layout(window) != before, "the drag should have moved the group"

        window._undo_edit()
        app.processEvents()
        assert _layout(window) == before, "undo must rewind the whole drag, not mid-drag"
    finally:
        window.close()
        app.processEvents()


def test_uncommitted_group_ticks_record_no_history_entry():
    """Preview ticks stage the edit but must not push an undo entry.

    Matching the pre-existing single-note drag path: an uncommitted tick
    mutates the working list for live feedback and records nothing, so the
    history only grows when the drag is released.
    """

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "move", 0)

        for offset in (20, 40):
            window.piano_roll._emit_group_edit(x + offset, y, committed=False)
            app.processEvents()

        # The snapshot is held for the commit, and nothing is in the history yet.
        assert window._pre_edit_snapshot is not None
        assert window.edit_history.undo(window.state.current_notes) is None
    finally:
        window.close()
        app.processEvents()


def test_group_edit_preserves_pitch_bends():
    bends = ((0.0, 0.0), (0.2, 0.5))
    notes = [_note(60, 2.0, bends=bends), _note(64, 3.0), _note(67, 4.0)]
    app, window = _window(notes)
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "move", 0)

        window.piano_roll._emit_group_edit(x + 60, y, committed=True)
        app.processEvents()

        assert window.state.current_notes[0].pitch_bends == bends
    finally:
        window.close()
        app.processEvents()


def test_group_selection_survives_the_edit():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 2})
        app.processEvents()
        x, y = _arm_drag(window, "move", 0)

        window.piano_roll._emit_group_edit(x + 60, y, committed=True)
        app.processEvents()

        assert window.selected_indices == {0, 2}
        assert window.piano_roll.selected_indices == {0, 2}
    finally:
        window.close()
        app.processEvents()


def test_group_state_is_cleared_after_release():
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        x, y = _arm_drag(window, "move", 0)

        window.piano_roll.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(x + 30, y),
                QPointF(x + 30, y),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        app.processEvents()

        assert window.piano_roll.drag_group_originals == {}
        assert window.piano_roll.drag_mode is None
    finally:
        window.close()
        app.processEvents()
