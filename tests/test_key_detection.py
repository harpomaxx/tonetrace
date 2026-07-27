"""Headless tests for musical key/scale detection."""

from __future__ import annotations

import pytest

from notegrabber.gui.key_detection import (
    PITCH_CLASS_NAMES,
    detect_key_from_notes,
    detect_key_from_profile,
    pitch_class_profile,
)
from notegrabber.gui.state import GuiMidiNote


# Diatonic scale degrees (pitch-class offsets from the tonic).
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


def _scale_profile(tonic: int, offsets) -> list[float]:
    """A profile that is 1.0 on the scale degrees of a key, 0 elsewhere."""

    profile = [0.0] * 12
    for offset in offsets:
        profile[(tonic + offset) % 12] = 1.0
    return profile


def _note(pitch: int, start: float = 0.0, duration: float = 1.0, velocity: int = 100) -> GuiMidiNote:
    return GuiMidiNote(pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity)


def test_c_major_scale_profile_detects_c_major() -> None:
    estimate = detect_key_from_profile(_scale_profile(0, _MAJOR_SCALE))
    assert estimate.candidate is not None
    assert estimate.candidate.name == "C major"


def test_a_minor_profile_biased_to_tonic_detects_a_minor() -> None:
    # A minor and C major share the same seven notes; weight the A-minor tonic
    # triad (A, C, E) so the tonal hierarchy resolves to A minor.
    profile = _scale_profile(9, _MINOR_SCALE)
    for pc in (9, 0, 4):  # A, C, E
        profile[pc] += 2.0
    estimate = detect_key_from_profile(profile)
    assert estimate.candidate is not None
    assert estimate.candidate.name == "A minor"


@pytest.mark.parametrize("tonic", range(12))
def test_all_twelve_major_keys_round_trip(tonic: int) -> None:
    # Weight the tonic triad so each major key is unambiguous.
    profile = _scale_profile(tonic, _MAJOR_SCALE)
    for offset in (0, 4, 7):  # major triad
        profile[(tonic + offset) % 12] += 2.0
    estimate = detect_key_from_profile(profile)
    assert estimate.candidate is not None
    assert estimate.candidate.tonic == tonic
    assert estimate.candidate.mode == "major"
    assert estimate.candidate.name.startswith(PITCH_CLASS_NAMES[tonic])


def test_empty_profile_is_uncertain() -> None:
    estimate = detect_key_from_profile([0.0] * 12)
    assert estimate.candidate is None
    assert not estimate.is_confident
    assert estimate.label == "Detected key: —"


def test_uniform_profile_is_not_confident() -> None:
    # A flat (atonal) profile correlates equally with every key -> uncertain.
    estimate = detect_key_from_profile([1.0] * 12)
    assert not estimate.is_confident


def test_profile_weights_by_duration_and_velocity() -> None:
    notes = [
        _note(60, duration=4.0, velocity=120),  # C, heavily weighted
        _note(61, duration=0.1, velocity=10),   # C#, negligible
    ]
    profile = pitch_class_profile(notes)
    assert profile[0] > profile[1]
    assert profile[0] == pytest.approx(4.0 * 120)
    assert profile[1] == pytest.approx(0.1 * 10)


def test_profile_respects_time_window() -> None:
    notes = [
        _note(60, start=0.0, duration=1.0),   # C, before the window
        _note(62, start=5.0, duration=1.0),   # D, inside the window
    ]
    profile = pitch_class_profile(notes, start_seconds=4.0, end_seconds=8.0)
    assert profile[0] == pytest.approx(0.0)  # C excluded
    assert profile[2] > 0.0                  # D included


def test_note_straddling_window_is_partially_weighted() -> None:
    note = _note(60, start=3.0, duration=4.0, velocity=100)  # spans 3.0-7.0
    profile = pitch_class_profile([note], start_seconds=5.0, end_seconds=9.0)
    # Only the 5.0-7.0 portion (2 seconds) overlaps the window.
    assert profile[0] == pytest.approx(2.0 * 100)


def test_detect_key_from_notes_end_to_end() -> None:
    # A confident C major line.
    notes = [_note(pitch) for pitch in (60, 62, 64, 65, 67, 69, 71, 72)]
    for pitch in (60, 64, 67):  # emphasize the C triad
        notes.append(_note(pitch, duration=2.0))
    estimate = detect_key_from_notes(notes)
    assert estimate.candidate is not None
    assert estimate.candidate.name == "C major"
    assert estimate.is_confident
