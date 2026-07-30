"""Tests for pitch-bend support in the MIDI writer.

Notes carrying a ``pitch_bends`` contour must emit MIDI pitch-bend (0xE0) events
across their duration, set the bend range via RPN 0, and reset the wheel to
center at note-off so bends do not leak into later notes. Notes without bends
must produce a plain, unchanged file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

mido = pytest.importorskip("mido")

from notegrabber.midi import (
    PITCH_BEND_CENTER,
    PITCH_BEND_RANGE_SEMITONES,
    MidiNote,
    _bend_units_to_wheel,
    write_midi,
)


def _events(path: Path):
    return list(mido.MidiFile(str(path)))


def test_zero_bend_maps_to_center():
    assert _bend_units_to_wheel(0) == PITCH_BEND_CENTER


def test_bend_direction_and_symmetry():
    up = _bend_units_to_wheel(3)  # +1 semitone (3 units of 1/3 semitone)
    down = _bend_units_to_wheel(-3)
    assert up > PITCH_BEND_CENTER > down
    # Symmetric distance from center.
    assert abs((up - PITCH_BEND_CENTER) - (PITCH_BEND_CENTER - down)) <= 1


def test_extreme_bend_is_clamped_in_range():
    assert 0 <= _bend_units_to_wheel(10_000) <= 16383
    assert 0 <= _bend_units_to_wheel(-10_000) <= 16383


def test_note_with_bends_emits_pitchwheel_rpn_and_reset(tmp_path):
    out = tmp_path / "bend.mid"
    note = MidiNote(pitch=60, start_tick=0, duration_ticks=480, pitch_bends=(0, 1, 2, 3, 2, 0, -2, -3))
    write_midi(out, [note])

    events = _events(out)
    pitchwheels = [m for m in events if m.type == "pitchwheel"]
    controls = [(m.control, m.value) for m in events if m.type == "control_change"]

    # One wheel event per bend sample, plus a final reset to center.
    assert len(pitchwheels) == len(note.pitch_bends) + 1
    assert pitchwheels[-1].pitch == 0  # mido reports center as 0
    # Pitch-bend range set via RPN 0 (data-entry MSB = semitones).
    assert (6, PITCH_BEND_RANGE_SEMITONES) in controls


def test_note_without_bends_has_no_pitchwheel_or_rpn(tmp_path):
    out = tmp_path / "plain.mid"
    write_midi(out, [MidiNote(pitch=64, start_tick=0, duration_ticks=240)])

    events = _events(out)
    assert not [m for m in events if m.type == "pitchwheel"]
    assert not [m for m in events if m.type == "control_change"]


def test_bends_do_not_leak_between_sequential_notes(tmp_path):
    """A bent note followed by a plain note: the wheel must be centered before the second note-on."""

    out = tmp_path / "seq.mid"
    bent = MidiNote(pitch=60, start_tick=0, duration_ticks=480, pitch_bends=(3, 3, 3))
    plain = MidiNote(pitch=62, start_tick=480, duration_ticks=240)
    write_midi(out, [bent, plain])

    # Walk absolute time; the wheel value active when the 2nd note starts must be center.
    wheel = 0  # mido center
    saw_second_note_on = False
    for msg in _events(out):
        if msg.type == "pitchwheel":
            wheel = msg.pitch
        elif msg.type == "note_on" and msg.note == 62:
            saw_second_note_on = True
            assert wheel == 0, f"wheel not reset before second note (was {wheel})"
    assert saw_second_note_on
