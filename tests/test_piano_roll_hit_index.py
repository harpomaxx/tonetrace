"""Piano-roll hover hit-testing uses a per-row index (issue #29).

Hit-testing must scan only the notes on the pitch row under the cursor, not
every note. These tests pin that the index yields the same result the old
linear scan did, follows a note when a drag moves its pitch, and stays correct
after committed edits.
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


def _center_of(roll, note):
    rect = roll._note_rect(note)
    return rect.center().x(), rect.center().y()


def test_index_buckets_notes_by_pitch_row():
    from notegrabber.gui.state import GuiMidiNote

    notes = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.4, velocity=90),
        GuiMidiNote(pitch=67, start_seconds=0.5, duration_seconds=0.4, velocity=90),
        GuiMidiNote(pitch=60, start_seconds=1.0, duration_seconds=0.4, velocity=90),
    ]
    roll = _widget_with_notes(notes)
    # Two notes share pitch 60's row; one is on pitch 67's row.
    row60 = roll._pitch_to_row[60]
    row67 = roll._pitch_to_row[67]
    assert sorted(roll._notes_by_row[row60]) == [0, 2]
    assert roll._notes_by_row[row67] == [1]


def test_hit_test_returns_note_under_cursor():
    from notegrabber.gui.state import GuiMidiNote

    notes = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.4, velocity=90),
        GuiMidiNote(pitch=67, start_seconds=0.5, duration_seconds=0.4, velocity=90),
    ]
    roll = _widget_with_notes(notes)

    x, y = _center_of(roll, notes[1])
    hit = roll._note_hit_at(x, y)
    assert hit is not None and hit[0] == 1 and hit[1] == "move"

    # A point on an empty row returns no hit.
    x0, _ = _center_of(roll, notes[0])
    empty_y = _center_of(roll, GuiMidiNote(pitch=64, start_seconds=0.0, duration_seconds=0.1, velocity=1))[1]
    assert roll._note_hit_at(x0, empty_y) is None


def test_resize_handles_detected_at_note_edges():
    from notegrabber.gui.state import GuiMidiNote

    note = GuiMidiNote(pitch=63, start_seconds=0.4, duration_seconds=0.6, velocity=90)
    roll = _widget_with_notes([note])
    rect = roll._note_rect(note)
    y = rect.center().y()
    assert roll._note_hit_at(rect.left() + 1, y)[1] == "resize_start"
    assert roll._note_hit_at(rect.right() - 1, y)[1] == "resize_end"


def test_preview_edit_moves_note_between_row_buckets():
    from notegrabber.gui.state import GuiMidiNote

    note = GuiMidiNote(pitch=60, start_seconds=0.2, duration_seconds=0.4, velocity=90)
    roll = _widget_with_notes([note])
    row60 = roll._pitch_to_row[60]
    row65 = roll._pitch_to_row[65]
    assert roll._notes_by_row.get(row60) == [0]

    # Drag the note up to pitch 65: the index must follow so hover still hits it.
    moved = GuiMidiNote(pitch=65, start_seconds=0.2, duration_seconds=0.4, velocity=90)
    roll.preview_note_edit(0, moved)
    assert roll._notes_by_row.get(row60) in (None, [])
    assert roll._notes_by_row.get(row65) == [0]

    x, y = _center_of(roll, moved)
    hit = roll._note_hit_at(x, y)
    assert hit is not None and hit[0] == 0


def test_index_matches_linear_scan_across_many_notes():
    """The row-indexed hit-test agrees with a brute-force scan for every note."""

    from notegrabber.gui.state import GuiMidiNote

    midi_notes = list(range(48, 84))
    notes = [
        GuiMidiNote(pitch=48 + (i % 36), start_seconds=i * 0.05, duration_seconds=0.15, velocity=90)
        for i in range(200)
    ]
    roll = _widget_with_notes(notes, midi_notes=midi_notes)

    def brute_force(x, y):
        for index in range(len(roll.notes) - 1, -1, -1):
            rect = roll._note_rect(roll.notes[index])
            if rect is None or not rect.contains(x, y):
                continue
            if abs(x - rect.left()) <= 7.0:
                return index, "resize_start"
            if abs(x - rect.right()) <= 7.0:
                return index, "resize_end"
            return index, "move"
        return None

    for note in notes:
        rect = roll._note_rect(note)
        for x in (rect.left() + 1, rect.center().x(), rect.right() - 1):
            y = rect.center().y()
            assert roll._note_hit_at(x, y) == brute_force(x, y)
