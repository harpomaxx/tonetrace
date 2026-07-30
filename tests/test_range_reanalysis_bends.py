"""Regression: pitch bends must survive the range re-analysis path.

Moving the waveform region and re-analyzing runs notes through an offset and a
clip step. Both rebuilt the note without copying pitch_bends, silently dropping
the bend curve on any run after the first. These assert the bends are preserved.
"""

from __future__ import annotations

from notegrabber.gui.analysis_worker import _clip_midi_notes_to_duration, _offset_midi_notes
from notegrabber.gui.state import midi_notes_to_gui
from notegrabber.midi import MidiNote


def _bent_note() -> MidiNote:
    return MidiNote(pitch=60, start_tick=0, duration_ticks=480, pitch_bends=(0, 2, 4, 6))


def test_offset_preserves_pitch_bends():
    out = _offset_midi_notes([_bent_note()], 40.0)
    assert out[0].pitch_bends == (0, 2, 4, 6)


def test_clip_preserves_pitch_bends():
    out = _clip_midi_notes_to_duration([_bent_note()], 100.0)
    assert out[0].pitch_bends == (0, 2, 4, 6)


def test_full_range_path_reaches_gui_notes_with_bends():
    offset = _offset_midi_notes([_bent_note()], 40.0)
    clipped = _clip_midi_notes_to_duration(offset, 100.0)
    gui_notes = midi_notes_to_gui(clipped)
    assert gui_notes[0].pitch_bends is not None
    assert len(gui_notes[0].pitch_bends) == 4
