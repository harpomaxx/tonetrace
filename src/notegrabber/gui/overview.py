"""Low-resolution full-song pitch overview helpers for ToneTrace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

OVERVIEW_SAMPLE_RATE = 8_000
OVERVIEW_FRAME_SECONDS = 0.5
OVERVIEW_MIN_MIDI = 36  # C2
OVERVIEW_BINS = 60
OVERVIEW_MAX_FRAMES = 1_200


@dataclass(frozen=True)
class PitchOverview:
    """Reduced pitch-salience overview for navigating long audio files."""

    frame_times: list[float]
    midi_notes: list[int]
    activations: list[list[float]]
    duration_seconds: float

    @property
    def frame_count(self) -> int:
        return len(self.frame_times)

    @property
    def band_count(self) -> int:
        return len(self.midi_notes)

    def activation(self, frame_index: int, band_index: int) -> float:
        if frame_index < 0 or frame_index >= len(self.activations):
            return 0.0
        row = self.activations[frame_index]
        if band_index < 0 or band_index >= len(row):
            return 0.0
        return max(0.0, min(1.0, float(row[band_index])))


def build_pitch_overview(
    audio_path: Path,
    *,
    sample_rate: int = OVERVIEW_SAMPLE_RATE,
    frame_seconds: float = OVERVIEW_FRAME_SECONDS,
    min_midi: int = OVERVIEW_MIN_MIDI,
    bins: int = OVERVIEW_BINS,
    max_frames: int = OVERVIEW_MAX_FRAMES,
) -> PitchOverview:
    """Build a cheap low-resolution CQT overview for full-song navigation."""

    try:
        import librosa  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Pitch overview requires librosa/numpy; install with `python3 -m pip install -e '.[standalone]'`."
        ) from exc

    audio, actual_sample_rate = librosa.load(audio_path, sr=sample_rate, mono=True)
    duration_seconds = float(len(audio) / actual_sample_rate) if actual_sample_rate > 0 else 0.0
    if len(audio) == 0 or duration_seconds <= 0:
        raise ValueError("audio file contains no decoded samples for overview")

    hop_length = max(1, round(actual_sample_rate * frame_seconds))
    cqt = librosa.cqt(
        audio,
        sr=actual_sample_rate,
        hop_length=hop_length,
        fmin=librosa.midi_to_hz(min_midi),
        n_bins=bins,
        bins_per_octave=12,
    )
    magnitudes = np.abs(cqt).T
    if magnitudes.size == 0:
        raise ValueError("overview CQT produced no frames")
    scale = float(np.percentile(magnitudes, 98)) or float(magnitudes.max()) or 1.0
    normalized = np.clip(magnitudes / scale, 0.0, 1.0)
    frame_times = [float(index * hop_length / actual_sample_rate) for index in range(normalized.shape[0])]
    activations = [[float(value) for value in row] for row in normalized.tolist()]
    frame_times, activations = downsample_overview_frames(frame_times, activations, max_frames=max_frames)
    return PitchOverview(
        frame_times=frame_times,
        midi_notes=list(range(min_midi, min_midi + bins)),
        activations=activations,
        duration_seconds=duration_seconds,
    )


def downsample_overview_frames(
    frame_times: list[float],
    activations: list[list[float]],
    *,
    max_frames: int = OVERVIEW_MAX_FRAMES,
) -> tuple[list[float], list[list[float]]]:
    """Downsample overview frames by max-pooling consecutive time chunks."""

    if len(frame_times) <= max_frames or max_frames <= 0:
        return list(frame_times), [list(row) for row in activations]
    step = max(1, len(frame_times) // max_frames)
    pooled_times: list[float] = []
    pooled_activations: list[list[float]] = []
    for start in range(0, len(frame_times), step):
        rows = activations[start : start + step]
        if not rows:
            continue
        pooled_times.append(frame_times[start])
        pooled_activations.append(_max_pool_rows(rows))
        if len(pooled_times) >= max_frames:
            break
    return pooled_times, pooled_activations


def _max_pool_rows(rows: Iterable[list[float]]) -> list[float]:
    iterator = iter(rows)
    try:
        pooled = [float(value) for value in next(iterator)]
    except StopIteration:
        return []
    for row in iterator:
        for index, value in enumerate(row):
            if index < len(pooled):
                pooled[index] = max(pooled[index], float(value))
    return pooled
