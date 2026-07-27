"""Headless tests for transcription statistics (note count, duration, tempo)."""

from __future__ import annotations

import pytest

from notegrabber.gui.state import GuiMidiNote
from notegrabber.gui.transcription_stats import (
    compute_stats,
    estimate_tempo_bpm,
)


def _note(pitch: int, start: float, duration: float = 0.25, velocity: int = 100) -> GuiMidiNote:
    return GuiMidiNote(pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity)


def _steady_notes(bpm: float, count: int, start: float = 0.0) -> list[GuiMidiNote]:
    period = 60.0 / bpm
    return [_note(60 + (i % 5), start + i * period) for i in range(count)]


@pytest.mark.parametrize("bpm", [60, 90, 120, 150, 180])
def test_steady_onsets_recover_tempo(bpm: int) -> None:
    est = estimate_tempo_bpm(_steady_notes(bpm, 16))
    assert est is not None
    assert est == pytest.approx(bpm, abs=1.0)


def test_half_time_gaps_fold_into_range() -> None:
    # 40 BPM onsets (below the 60 floor) should fold up to 80 BPM.
    est = estimate_tempo_bpm(_steady_notes(40, 16))
    assert est is not None
    assert est == pytest.approx(80, abs=1.0)


def test_too_few_onsets_returns_none() -> None:
    assert estimate_tempo_bpm(_steady_notes(120, 3)) is None


def test_no_notes_returns_none() -> None:
    assert estimate_tempo_bpm([]) is None


def test_simultaneous_onsets_collapse_to_one_beat() -> None:
    # A chord (three notes at the same onset) must not create zero-length gaps
    # that swamp the estimate; the underlying pulse is still 120 BPM.
    notes: list[GuiMidiNote] = []
    period = 60.0 / 120.0
    for i in range(12):
        t = i * period
        notes.extend([_note(60, t), _note(64, t), _note(67, t)])  # chord
    est = estimate_tempo_bpm(notes)
    assert est is not None
    assert est == pytest.approx(120, abs=2.0)


def test_compute_stats_bundle() -> None:
    notes = _steady_notes(120, 16)
    stats = compute_stats(notes, duration_seconds=42.0)
    assert stats.note_count == 16
    assert stats.duration_seconds == pytest.approx(42.0)
    assert stats.tempo_bpm == pytest.approx(120, abs=1.0)
    assert stats.key.candidate is not None


def test_strip_text_is_terse_and_complete() -> None:
    stats = compute_stats(_steady_notes(120, 16), duration_seconds=125.0)
    text = stats.strip_text()
    assert "Notes 16" in text
    assert "2:05" in text  # 125 s -> 2:05
    assert "BPM" in text


def test_strip_text_handles_empty() -> None:
    stats = compute_stats([], duration_seconds=0.0)
    text = stats.strip_text()
    assert "Notes 0" in text
    assert "0:00" in text
    assert "— BPM" in text
    assert "—" in text  # key uncertain / none
