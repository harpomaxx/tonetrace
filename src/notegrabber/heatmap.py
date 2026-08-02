"""Compact heatmap storage and JSON adapters.

The CLI heatmap JSON schema is an interchange/export format.  Runtime callers
should use :class:`HeatmapData`, which stores the activation payload once as a
validated ``float32`` matrix when numpy is available and falls back to nested
Python rows only when numpy is genuinely unavailable.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO

from notegrabber.midi import MidiNote, TICKS_PER_SECOND


_FLOAT_TOLERANCE = 1e-6


def _load_numpy() -> Any:
    """Return numpy, or None when it is unavailable.

    Kept as a helper so tests can exercise the no-numpy fallback without
    destabilizing the interpreter by removing numpy modules globally.
    """

    try:
        import numpy as np  # type: ignore[import-not-found]
    except Exception:
        return None
    return np


@dataclass(frozen=True, eq=False)
class HeatmapData:
    """Pitch salience heatmap with compact runtime storage.

    ``activations`` is normally a C-contiguous numpy ``float32`` array with shape
    ``(frame_count, note_count)``.  If numpy cannot be imported, the constructor
    keeps a validated/clamped ``list[list[float]]`` fallback so the dependency-free
    simple backend and pure-Python accessors continue to work.
    """

    backend: str
    midi_notes: list[int]
    frame_times: list[float]
    activations: Any
    sample_rate: int
    hop_size: int
    window_size: int

    def __post_init__(self) -> None:
        midi_notes = [int(note) for note in self.midi_notes]
        frame_times = [float(time_seconds) for time_seconds in self.frame_times]
        if any(not math.isfinite(time_seconds) for time_seconds in frame_times):
            raise ValueError("heatmap frame times must be finite")
        object.__setattr__(self, "midi_notes", midi_notes)
        object.__setattr__(self, "frame_times", frame_times)
        object.__setattr__(self, "sample_rate", int(self.sample_rate))
        object.__setattr__(self, "hop_size", int(self.hop_size))
        object.__setattr__(self, "window_size", int(self.window_size))

        np = _load_numpy()
        if np is not None:
            matrix = self._validated_matrix(np, self.activations, len(frame_times), len(midi_notes))
            object.__setattr__(self, "activations", matrix)
        else:
            rows = self._validated_rows(self.activations, len(frame_times), len(midi_notes))
            object.__setattr__(self, "activations", rows)

    @staticmethod
    def _validated_matrix(np: Any, activations: Any, frame_count: int, note_count: int) -> Any:
        if _is_numpy_array(np, activations):
            matrix = activations
            if matrix.ndim != 2 or matrix.shape != (frame_count, note_count):
                raise ValueError("heatmap activation matrix shape does not match frame/note counts")
        else:
            if frame_count == 0 and not activations:
                matrix = np.zeros((0, note_count), dtype=np.float32)
            else:
                matrix = np.asarray(activations, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape != (frame_count, note_count):
                raise ValueError("heatmap activation matrix shape does not match frame/note counts")

        if matrix.size == 0:
            if matrix.dtype == np.float32 and matrix.flags.c_contiguous:
                return matrix
            return np.ascontiguousarray(matrix, dtype=np.float32)

        if matrix.dtype == np.float32 and matrix.flags.c_contiguous:
            min_value = float(matrix.min())
            max_value = float(matrix.max())
            if not math.isfinite(min_value) or not math.isfinite(max_value):
                raise ValueError("heatmap activations must be finite")
            if min_value >= 0.0 and max_value <= 1.0:
                return matrix
            matrix = matrix.copy()
        else:
            matrix = np.ascontiguousarray(matrix, dtype=np.float32)
            min_value = float(matrix.min())
            max_value = float(matrix.max())
            if not math.isfinite(min_value) or not math.isfinite(max_value):
                raise ValueError("heatmap activations must be finite")

        np.clip(matrix, 0.0, 1.0, out=matrix)
        return matrix

    @staticmethod
    def _validated_rows(activations: Any, frame_count: int, note_count: int) -> list[list[float]]:
        if frame_count == 0:
            rows = list(activations) if activations is not None else []
            if rows:
                raise ValueError("heatmap activation rows do not match frame count")
            return []
        rows: list[list[float]] = []
        for row in activations:
            clamped: list[float] = []
            for value in row:
                activation = float(value)
                if not math.isfinite(activation):
                    raise ValueError("heatmap activations must be finite")
                clamped.append(max(0.0, min(1.0, activation)))
            if len(clamped) != note_count:
                raise ValueError("heatmap frame activation count does not match midi_notes")
            rows.append(clamped)
        if len(rows) != frame_count:
            raise ValueError("heatmap activation rows do not match frame count")
        return rows

    @property
    def frame_count(self) -> int:
        return len(self.frame_times)

    @property
    def note_count(self) -> int:
        return len(self.midi_notes)

    @property
    def duration_seconds(self) -> float:
        if not self.frame_times:
            return 0.0
        if len(self.frame_times) == 1:
            return self.frame_times[0]
        return self.frame_times[-1] + (self.frame_times[-1] - self.frame_times[-2])

    def activation(self, frame_index: int, note_index: int) -> float:
        """Return a clamped activation for a frame/note cell."""

        if frame_index < 0 or frame_index >= self.frame_count or note_index < 0 or note_index >= self.note_count:
            return 0.0
        matrix = self.activation_matrix()
        if matrix is not None:
            return max(0.0, min(1.0, float(matrix[frame_index, note_index])))
        row = self.activations[frame_index]
        return max(0.0, min(1.0, float(row[note_index])))

    def activation_matrix(self) -> Any:
        """Return the primary numpy matrix, or None on the pure-Python path."""

        np = _load_numpy()
        if np is not None and _is_numpy_array(np, self.activations):
            return self.activations
        return None

    def with_time_offset(self, offset_seconds: float) -> "HeatmapData":
        """Return a heatmap with shifted frame times and shared activations."""

        offset = float(offset_seconds)
        return HeatmapData(
            backend=self.backend,
            midi_notes=list(self.midi_notes),
            frame_times=[time_seconds + offset for time_seconds in self.frame_times],
            activations=self.activations,
            sample_rate=self.sample_rate,
            hop_size=self.hop_size,
            window_size=self.window_size,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HeatmapData):
            return NotImplemented
        if (
            self.backend != other.backend
            or self.midi_notes != other.midi_notes
            or self.sample_rate != other.sample_rate
            or self.hop_size != other.hop_size
            or self.window_size != other.window_size
            or len(self.frame_times) != len(other.frame_times)
        ):
            return False
        if any(not math.isclose(a, b, rel_tol=0.0, abs_tol=_FLOAT_TOLERANCE) for a, b in zip(self.frame_times, other.frame_times)):
            return False

        left = self.activation_matrix()
        right = other.activation_matrix()
        np = _load_numpy()
        if np is not None and left is not None and right is not None:
            return bool(np.allclose(left, right, rtol=0.0, atol=_FLOAT_TOLERANCE))
        if self.frame_count != other.frame_count or self.note_count != other.note_count:
            return False
        for frame_index in range(self.frame_count):
            for note_index in range(self.note_count):
                if not math.isclose(
                    self.activation(frame_index, note_index),
                    other.activation(frame_index, note_index),
                    rel_tol=0.0,
                    abs_tol=_FLOAT_TOLERANCE,
                ):
                    return False
        return True


def _is_numpy_array(np: Any, value: Any) -> bool:
    return isinstance(value, np.ndarray)


def heatmap_from_document(document: dict[str, Any]) -> HeatmapData:
    """Convert the stable JSON-like heatmap document into compact data."""

    frames = document.get("frames", [])
    midi_notes = [int(note) for note in document.get("midi_notes", [])]
    frame_times: list[float] = []
    rows: list[list[float]] = []
    for frame in frames:
        frame_times.append(float(frame.get("time_seconds", 0.0)))
        row = [float(value) for value in frame.get("activations", [])]
        if len(row) != len(midi_notes):
            raise ValueError("heatmap frame activation count does not match midi_notes")
        rows.append(row)
    return HeatmapData(
        backend=str(document.get("backend", "unknown")),
        midi_notes=midi_notes,
        frame_times=frame_times,
        activations=rows,
        sample_rate=int(document.get("sample_rate", 0)),
        hop_size=int(document.get("hop_size", 0)),
        window_size=int(document.get("window_size", 0)),
    )


def heatmap_to_document(heatmap: HeatmapData) -> dict[str, Any]:
    """Materialize the stable JSON-like document.

    This is intentionally an explicit compatibility/export adapter.  Normal GUI
    and CLI paths should consume :class:`HeatmapData` directly or stream JSON with
    :func:`write_heatmap_json`.
    """

    return {
        "version": 1,
        "backend": heatmap.backend,
        "sample_rate": heatmap.sample_rate,
        "hop_size": heatmap.hop_size,
        "window_size": heatmap.window_size,
        "midi_notes": list(heatmap.midi_notes),
        "frames": list(_iter_frame_documents(heatmap)),
    }


def write_heatmap_json(path: Path, heatmap: HeatmapData | dict[str, Any]) -> None:
    """Write the stable heatmap JSON schema, streaming compact data row by row."""

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _replacement_mode(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        else:  # pragma: no cover - POSIX path is covered in tests
            os.chmod(tmp_path, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if isinstance(heatmap, HeatmapData):
                _write_heatmap_data_json(handle, heatmap)
            else:
                json.dump(heatmap, handle, separators=(",", ":"), allow_nan=False)
                handle.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        finally:
            raise


def _replacement_mode(path: Path) -> int:
    if path.exists():
        return path.stat().st_mode & 0o777
    current_umask = os.umask(0)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def _write_heatmap_data_json(handle: TextIO, heatmap: HeatmapData) -> None:
    handle.write("{")
    _write_json_field(handle, "version", 1)
    handle.write(",")
    _write_json_field(handle, "backend", heatmap.backend)
    handle.write(",")
    _write_json_field(handle, "sample_rate", heatmap.sample_rate)
    handle.write(",")
    _write_json_field(handle, "hop_size", heatmap.hop_size)
    handle.write(",")
    _write_json_field(handle, "window_size", heatmap.window_size)
    handle.write(",")
    _write_json_field(handle, "midi_notes", list(heatmap.midi_notes))
    handle.write(',"frames":[')
    for index, frame in enumerate(_iter_frame_documents(heatmap)):
        if index:
            handle.write(",")
        json.dump(frame, handle, separators=(",", ":"), allow_nan=False)
    handle.write("]}\n")


def _write_json_field(handle: TextIO, name: str, value: Any) -> None:
    handle.write(json.dumps(name))
    handle.write(":")
    json.dump(value, handle, separators=(",", ":"))


def _iter_frame_documents(heatmap: HeatmapData) -> Iterator[dict[str, Any]]:
    matrix = heatmap.activation_matrix()
    if matrix is not None:
        for time_seconds, row in zip(heatmap.frame_times, matrix):
            yield {"time_seconds": time_seconds, "activations": _document_activation_row(heatmap.backend, row)}
    else:
        for time_seconds, row in zip(heatmap.frame_times, heatmap.activations):
            yield {"time_seconds": time_seconds, "activations": _document_activation_row(heatmap.backend, row)}


def _document_activation_row(backend: str, row: Iterable[float]) -> list[float]:
    if backend in {"cqt", "simple"}:
        return [round(float(value), 6) for value in row]
    return [float(value) for value in row]


def notes_from_heatmap_data(
    heatmap: HeatmapData,
    *,
    threshold: float,
    min_duration_seconds: float,
    min_note_frames: int = 1,
) -> list[MidiNote]:
    """Extract note events from compact heatmap data."""

    matrix = heatmap.activation_matrix()
    if matrix is not None:
        return notes_from_matrix(
            matrix,
            midi_notes=heatmap.midi_notes,
            sample_rate=heatmap.sample_rate,
            hop_size=heatmap.hop_size,
            threshold=threshold,
            min_duration_seconds=min_duration_seconds,
            min_note_frames=min_note_frames,
        )
    return _notes_from_rows(
        heatmap.activations,
        midi_notes=heatmap.midi_notes,
        sample_rate=heatmap.sample_rate,
        hop_size=heatmap.hop_size,
        threshold=threshold,
        min_duration_seconds=min_duration_seconds,
        min_note_frames=min_note_frames,
    )


def notes_from_matrix(
    matrix: Any,
    *,
    midi_notes: list[int],
    sample_rate: int,
    hop_size: int,
    threshold: float,
    min_duration_seconds: float,
    min_note_frames: int = 1,
) -> list[MidiNote]:
    """Vectorized heatmap extraction with bounded float workspace.

    This matches the legacy document extractor: a cell is active when it is over
    threshold and a local peak across neighbouring pitches.  It allocates one
    boolean ``active`` matrix, then detects runs one note column at a time with
    O(frames) edge temporaries.  It deliberately avoids full-size left/right float
    matrices.
    """

    np = _load_numpy()
    if np is None:  # pragma: no cover - callers use _notes_from_rows instead
        raise RuntimeError("matrix extraction requires numpy")

    frame_count, note_count = matrix.shape
    if frame_count == 0 or note_count == 0:
        return []

    hop_seconds = hop_size / sample_rate
    seconds_per_tick = 1.0 / TICKS_PER_SECOND
    required_frames = max(min_note_frames, math.ceil(min_duration_seconds / hop_seconds))

    active = matrix >= threshold
    if note_count > 1:
        active[:, 1:] &= matrix[:, 1:] >= matrix[:, :-1]
        active[:, :-1] &= matrix[:, :-1] >= matrix[:, 1:]

    notes: list[MidiNote] = []
    padded = np.empty(frame_count + 2, dtype=np.bool_)
    padded[0] = False
    padded[-1] = False
    for note_index, pitch in enumerate(midi_notes):
        padded[1:-1] = active[:, note_index]
        edges = padded[1:].astype(np.int8) - padded[:-1].astype(np.int8)
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)
        for start_frame, end_frame in zip(starts.tolist(), ends.tolist()):
            if end_frame - start_frame < required_frames:
                continue
            peak = float(matrix[start_frame:end_frame, note_index].max())
            _append_heatmap_note(notes, pitch, start_frame, end_frame, hop_seconds, seconds_per_tick, peak)
    return sorted(notes, key=lambda note: (note.start_tick, note.pitch))


def _notes_from_rows(
    rows: Iterable[Iterable[float]],
    *,
    midi_notes: list[int],
    sample_rate: int,
    hop_size: int,
    threshold: float,
    min_duration_seconds: float,
    min_note_frames: int,
) -> list[MidiNote]:
    materialized = [list(row) for row in rows]
    if not materialized:
        return []
    hop_seconds = hop_size / sample_rate
    seconds_per_tick = 1.0 / TICKS_PER_SECOND
    required_frames = max(min_note_frames, math.ceil(min_duration_seconds / hop_seconds))
    notes: list[MidiNote] = []
    for note_index, pitch in enumerate(midi_notes):
        active_start: int | None = None
        active_peak = 0.0
        for frame_index, row in enumerate(materialized):
            activation = float(row[note_index])
            is_active = activation >= threshold and _is_local_peak(row, note_index)
            if is_active:
                if active_start is None:
                    active_start = frame_index
                    active_peak = activation
                else:
                    active_peak = max(active_peak, activation)
            elif active_start is not None:
                if frame_index - active_start >= required_frames:
                    _append_heatmap_note(notes, pitch, active_start, frame_index, hop_seconds, seconds_per_tick, active_peak)
                active_start = None
                active_peak = 0.0
        if active_start is not None and len(materialized) - active_start >= required_frames:
            _append_heatmap_note(notes, pitch, active_start, len(materialized), hop_seconds, seconds_per_tick, active_peak)
    return sorted(notes, key=lambda note: (note.start_tick, note.pitch))


def _append_heatmap_note(
    notes: list[MidiNote],
    pitch: int,
    start_frame: int,
    end_frame: int,
    hop_seconds: float,
    seconds_per_tick: float,
    peak_activation: float,
) -> None:
    start_tick = round(start_frame * hop_seconds / seconds_per_tick)
    duration_ticks = max(1, round((end_frame - start_frame) * hop_seconds / seconds_per_tick))
    velocity = max(1, min(127, round(peak_activation * 127)))
    notes.append(MidiNote(pitch=pitch, start_tick=start_tick, duration_ticks=duration_ticks, velocity=velocity))


def _is_local_peak(values: list[float], note_index: int) -> bool:
    activation = float(values[note_index])
    left = float(values[note_index - 1]) if note_index > 0 else -1.0
    right = float(values[note_index + 1]) if note_index + 1 < len(values) else -1.0
    return activation >= left and activation >= right
