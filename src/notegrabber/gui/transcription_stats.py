"""Summary statistics for a transcription (note count, duration, tempo, key).

Qt-free and dependency-free (pure Python, no numpy/librosa) so it can be
unit-tested headlessly and reused by non-Qt frontends, mirroring ``state.py`` and
``key_detection.py``. The GUI only reads the result.

Tempo is estimated from the inter-onset intervals of the notes themselves (no
audio re-decode, no extra dependency): the most common gap between successive
note onsets, folded into a musical range, is taken as the beat period. This is a
first-version heuristic -- it reports ``None`` for material too sparse or
irregular to trust rather than forcing a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .key_detection import KeyEstimate, detect_key_from_notes

# Tempo search bounds (BPM). Estimates are folded into this range by doubling or
# halving, since inter-onset gaps may capture half- or double-time.
MIN_TEMPO_BPM = 60.0
MAX_TEMPO_BPM = 180.0

# Need at least this many onset gaps to attempt a tempo estimate.
MIN_ONSET_GAPS_FOR_TEMPO = 4

# Two onsets within this many seconds are treated as the same beat (chord/roll),
# so their zero-ish gap does not dominate the estimate.
SIMULTANEOUS_ONSET_SECONDS = 0.04

# Fraction of the median gap used as the histogram bucket width when finding the
# most common inter-onset interval.
_GAP_CLUSTER_TOLERANCE = 0.15


@dataclass(frozen=True)
class TranscriptionStats:
    """Glanceable summary of the current transcription."""

    note_count: int
    duration_seconds: float
    tempo_bpm: float | None
    key: KeyEstimate
    # True when these stats describe a selected time range rather than the whole
    # transcription, so the strip can label it.
    is_selection: bool = False

    def _format_duration(self) -> str:
        total = max(0, int(round(self.duration_seconds)))
        return f"{total // 60}:{total % 60:02d}"

    def _format_tempo(self) -> str:
        return f"~{round(self.tempo_bpm)} BPM" if self.tempo_bpm is not None else "— BPM"

    def _format_key(self) -> str:
        # Strip the "Detected key: " prefix; the strip has its own layout.
        return self.key.label.replace("Detected key: ", "")

    def strip_text(self) -> str:
        """A terse one-line summary for an always-visible stats strip."""

        prefix = "Selection: " if self.is_selection else ""
        return (
            f"{prefix}Notes {self.note_count}"
            f"  ·  {self._format_duration()}"
            f"  ·  {self._format_tempo()}"
            f"  ·  {self._format_key()}"
        )


def _onset_times(notes: Iterable[object]) -> list[float]:
    """Sorted, de-duplicated note onset times (chords collapse to one onset)."""

    starts = sorted(float(getattr(note, "start_seconds")) for note in notes)
    if not starts:
        return []
    collapsed = [starts[0]]
    for start in starts[1:]:
        if start - collapsed[-1] > SIMULTANEOUS_ONSET_SECONDS:
            collapsed.append(start)
    return collapsed


def _fold_into_tempo_range(bpm: float) -> float:
    """Fold a BPM into [MIN_TEMPO_BPM, MAX_TEMPO_BPM] by doubling/halving."""

    if bpm <= 0.0:
        return bpm
    while bpm < MIN_TEMPO_BPM:
        bpm *= 2.0
    while bpm > MAX_TEMPO_BPM:
        bpm /= 2.0
    return bpm


def estimate_tempo_bpm(notes: Iterable[object]) -> float | None:
    """Estimate tempo (BPM) from note inter-onset intervals, or None if unclear."""

    onsets = _onset_times(notes)
    gaps = [b - a for a, b in zip(onsets, onsets[1:]) if b - a > 0.0]
    if len(gaps) < MIN_ONSET_GAPS_FOR_TEMPO:
        return None

    # Cluster gaps around the median gap width to find the dominant beat period,
    # which is more robust than a raw mean when rhythms mix note lengths.
    ordered = sorted(gaps)
    median_gap = ordered[len(ordered) // 2]
    if median_gap <= 0.0:
        return None
    tolerance = median_gap * _GAP_CLUSTER_TOLERANCE
    cluster = [gap for gap in gaps if abs(gap - median_gap) <= tolerance]
    beat_period = sum(cluster) / len(cluster) if cluster else median_gap
    if beat_period <= 0.0:
        return None
    return _fold_into_tempo_range(60.0 / beat_period)


def _notes_in_window(
    notes: Iterable[object],
    start_seconds: float,
    end_seconds: float | None,
) -> list[object]:
    """Return notes that overlap the ``[start, end]`` window (end None = to the end)."""

    kept: list[object] = []
    for note in notes:
        note_start = float(getattr(note, "start_seconds"))
        note_end = note_start + float(getattr(note, "duration_seconds"))
        if note_end <= start_seconds:
            continue
        if end_seconds is not None and note_start >= end_seconds:
            continue
        kept.append(note)
    return kept


def compute_stats(
    notes: Sequence[object],
    *,
    duration_seconds: float,
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
    is_selection: bool = False,
) -> TranscriptionStats:
    """Compute the stats bundle, optionally scoped to a selected time range.

    When ``is_selection`` is set (with ``start_seconds``/``end_seconds``), notes
    are restricted to that window for the count, tempo, and key, and
    ``duration_seconds`` should be the window length. Otherwise stats cover the
    whole note list.
    """

    if is_selection:
        scoped = _notes_in_window(notes, start_seconds, end_seconds)
        key = detect_key_from_notes(scoped, start_seconds=start_seconds, end_seconds=end_seconds)
    else:
        scoped = list(notes)
        key = detect_key_from_notes(scoped)

    return TranscriptionStats(
        note_count=len(scoped),
        duration_seconds=max(0.0, float(duration_seconds)),
        tempo_bpm=estimate_tempo_bpm(scoped),
        key=key,
        is_selection=is_selection,
    )
