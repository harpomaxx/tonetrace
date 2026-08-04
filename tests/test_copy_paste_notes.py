"""Copy, paste and duplicate the selected MIDI notes (issue #63).

Copy stores the selection relative to its earliest note in *both* time and
pitch, so the buffer is a pattern rather than a position. Paste anchors that
pattern at the mouse pointer -- x gives the time, the row under the pointer
gives the pitch, so the group transposes while keeping its intervals -- and
falls back to the playhead at the original pitch when the pointer is not over
the grid. Ctrl+D duplicates beside the source instead. Every insert is one
undoable step, however many notes it moves.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.3, velocity=90):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity)


# --- state helper -----------------------------------------------------------


def test_add_gui_notes_inserts_in_start_time_order():
    from notegrabber.gui.state import add_gui_notes

    existing = [_note(60, 0.0), _note(61, 2.0), _note(62, 4.0)]
    updated, inserted = add_gui_notes(existing, [_note(70, 1.0), _note(71, 3.0)])

    assert [note.start_seconds for note in updated] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert [updated[index].pitch for index in sorted(inserted)] == [70, 71]
    assert len(existing) == 3, "input list must not be mutated"


def test_add_gui_notes_reports_indices_that_survive_later_inserts():
    """Each reported index must point at the note it was reported for."""

    from notegrabber.gui.state import add_gui_notes

    existing = [_note(60, 0.0), _note(61, 5.0)]
    new = [_note(70, 1.0), _note(71, 2.0), _note(72, 3.0)]
    updated, inserted = add_gui_notes(existing, new)

    assert sorted(updated[index].pitch for index in inserted) == [70, 71, 72]


def test_add_gui_notes_does_not_reorder_existing_notes():
    """A drag-edit can leave the list unsorted; a paste must not renumber it.

    edit_history snapshots by position and the sequence table is index
    addressed, so silently re-sorting would shuffle untouched notes.
    """

    from notegrabber.gui.state import add_gui_notes

    existing = [_note(60, 5.0), _note(62, 1.0)]  # deliberately out of order
    updated, inserted = add_gui_notes(existing, [_note(64, 3.0)])

    assert [note.pitch for note in updated if note.pitch != 64] == [60, 62]
    assert len(inserted) == 1


def test_add_gui_notes_empty_batch_is_a_no_op():
    from notegrabber.gui.state import add_gui_notes

    existing = [_note(60, 0.0)]
    updated, inserted = add_gui_notes(existing, [])

    assert inserted == set()
    assert [note.pitch for note in updated] == [60]


def test_add_gui_notes_normalizes_inserted_notes():
    from notegrabber.gui.state import add_gui_notes

    updated, inserted = add_gui_notes([], [_note(999, -3.0, duration=0.0, velocity=999)])
    note = updated[next(iter(inserted))]

    assert note.pitch == 127
    assert note.velocity == 127
    assert note.start_seconds == 0.0
    assert note.duration_seconds > 0.0


# --- main window ------------------------------------------------------------


def _window():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1400, 900)
    window.show()
    app.processEvents()

    midi_notes = list(range(60, 72))
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
    notes = [_note(60, 2.0), _note(64, 2.5), _note(67, 3.2)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def _pitches_and_starts(window):
    return [(note.pitch, round(note.start_seconds, 3)) for note in window.state.current_notes]


def test_copy_stores_notes_relative_in_time_and_pitch():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()

        starts = [note.start_seconds for note in window._clipboard_notes]
        assert starts == pytest.approx([0.0, 0.5, 1.2])
        # Pitches are offsets from the first note (60, 64, 67 -> 0, 4, 7).
        assert [note.pitch for note in window._clipboard_notes] == [0, 4, 7]
        assert window._clipboard_root_pitch == 60
    finally:
        window.close()
        app.processEvents()


def test_paste_without_a_cursor_falls_back_to_playhead_and_original_pitch():
    """Offscreen the pointer is not over the grid, so this is the fallback path."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()

        window.piano_roll.set_playhead(10.0)
        window._paste_notes()
        app.processEvents()

        assert _pitches_and_starts(window) == [
            (60, 2.0),
            (64, 2.5),
            (67, 3.2),
            (60, 10.0),
            (64, 10.5),
            (67, 11.2),
        ]
    finally:
        window.close()
        app.processEvents()


def test_paste_at_a_target_transposes_and_keeps_intervals():
    """Anchoring on a different pitch moves the pattern without reshaping it."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()

        # C4-E4-G4 (60, 64, 67) anchored onto G4 becomes G4-B4-D5.
        window._insert_notes(
            window._clipboard_notes, at_seconds=10.0, at_pitch=67, verb="Pasted"
        )
        app.processEvents()

        pasted = [
            (note.pitch, round(note.start_seconds, 3))
            for note in window.state.current_notes
            if note.start_seconds >= 10.0
        ]
        assert pasted == [(67, 10.0), (71, 10.5), (74, 11.2)]
        intervals = [pasted[i + 1][0] - pasted[i][0] for i in range(len(pasted) - 1)]
        assert intervals == [4, 3], "intervals must survive the transposition"
    finally:
        window.close()
        app.processEvents()


def test_paste_clamps_transposition_to_the_midi_range():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()

        window._insert_notes(
            window._clipboard_notes, at_seconds=20.0, at_pitch=126, verb="Pasted"
        )
        app.processEvents()

        pitches = [
            note.pitch for note in window.state.current_notes if note.start_seconds >= 20.0
        ]
        assert pitches and all(0 <= pitch <= 127 for pitch in pitches)
    finally:
        window.close()
        app.processEvents()


def test_cursor_target_is_none_when_the_pointer_is_off_the_grid():
    app, window = _window()
    try:
        # Nothing is hovering the offscreen widget, so there is no target.
        assert window.piano_roll.cursor_target() is None
    finally:
        window.close()
        app.processEvents()


def test_paste_uses_the_cursor_target_when_one_is_available():
    """The pointer supplies both the time and the pitch for the paste."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()

        # Stand in for a pointer hovering the G4 row at t=10.
        window.piano_roll.cursor_target = lambda: (10.0, 67)
        window.piano_roll.set_playhead(99.0)  # must be ignored
        window._paste_notes()
        app.processEvents()

        pasted = [
            (note.pitch, round(note.start_seconds, 3))
            for note in window.state.current_notes
            if note.start_seconds >= 10.0
        ]
        assert pasted == [(67, 10.0), (71, 10.5), (74, 11.2)]
    finally:
        window.close()
        app.processEvents()


def test_cursor_target_maps_a_point_back_to_its_own_note():
    """cursor_target's mapping must agree with where a note is drawn."""

    app, window = _window()
    try:
        roll = window.piano_roll
        note = window.state.current_notes[1]
        rect = roll._note_rect(note)

        # Same mappings cursor_target uses, at the note's own centre.
        assert roll._pitch_at_y(rect.center().y()) == note.pitch
        assert roll.seconds_at_x(rect.left()) == pytest.approx(
            note.start_seconds, abs=roll.seconds_per_pixel
        )
    finally:
        window.close()
        app.processEvents()


def test_pasted_notes_become_the_selection():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()
        window._copy_selected_notes()
        window.piano_roll.set_playhead(8.0)
        window._paste_notes()
        app.processEvents()

        pasted = sorted(window.selected_indices)
        assert len(pasted) == 2
        notes = window.state.current_notes
        assert [round(notes[index].start_seconds, 3) for index in pasted] == [8.0, 8.5]
    finally:
        window.close()
        app.processEvents()


def test_paste_is_a_single_undo_step():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()
        window.piano_roll.set_playhead(10.0)
        window._paste_notes()
        app.processEvents()
        assert len(window.state.current_notes) == 6

        window._undo_edit()
        app.processEvents()
        assert len(window.state.current_notes) == 3
    finally:
        window.close()
        app.processEvents()


def test_paste_can_repeat_at_different_playhead_positions():
    """The buffer survives a paste, so it can be stamped repeatedly."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        window._copy_selected_notes()

        for position in (6.0, 9.0, 12.0):
            window.piano_roll.set_playhead(position)
            window._paste_notes()
            app.processEvents()

        starts = sorted(round(note.start_seconds, 3) for note in window.state.current_notes)
        assert starts == [2.0, 2.5, 3.2, 6.0, 9.0, 12.0]
    finally:
        window.close()
        app.processEvents()


def test_duplicate_lands_beside_the_source_not_at_the_playhead():
    app, window = _window()
    try:
        # Playhead deliberately far away: duplicate must ignore it.
        window.piano_roll.set_playhead(50.0)
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        window._duplicate_selected_notes()
        app.processEvents()

        offset = window.DUPLICATE_OFFSET_SECONDS
        assert (60, round(2.0 + offset, 3)) in _pitches_and_starts(window)
    finally:
        window.close()
        app.processEvents()


def test_duplicate_preserves_gaps_across_a_group():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._duplicate_selected_notes()
        app.processEvents()

        offset = window.DUPLICATE_OFFSET_SECONDS
        starts = _pitches_and_starts(window)
        for pitch, original in ((60, 2.0), (64, 2.5), (67, 3.2)):
            assert (pitch, round(original + offset, 3)) in starts
    finally:
        window.close()
        app.processEvents()


def test_duplicate_is_a_single_undo_step():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._duplicate_selected_notes()
        app.processEvents()
        assert len(window.state.current_notes) == 6

        window._undo_edit()
        app.processEvents()
        assert len(window.state.current_notes) == 3
    finally:
        window.close()
        app.processEvents()


def test_copy_with_empty_selection_is_a_no_op_with_a_message():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices(set())
        app.processEvents()
        window._copy_selected_notes()

        assert window._clipboard_notes == []
        assert "Nothing to copy" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_paste_with_empty_buffer_is_a_no_op_with_a_message():
    app, window = _window()
    try:
        before = len(window.state.current_notes)
        window._paste_notes()
        app.processEvents()

        assert len(window.state.current_notes) == before
        assert "Nothing to paste" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_duplicate_with_empty_selection_is_a_no_op_with_a_message():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices(set())
        app.processEvents()
        before = len(window.state.current_notes)
        window._duplicate_selected_notes()
        app.processEvents()

        assert len(window.state.current_notes) == before
        assert "Nothing to duplicate" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_copied_notes_are_unaffected_by_later_edits_to_the_source():
    """The buffer holds copies, so deleting the source does not empty it."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._copy_selected_notes()

        window._delete_selected_note()
        app.processEvents()
        assert window.state.current_notes == []

        window.piano_roll.set_playhead(5.0)
        window._paste_notes()
        app.processEvents()
        assert len(window.state.current_notes) == 3
    finally:
        window.close()
        app.processEvents()


def test_paste_clamps_negative_playhead_to_zero():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        window._copy_selected_notes()

        window.piano_roll.set_playhead(0.0)
        window._paste_notes()
        app.processEvents()

        assert all(note.start_seconds >= 0.0 for note in window.state.current_notes)
    finally:
        window.close()
        app.processEvents()


def test_shortcuts_are_registered():
    """Ctrl+C / Ctrl+V / Ctrl+D reach the handlers."""

    from PySide6.QtGui import QKeySequence, QShortcut

    app, window = _window()
    try:
        bound = {
            shortcut.key().toString()
            for shortcut in window.findChildren(QShortcut)
        }
        assert QKeySequence(QKeySequence.StandardKey.Copy).toString() in bound
        assert QKeySequence(QKeySequence.StandardKey.Paste).toString() in bound
        assert "Ctrl+D" in bound
    finally:
        window.close()
        app.processEvents()
