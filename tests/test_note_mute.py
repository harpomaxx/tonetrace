"""Non-destructive mute of notes (issue #66).

M toggles mute for the selection. Muted notes stay in the project, on the roll
and in the sequence table -- selectable and editable -- but are excluded from
the MIDI preview and from export. That makes "which notes do I keep?" an
audition loop rather than a delete/undo loop.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.4, velocity=90, muted=False):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch,
        start_seconds=start,
        duration_seconds=duration,
        velocity=velocity,
        muted=muted,
    )


# --- state helpers ----------------------------------------------------------


def test_notes_are_audible_by_default():
    assert _note(60, 0.0).muted is False


def test_audible_gui_notes_drops_muted():
    from notegrabber.gui.state import audible_gui_notes

    notes = [_note(60, 0.0), _note(64, 1.0, muted=True), _note(67, 2.0)]
    assert [note.pitch for note in audible_gui_notes(notes)] == [60, 67]


def test_gui_notes_to_midi_excludes_muted():
    """Every export path goes through here, so muting is enforced centrally."""

    from notegrabber.gui.state import gui_notes_to_midi

    notes = [_note(60, 0.0), _note(64, 1.0, muted=True)]
    assert len(gui_notes_to_midi(notes)) == 1


def test_set_gui_notes_muted_marks_only_the_given_indices():
    from notegrabber.gui.state import set_gui_notes_muted

    notes = [_note(60, 0.0), _note(64, 1.0), _note(67, 2.0)]
    updated = set_gui_notes_muted(notes, {0, 2}, True)

    assert [note.muted for note in updated] == [True, False, True]
    assert [note.muted for note in notes] == [False, False, False], "input not mutated"


def test_set_gui_notes_muted_ignores_out_of_range():
    from notegrabber.gui.state import set_gui_notes_muted

    notes = [_note(60, 0.0)]
    updated = set_gui_notes_muted(notes, {5, -1}, True)
    assert updated[0].muted is False


def test_muting_preserves_every_other_field():
    from notegrabber.gui.state import set_gui_notes_muted

    from dataclasses import replace

    bends = ((0.0, 0.0), (0.2, 1.0))
    notes = [replace(_note(60, 1.5, duration=0.75, velocity=101), pitch_bends=bends)]
    updated = set_gui_notes_muted(notes, {0}, True)

    note = updated[0]
    assert note.muted is True
    assert (note.pitch, note.start_seconds, note.duration_seconds, note.velocity) == (
        60,
        1.5,
        0.75,
        101,
    )
    assert note.pitch_bends == bends


# --- window wiring ----------------------------------------------------------


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
        notes = [_note(60, 1.0), _note(64, 2.0), _note(67, 3.0)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def _press_m(window):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    window.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier)
    )
    QApplication.processEvents()


def _muted_flags(window):
    return [note.muted for note in window.state.current_notes]


def test_m_mutes_the_selection():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 2})
        app.processEvents()

        _press_m(window)

        assert _muted_flags(window) == [True, False, True]
    finally:
        window.close()
        app.processEvents()


def test_m_unmutes_an_already_muted_selection():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 2})
        app.processEvents()

        _press_m(window)
        _press_m(window)

        assert _muted_flags(window) == [False, False, False]
    finally:
        window.close()
        app.processEvents()


def test_a_mixed_selection_mutes_wholesale():
    """"Mute this lot" should not flip each note independently."""

    app, window = _window([_note(60, 1.0, muted=True), _note(64, 2.0)])
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()

        _press_m(window)

        assert _muted_flags(window) == [True, True]
    finally:
        window.close()
        app.processEvents()


def test_muted_notes_stay_on_the_roll_and_selectable():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        _press_m(window)

        assert len(window.piano_roll.notes) == 3, "muted notes are not removed"
        assert window.piano_roll.selected_indices == {0}
        assert window.piano_roll._note_rect(window.state.current_notes[0]) is not None
    finally:
        window.close()
        app.processEvents()


def test_muted_notes_are_excluded_from_export():
    from notegrabber.gui.state import gui_notes_to_midi

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 2})
        app.processEvents()
        _press_m(window)

        assert len(gui_notes_to_midi(window.state.current_notes)) == 1
    finally:
        window.close()
        app.processEvents()


def test_muted_notes_are_excluded_from_the_midi_preview():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({1})
        app.processEvents()
        _press_m(window)

        preview = window._notes_for_midi_preview(window.state.current_notes)
        assert [note.pitch for note in preview] == [60, 67]
    finally:
        window.close()
        app.processEvents()


def test_muting_is_a_single_undo_step_and_survives_undo():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()

        _press_m(window)
        assert _muted_flags(window) == [True, True, True]

        window._undo_edit()
        app.processEvents()
        assert _muted_flags(window) == [False, False, False]

        window._redo_edit()
        app.processEvents()
        assert _muted_flags(window) == [True, True, True]
    finally:
        window.close()
        app.processEvents()


def test_muted_notes_can_still_be_edited():
    """Mute must not make a note inert -- it stays a normal editable note."""

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        _press_m(window)

        window._edit_note(
            0,
            start_seconds=5.0,
            duration_seconds=0.4,
            pitch=72,
            velocity=90,
            status_prefix="Edited",
            update_preview=True,
        )
        app.processEvents()

        note = window.state.current_notes[0]
        assert note.pitch == 72
        assert note.muted is True, "editing a muted note must not silently unmute it"
    finally:
        window.close()
        app.processEvents()


def test_muted_notes_can_still_be_nudged():
    from PySide6.QtCore import Qt

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        _press_m(window)

        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        window.keyPressEvent(
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        )
        app.processEvents()

        note = window.state.current_notes[0]
        assert note.pitch == 61
        assert note.muted is True
    finally:
        window.close()
        app.processEvents()


def test_m_without_a_selection_is_not_consumed():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices(set())
        app.processEvents()

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier)
        assert window.handle_piano_roll_key(event) is False
    finally:
        window.close()
        app.processEvents()


def test_ctrl_m_is_not_treated_as_mute():
    """Only a bare M mutes, leaving modified combinations free."""

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()

        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier
        )
        assert window.handle_piano_roll_key(event) is False
        assert _muted_flags(window) == [False, False, False]
    finally:
        window.close()
        app.processEvents()


def test_status_reports_the_audible_count():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 2})
        app.processEvents()
        _press_m(window)

        text = window.transport.status_label.text()
        assert "Muted 2 notes" in text
        assert "1 audible note." in text, "singular should not read '1 audible notes'"
    finally:
        window.close()
        app.processEvents()


def test_the_sequence_table_marks_muted_notes():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0})
        app.processEvents()
        _press_m(window)

        assert window.sequence.table.item(0, 1).text() == "(C4)"
    finally:
        window.close()
        app.processEvents()
