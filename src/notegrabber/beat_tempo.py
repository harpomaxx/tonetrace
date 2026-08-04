"""Audio-based tempo and beat estimation via librosa (issue #14).

The note-onset heuristic in ``gui/transcription_stats.py`` is dependency-free
and works well on steady material, but degrades on sparse, rubato or heavily
syncopated input, where it reports no tempo at all.  This module runs
``librosa.beat.beat_track`` on the audio itself, which is generally more robust
on rhythmic material.

It also returns **beat times**, which the note-onset heuristic cannot provide:
knowing a piece is 138 BPM does not say where beat one falls, and any future
quantize feature needs that phase information, not just the rate.

Deliberately Qt-free and importable without librosa installed, so the caller
can decide the policy and the tests can run headless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# Same fold range the note-onset estimator uses, so both paths report tempos in
# a comparable band and a switch between them is not a visible jump.
MIN_TEMPO_BPM = 60.0
MAX_TEMPO_BPM = 180.0

# Below this many detected beats there are too few gaps to judge steadiness at
# all. Kept low deliberately: a short clip has few beats because it is short,
# not because tracking failed -- a 2.8s sample yields 5 near-perfect beats, and
# a count threshold tuned for full songs would reject it. Steadiness
# (MAX_BEAT_INTERVAL_SPREAD) is the real quality signal; the count only has to
# be enough for that measure to mean something.
MIN_BEATS_FOR_CONFIDENCE = 4

# Beat-interval spread (std/mean) above which the tracker is reporting an
# unsteady grid -- rubato, free time, or simply a wrong lock.
MAX_BEAT_INTERVAL_SPREAD = 0.25


@dataclass(frozen=True)
class BeatEstimate:
    """Tempo and beat positions estimated from audio."""

    tempo_bpm: float | None
    beat_times: tuple[float, ...] = ()
    # Whether the estimate is steady enough to prefer over the note-onset
    # heuristic; see MIN_BEATS_FOR_CONFIDENCE / MAX_BEAT_INTERVAL_SPREAD.
    is_confident: bool = False
    error: str | None = None

    @property
    def beat_count(self) -> int:
        return len(self.beat_times)

    @property
    def first_beat_seconds(self) -> float | None:
        """Phase: where the tracker thinks the grid starts."""

        return self.beat_times[0] if self.beat_times else None


def fold_into_tempo_range(bpm: float) -> float:
    """Fold a BPM into [MIN_TEMPO_BPM, MAX_TEMPO_BPM] by doubling/halving.

    Beat trackers commonly lock to half or double the musical tempo; folding
    makes those agree with the note-onset estimator instead of looking like a
    disagreement about the piece.
    """

    if bpm <= 0.0:
        return bpm
    while bpm < MIN_TEMPO_BPM:
        bpm *= 2.0
    while bpm > MAX_TEMPO_BPM:
        bpm /= 2.0
    return bpm


def beat_interval_spread(beat_times: "list[float] | tuple[float, ...]") -> float | None:
    """Coefficient of variation of the gaps between beats.

    A steady grid has near-identical gaps (spread ~0). Rubato, a mis-locked
    tracker, or beats found in noise give a wide spread. Returns None when
    there are too few beats to say anything.
    """

    if len(beat_times) < 3:
        return None
    gaps = [b - a for a, b in zip(beat_times, beat_times[1:]) if b > a]
    if len(gaps) < 2:
        return None
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0.0:
        return None
    variance = sum((gap - mean_gap) ** 2 for gap in gaps) / len(gaps)
    return (variance**0.5) / mean_gap


def assess_beats(tempo_bpm: float | None, beat_times: "list[float] | tuple[float, ...]") -> bool:
    """Decide whether a beat-tracking result is steady enough to trust."""

    if tempo_bpm is None or tempo_bpm <= 0.0:
        return False
    if len(beat_times) < MIN_BEATS_FOR_CONFIDENCE:
        return False
    spread = beat_interval_spread(beat_times)
    if spread is None:
        return False
    return spread <= MAX_BEAT_INTERVAL_SPREAD


def estimate_beats_from_audio(audio_path: Path) -> BeatEstimate:
    """Estimate tempo and beat times for an audio file with librosa.

    Never raises: librosa is optional and beat tracking can fail on odd input,
    so problems come back as an estimate with ``error`` set and the caller
    falls back to the note-onset heuristic.
    """

    try:
        import librosa
    except Exception as exc:  # pragma: no cover - librosa is a GUI/analysis dep
        return BeatEstimate(tempo_bpm=None, error=f"librosa is not available: {exc}")

    try:
        samples, sample_rate = librosa.load(str(audio_path), sr=None, mono=True)
        if samples.size == 0:
            return BeatEstimate(tempo_bpm=None, error="Audio file is empty.")
        onset_envelope = librosa.onset.onset_strength(y=samples, sr=sample_rate)
        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_envelope,
            sr=sample_rate,
            units="frames",
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate)
    except Exception as exc:
        return BeatEstimate(tempo_bpm=None, error=f"Beat tracking failed: {exc}")

    # librosa returns tempo as a scalar or a 1-element array depending on
    # version; normalise before doing anything numeric with it.
    try:
        tempo_value = float(tempo if not hasattr(tempo, "__len__") else tempo[0])
    except (TypeError, IndexError, ValueError):
        return BeatEstimate(tempo_bpm=None, error="Beat tracking returned no tempo.")

    times = tuple(float(value) for value in beat_times)
    if tempo_value <= 0.0:
        return BeatEstimate(tempo_bpm=None, beat_times=times, error="Beat tracking returned no tempo.")

    folded = fold_into_tempo_range(tempo_value)
    return BeatEstimate(
        tempo_bpm=folded,
        beat_times=times,
        is_confident=assess_beats(folded, times),
    )
