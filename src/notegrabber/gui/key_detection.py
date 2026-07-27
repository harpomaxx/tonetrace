"""Musical key/scale detection from extracted notes.

Qt-free and dependency-free (pure Python, no numpy) so it can be unit-tested in
headless environments and reused by non-Qt frontends, mirroring the models in
``state.py``.

The estimator builds a 12-bin pitch-class profile by weighting each note by its
duration and velocity, then scores that profile against the 24 major/minor key
templates with a Krumhansl-Schmuckler-style correlation. It reports the best key
plus a confidence and a short ranked list of alternatives, and flags low
confidence for atonal, percussive, or very short material rather than forcing a
guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Kessler key profiles (major and minor), the standard tonal-hierarchy
# weights used for key finding. Index 0 is the tonic of the mode.
_KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Below this total weight the profile is too thin to trust (e.g. a very short or
# near-silent slice); we report "uncertain" instead of a key.
MIN_PROFILE_WEIGHT = 1e-6

# When the best key barely beats the runner-up, the result is ambiguous. This is
# the minimum gap (best minus second-best correlation) to call a key "confident".
DEFAULT_CONFIDENCE_FLOOR = 0.10


@dataclass(frozen=True)
class KeyCandidate:
    """A single scored key hypothesis."""

    tonic: int  # pitch class 0-11 (0 = C)
    mode: str  # "major" or "minor"
    score: float  # correlation with the key template, roughly [-1, 1]

    @property
    def name(self) -> str:
        return f"{PITCH_CLASS_NAMES[self.tonic]} {self.mode}"


@dataclass(frozen=True)
class KeyEstimate:
    """Result of key detection over some notes."""

    candidate: KeyCandidate | None
    confidence: float  # gap between best and second-best score, clamped to [0, 1]
    is_confident: bool
    alternatives: list[KeyCandidate] = field(default_factory=list)

    @property
    def label(self) -> str:
        """A short human-readable label for the GUI."""

        if self.candidate is None:
            return "Detected key: —"
        suffix = "" if self.is_confident else " (uncertain)"
        return f"Detected key: {self.candidate.name}{suffix}"


def pitch_class_profile(
    notes: Iterable[object],
    *,
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
) -> list[float]:
    """Build a 12-bin pitch-class profile from notes, weighted by duration x velocity.

    ``notes`` are any objects with ``pitch``, ``start_seconds``, ``duration_seconds``,
    and ``velocity`` attributes (e.g. ``GuiMidiNote``). Notes are clipped to the
    ``[start_seconds, end_seconds]`` window so a selected slice can be analysed;
    ``end_seconds=None`` means "to the end". Only the overlapping portion of each
    note contributes, so a note straddling the range boundary is weighted by the
    part inside the range.
    """

    profile = [0.0] * 12
    for note in notes:
        note_start = float(getattr(note, "start_seconds"))
        note_duration = float(getattr(note, "duration_seconds"))
        note_end = note_start + note_duration
        # Clip the note to the requested window.
        clipped_start = max(note_start, start_seconds)
        clipped_end = note_end if end_seconds is None else min(note_end, end_seconds)
        overlap = clipped_end - clipped_start
        if overlap <= 0.0:
            continue
        velocity = max(0.0, float(getattr(note, "velocity")))
        pitch_class = int(getattr(note, "pitch")) % 12
        profile[pitch_class] += overlap * velocity
    return profile


def _correlation(profile: Sequence[float], template: Sequence[float]) -> float:
    """Pearson correlation between a 12-bin profile and a key template."""

    n = len(profile)
    mean_p = sum(profile) / n
    mean_t = sum(template) / n
    cov = 0.0
    var_p = 0.0
    var_t = 0.0
    for value_p, value_t in zip(profile, template):
        dp = value_p - mean_p
        dt = value_t - mean_t
        cov += dp * dt
        var_p += dp * dp
        var_t += dt * dt
    denom = math.sqrt(var_p * var_t)
    if denom == 0.0:
        return 0.0
    return cov / denom


def _rotated(template: Sequence[float], tonic: int) -> list[float]:
    """Rotate a mode template so its tonic sits at ``tonic`` (pitch class)."""

    return [template[(index - tonic) % 12] for index in range(12)]


def detect_key_from_profile(
    profile: Sequence[float],
    *,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    max_alternatives: int = 3,
) -> KeyEstimate:
    """Score a 12-bin pitch-class profile against all 24 keys."""

    if len(profile) != 12 or sum(profile) <= MIN_PROFILE_WEIGHT:
        return KeyEstimate(candidate=None, confidence=0.0, is_confident=False, alternatives=[])

    candidates: list[KeyCandidate] = []
    for tonic in range(12):
        candidates.append(KeyCandidate(tonic, "major", _correlation(profile, _rotated(_KK_MAJOR, tonic))))
        candidates.append(KeyCandidate(tonic, "minor", _correlation(profile, _rotated(_KK_MINOR, tonic))))

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    best = candidates[0]
    runner_up_score = candidates[1].score if len(candidates) > 1 else 0.0
    confidence = max(0.0, min(1.0, best.score - runner_up_score))
    is_confident = confidence >= confidence_floor and best.score > 0.0
    return KeyEstimate(
        candidate=best,
        confidence=confidence,
        is_confident=is_confident,
        alternatives=candidates[1 : 1 + max(0, max_alternatives)],
    )


def detect_key_from_notes(
    notes: Iterable[object],
    *,
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> KeyEstimate:
    """Detect the key of the given notes (optionally within a time window)."""

    profile = pitch_class_profile(notes, start_seconds=start_seconds, end_seconds=end_seconds)
    return detect_key_from_profile(profile, confidence_floor=confidence_floor)
