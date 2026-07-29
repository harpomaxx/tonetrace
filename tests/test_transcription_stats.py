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


def test_selection_scopes_note_count_and_labels_strip() -> None:
    notes = [
        _note(60, start=0.0),
        _note(62, start=1.0),
        _note(64, start=5.0),   # inside the [4, 8] window
        _note(65, start=6.0),   # inside
        _note(67, start=10.0),  # outside
    ]
    whole = compute_stats(notes, duration_seconds=12.0)
    assert whole.note_count == 5
    assert not whole.is_selection
    assert not whole.strip_text().startswith("Selection:")

    sliced = compute_stats(
        notes, duration_seconds=4.0, start_seconds=4.0, end_seconds=8.0, is_selection=True
    )
    assert sliced.note_count == 2  # only the two notes in [4, 8]
    assert sliced.is_selection
    assert sliced.strip_text().startswith("Selection:")
    assert "0:04" in sliced.strip_text()  # window length, not whole-song duration


def test_selection_tempo_uses_only_windowed_notes() -> None:
    # 120 BPM (0.5s spacing) inside the window; a couple of far-away notes outside.
    windowed = [_note(60 + (i % 3), start=10.0 + i * 0.5) for i in range(16)]
    outliers = [_note(60, start=0.0), _note(60, start=1.0)]
    stats = compute_stats(
        windowed + outliers,
        duration_seconds=8.0,
        start_seconds=10.0,
        end_seconds=18.0,
        is_selection=True,
    )
    assert stats.tempo_bpm is not None
    assert stats.tempo_bpm == pytest.approx(120, abs=2.0)


def test_note_straddling_selection_boundary_is_included() -> None:
    # A note starting before the window but overlapping into it counts.
    note = _note(60, start=3.0, duration=3.0)  # spans 3.0-6.0
    stats = compute_stats(
        [note], duration_seconds=3.0, start_seconds=5.0, end_seconds=8.0, is_selection=True
    )
    assert stats.note_count == 1
