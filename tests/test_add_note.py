"""Creating MIDI notes by double-clicking empty piano-roll space (issue #37).

Covers the state helper (``add_gui_note``) and the widget gesture: double-click
on empty space emits ``note_created`` with the clicked pitch/time, while a
double-click on an existing note does not create anything.
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
    frames = [i * 0.1 for i in range(20)]
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
    roll.resize(1200, len(midi_notes) * roll.note_height + 4)
    roll.set_data(heatmap, notes, full_duration_seconds=2.0)
    return roll


def _double_click(roll, x, y):
    """Send a left-button double-click at canvas (x, y)."""

    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    roll.mouseDoubleClickEvent(event)


# --- state helper -----------------------------------------------------------


def test_add_gui_note_inserts_in_start_time_order():
    from notegrabber.gui.state import GuiMidiNote, add_gui_note

    notes = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.4, velocity=90),
        GuiMidiNote(pitch=62, start_seconds=1.0, duration_seconds=0.4, velocity=90),
    ]
    created = GuiMidiNote(pitch=64, start_seconds=0.5, duration_seconds=0.25, velocity=90)
    updated, index = add_gui_note(notes, created)

    assert index == 1
    assert [note.start_seconds for note in updated] == [0.0, 0.5, 1.0]
    assert updated[index].pitch == 64
    # The input list is not mutated.
    assert len(notes) == 2


def test_add_gui_note_appends_when_latest():
    from notegrabber.gui.state import GuiMidiNote, add_gui_note

    notes = [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.4, velocity=90)]
    created = GuiMidiNote(pitch=61, start_seconds=5.0, duration_seconds=0.25, velocity=90)
    updated, index = add_gui_note(notes, created)

    assert index == 1
    assert updated[-1].start_seconds == 5.0


def test_add_gui_note_into_empty_list():
    from notegrabber.gui.state import GuiMidiNote, add_gui_note

    created = GuiMidiNote(pitch=72, start_seconds=0.3, duration_seconds=0.25, velocity=90)
    updated, index = add_gui_note([], created)

    assert index == 0 and len(updated) == 1


def test_add_gui_note_normalizes_out_of_range_values():
    """Out-of-range pitch/velocity/time are clamped, like every other edit path."""

    from notegrabber.gui.state import GuiMidiNote, add_gui_note

    created = GuiMidiNote(pitch=999, start_seconds=-4.0, duration_seconds=0.0, velocity=999)
    updated, index = add_gui_note([], created)
    note = updated[index]

    assert note.pitch == 127
    assert note.velocity == 127
    assert note.start_seconds == 0.0
    assert note.duration_seconds > 0.0


# --- widget gesture ---------------------------------------------------------


def test_double_click_empty_space_emits_note_created():
    from notegrabber.gui.state import GuiMidiNote

    existing = GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.3, velocity=90)
    roll = _widget_with_notes([existing])

    emitted = []
    roll.note_created.connect(lambda *args: emitted.append(args))

    # An empty row (pitch 67), well past the existing note in time.
    target = GuiMidiNote(pitch=67, start_seconds=1.2, duration_seconds=0.1, velocity=1)
    rect = roll._note_rect(target)
    _double_click(roll, rect.left(), rect.center().y())

    assert len(emitted) == 1
    start_seconds, duration_seconds, pitch, velocity = emitted[0]
    assert pitch == 67
    assert start_seconds == pytest.approx(1.2, abs=0.05)
    assert duration_seconds == roll.new_note_duration_seconds
    assert velocity == roll.new_note_velocity


def test_double_click_on_existing_note_does_not_create():
    from notegrabber.gui.state import GuiMidiNote

    existing = GuiMidiNote(pitch=60, start_seconds=0.2, duration_seconds=0.6, velocity=90)
    roll = _widget_with_notes([existing])

    emitted = []
    roll.note_created.connect(lambda *args: emitted.append(args))

    rect = roll._note_rect(existing)
    _double_click(roll, rect.center().x(), rect.center().y())

    assert emitted == []


def test_double_click_on_keyboard_gutter_does_not_create():
    """The left keyboard strip is not part of the timeline, so it creates nothing."""

    roll = _widget_with_notes([])

    emitted = []
    roll.note_created.connect(lambda *args: emitted.append(args))

    _double_click(roll, roll.keyboard_width / 2.0, roll.note_height * 2.0)

    assert emitted == []


def test_double_click_without_heatmap_does_not_create():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    roll = PianoRollWidget()

    emitted = []
    roll.note_created.connect(lambda *args: emitted.append(args))

    _double_click(roll, 300.0, 40.0)

    assert emitted == []


def test_double_click_does_not_leave_a_drag_armed():
    """Creation must not leave a half-armed drag that a later move would apply."""

    roll = _widget_with_notes([])

    target_y = roll.note_height * 3.0
    _double_click(roll, roll.keyboard_width + 120.0, target_y)

    assert roll.drag_mode is None
    assert roll.drag_note_index is None
    assert roll.drag_original_note is None
