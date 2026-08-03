"""Multi-note selection and multi-delete (issue #35).

Selection used to be a single index end to end. It is now a set, reached two
ways: shift-click toggles one note, and a drag over empty space rubber-bands
every note the rectangle touches (shift-drag adds to the existing selection).

Gesture arbitration on empty space: mouse-down arms a band, a move past the
drag threshold turns it into a selection, and a release without movement is a
plain click, which clears the selection and seeks exactly as it always did.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _widget_with_notes(notes, midi_notes=range(60, 72)):
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.state import GuiHeatmap
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    midi_notes = list(midi_notes)
    frames = [i * 0.1 for i in range(40)]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frames,
        activations=[[0.0] * len(midi_notes) for _ in frames],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    roll = PianoRollWidget()
    roll.resize(1400, len(midi_notes) * roll.note_height + 4)
    roll.set_data(heatmap, notes, full_duration_seconds=4.0)
    return roll


def _press(roll, x, y, shift=False):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    roll.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            mods,
        )
    )


def _move(roll, x, y, shift=False):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    roll.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            mods,
        )
    )


def _release(roll, x, y, shift=False):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    roll.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            mods,
        )
    )


def _center_of(roll, note):
    rect = roll._note_rect(note)
    return rect.center().x(), rect.center().y()


def _spread_notes():
    """Three notes on distinct rows, well separated in time."""

    from notegrabber.gui.state import GuiMidiNote

    return [
        GuiMidiNote(pitch=60, start_seconds=0.2, duration_seconds=0.4, velocity=90),
        GuiMidiNote(pitch=64, start_seconds=1.2, duration_seconds=0.4, velocity=90),
        GuiMidiNote(pitch=68, start_seconds=2.2, duration_seconds=0.4, velocity=90),
    ]


# --- selection state --------------------------------------------------------


def test_plain_click_replaces_the_selection():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    _press(roll, *_center_of(roll, notes[0]))
    assert roll.selected_indices == {0}

    _press(roll, *_center_of(roll, notes[2]))
    assert roll.selected_indices == {2}


def test_shift_click_toggles_notes_in_and_out():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    _press(roll, *_center_of(roll, notes[0]))
    _press(roll, *_center_of(roll, notes[1]), shift=True)
    _press(roll, *_center_of(roll, notes[2]), shift=True)
    assert roll.selected_indices == {0, 1, 2}

    # Shift-clicking a selected note removes it again.
    _press(roll, *_center_of(roll, notes[1]), shift=True)
    assert roll.selected_indices == {0, 2}


def test_selected_note_index_is_the_single_selection_view():
    """The legacy single-index accessor is None unless exactly one is selected."""

    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    assert roll.selected_note_index is None

    _press(roll, *_center_of(roll, notes[1]))
    assert roll.selected_note_index == 1

    _press(roll, *_center_of(roll, notes[2]), shift=True)
    assert roll.selected_indices == {1, 2}
    assert roll.selected_note_index is None


def test_selection_changed_signal_fires_on_change_only():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    seen = []
    roll.selection_changed.connect(lambda indices: seen.append(set(indices)))

    roll.set_selected_indices({0, 1})
    roll.set_selected_indices({0, 1})  # no change, no emit
    roll.set_selected_indices({2})

    assert seen == [{0, 1}, {2}]


def test_stale_indices_are_dropped_when_notes_shrink():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)
    roll.set_selected_indices({0, 1, 2})

    roll.set_data(roll.heatmap, notes[:1], full_duration_seconds=4.0)
    assert roll.selected_indices == {0}


# --- rubber band ------------------------------------------------------------


def test_rubber_band_selects_every_intersecting_note():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    # Drag a band spanning all three rows and the whole time range.
    top = min(roll._note_rect(note).top() for note in notes) - 4
    bottom = max(roll._note_rect(note).bottom() for note in notes) + 4
    left = roll._note_rect(notes[0]).left() - 4
    right = roll._note_rect(notes[2]).right() + 4

    _press(roll, left, top)
    _move(roll, right, bottom)
    _release(roll, right, bottom)

    assert roll.selected_indices == {0, 1, 2}


def test_rubber_band_excludes_notes_outside_the_rectangle():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    rect = roll._note_rect(notes[1])
    _press(roll, rect.left() - 3, rect.top() - 3)
    _move(roll, rect.right() + 3, rect.bottom() + 3)
    _release(roll, rect.right() + 3, rect.bottom() + 3)

    assert roll.selected_indices == {1}


def test_shift_rubber_band_adds_to_the_existing_selection():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    _press(roll, *_center_of(roll, notes[0]))
    assert roll.selected_indices == {0}

    rect = roll._note_rect(notes[2])
    _press(roll, rect.left() - 3, rect.top() - 3, shift=True)
    _move(roll, rect.right() + 3, rect.bottom() + 3, shift=True)
    _release(roll, rect.right() + 3, rect.bottom() + 3, shift=True)

    assert roll.selected_indices == {0, 2}


def test_plain_rubber_band_replaces_the_existing_selection():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    _press(roll, *_center_of(roll, notes[0]))
    rect = roll._note_rect(notes[2])
    _press(roll, rect.left() - 3, rect.top() - 3)
    _move(roll, rect.right() + 3, rect.bottom() + 3)
    _release(roll, rect.right() + 3, rect.bottom() + 3)

    assert roll.selected_indices == {2}


def test_band_is_cleared_after_release():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    rect = roll._note_rect(notes[1])
    _press(roll, rect.left() - 3, rect.top() - 3)
    _move(roll, rect.right() + 3, rect.bottom() + 3)
    assert roll._rubber_rect() is not None

    _release(roll, rect.right() + 3, rect.bottom() + 3)
    assert roll.rubber_start is None
    assert roll._rubber_rect() is None


# --- gesture arbitration ----------------------------------------------------


def test_click_without_moving_still_seeks_and_clears():
    """A plain click on empty space keeps its old meaning."""

    notes = _spread_notes()
    roll = _widget_with_notes(notes)
    _press(roll, *_center_of(roll, notes[0]))
    assert roll.selected_indices == {0}

    seeks = []
    roll.seek_requested.connect(seeks.append)

    empty_x = roll.keyboard_width + 600.0
    empty_y = roll._note_rect(notes[1]).center().y() + roll.note_height * 2
    _press(roll, empty_x, empty_y)
    _release(roll, empty_x, empty_y)

    assert roll.selected_indices == set()
    assert len(seeks) == 1
    assert seeks[0] == pytest.approx((empty_x - roll.keyboard_width) * roll.seconds_per_pixel)


def test_drag_over_empty_space_does_not_seek():
    """A rubber-band drag must not also jump the playhead."""

    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    seeks = []
    roll.seek_requested.connect(seeks.append)

    rect = roll._note_rect(notes[1])
    _press(roll, rect.left() - 5, rect.top() - 5)
    _move(roll, rect.right() + 5, rect.bottom() + 5)
    _release(roll, rect.right() + 5, rect.bottom() + 5)

    assert seeks == []
    assert roll.selected_indices == {1}


def test_movement_below_the_threshold_is_still_a_click():
    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    seeks = []
    roll.seek_requested.connect(seeks.append)

    x = roll.keyboard_width + 500.0
    y = roll._note_rect(notes[0]).center().y() + roll.note_height * 3
    _press(roll, x, y)
    _move(roll, x + 1.0, y + 1.0)  # under drag_threshold_pixels
    _release(roll, x + 1.0, y + 1.0)

    assert len(seeks) == 1
    assert roll.selected_indices == set()


def test_dragging_a_note_body_still_moves_only_that_note():
    """Note drag is unchanged: mouse-down on a note starts a move, not a band."""

    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    edits = []
    roll.note_edited.connect(lambda *args: edits.append(args))

    x, y = _center_of(roll, notes[1])
    _press(roll, x, y)
    _move(roll, x + 40.0, y)
    _release(roll, x + 40.0, y)

    assert roll.rubber_start is None
    assert edits, "dragging a note body should still emit note_edited"
    assert all(edit[0] == 1 for edit in edits)


def test_shift_click_on_a_note_does_not_start_a_drag():
    """Toggling a note off must not arm a move that a stray wobble would apply."""

    notes = _spread_notes()
    roll = _widget_with_notes(notes)

    edits = []
    roll.note_edited.connect(lambda *args: edits.append(args))

    x, y = _center_of(roll, notes[0])
    _press(roll, x, y, shift=True)
    assert roll.drag_mode is None

    _move(roll, x + 40.0, y, shift=True)
    _release(roll, x + 40.0, y, shift=True)
    assert edits == []


def test_clicking_a_note_inside_a_multi_selection_keeps_the_set():
    """So a drag can act on a group rather than collapsing it to one note."""

    notes = _spread_notes()
    roll = _widget_with_notes(notes)
    roll.set_selected_indices({0, 1, 2})

    _press(roll, *_center_of(roll, notes[1]))
    assert roll.selected_indices == {0, 1, 2}


# --- state helper -----------------------------------------------------------


def test_delete_gui_notes_removes_every_index():
    from notegrabber.gui.state import delete_gui_notes

    notes = _spread_notes()
    remaining = delete_gui_notes(notes, {0, 2})

    assert len(remaining) == 1
    assert remaining[0].pitch == 64
    assert len(notes) == 3, "input list must not be mutated"


def test_delete_gui_notes_handles_out_of_range_and_empty():
    from notegrabber.gui.state import delete_gui_notes

    notes = _spread_notes()
    assert len(delete_gui_notes(notes, set())) == 3
    assert len(delete_gui_notes(notes, {99, -1})) == 3
    assert delete_gui_notes(notes, {0, 1, 2}) == []


def test_delete_gui_notes_does_not_shift_indices_mid_delete():
    """Deleting low indices must not renumber the ones still to be removed."""

    from notegrabber.gui.state import GuiMidiNote, delete_gui_notes

    notes = [
        GuiMidiNote(pitch=60 + i, start_seconds=i * 0.5, duration_seconds=0.2, velocity=90)
        for i in range(6)
    ]
    remaining = delete_gui_notes(notes, {0, 2, 4})

    assert [note.pitch for note in remaining] == [61, 63, 65]


# --- main window wiring -----------------------------------------------------


def _window_with_notes():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1400, 900)
    window.show()
    app.processEvents()

    midi_notes = list(range(60, 72))
    frames = [i * 0.1 for i in range(40)]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frames,
        activations=[[0.0] * len(midi_notes) for _ in frames],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    notes = [
        GuiMidiNote(pitch=60 + i * 3, start_seconds=0.2 + i, duration_seconds=0.4, velocity=90)
        for i in range(4)
    ]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def test_multi_delete_removes_every_selected_note_in_one_undo_step():
    app, window = _window_with_notes()
    try:
        window.piano_roll.set_selected_indices({0, 2})
        app.processEvents()

        window._delete_selected_note()
        app.processEvents()
        assert [note.pitch for note in window.state.current_notes] == [63, 69]

        # One undoable step, not one per deleted note.
        window._undo_edit()
        app.processEvents()
        assert [note.pitch for note in window.state.current_notes] == [60, 63, 66, 69]
    finally:
        window.close()
        app.processEvents()


def test_label_and_inspector_track_selection_size():
    app, window = _window_with_notes()
    try:
        window.piano_roll.set_selected_indices({1})
        app.processEvents()
        assert window.selected_note_index == 1
        assert window.note_pitch_spin.isEnabled()

        window.piano_roll.set_selected_indices({1, 3})
        app.processEvents()
        assert window.selected_indices == {1, 3}
        assert window.selected_note_index is None
        assert "2 notes selected" in window.selected_note_label.text()
        # The single-note inspector cannot represent two notes.
        assert not window.note_pitch_spin.isEnabled()

        window.piano_roll.set_selected_indices(set())
        app.processEvents()
        assert "No note selected" in window.selected_note_label.text()
    finally:
        window.close()
        app.processEvents()


def test_delete_button_enabled_for_any_non_empty_selection():
    app, window = _window_with_notes()
    try:
        window.piano_roll.set_selected_indices(set())
        app.processEvents()
        assert not window.controls.delete_button.isEnabled()

        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        assert window.controls.delete_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_single_delete_still_reports_the_pitch():
    """The one-note message is unchanged; only the batch case is new."""

    app, window = _window_with_notes()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        window._delete_selected_note()
        app.processEvents()

        assert "Deleted MIDI 60" in window.transport.status_label.text()
        assert len(window.state.current_notes) == 3
    finally:
        window.close()
        app.processEvents()
