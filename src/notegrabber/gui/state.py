"""GUI-facing state and conversion helpers.

The native GUI uses small explicit models instead of passing raw heatmap JSON or
MIDI ticks around widgets.  This module deliberately has no Qt dependency so it
can be tested in headless environments and reused by future non-Qt frontends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from notegrabber.analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    CQT_THRESHOLD,
    MIN_NOTE_FRAMES,
    BackendName,
    notes_from_heatmap,
)
from notegrabber.midi import (
    MidiNote,
    PITCH_BEND_UNITS_PER_SEMITONE as _BEND_UNITS_PER_SEMITONE,
    TICKS_PER_SECOND,
)


@dataclass(frozen=True)
class GuiMidiNote:
    """A MIDI note represented in real seconds for UI layout/editing."""

    pitch: int
    start_seconds: float
    duration_seconds: float
    velocity: int
    source: str = "basic-pitch"
    # Optional pitch-bend contour: evenly-spaced (time_in_note_seconds,
    # semitone_offset) points across the note. ``None`` means no bend. Used to
    # draw the bend curve in the piano roll; carried through edits unchanged.
    pitch_bends: tuple[tuple[float, float], ...] | None = None

    @property
    def end_seconds(self) -> float:
        """Return the note end time in seconds."""

        return self.start_seconds + self.duration_seconds


@dataclass(frozen=True)
class GuiHeatmap:
    """Pitch salience heatmap ready for GUI drawing.

    ``activations`` (a ``list[list[float]]``) stays the source of truth so this
    model has no hard numpy dependency and JSON export/round-trip is unchanged.
    For the hot paint path, :meth:`activation_matrix` lazily builds a cached
    numpy ``float32`` array of shape ``(frame_count, note_count)`` so the piano
    roll can reduce whole columns/rows in C instead of per-cell Python calls.
    """

    backend: str
    midi_notes: list[int]
    frame_times: list[float]
    activations: list[list[float]]
    sample_rate: int
    hop_size: int
    window_size: int
    # Lazily-populated numpy cache; excluded from equality/repr/hash. Sentinel
    # ``False`` means "not computed yet"; ``None`` means "numpy unavailable".
    _activation_matrix: Any = field(default=False, compare=False, repr=False, hash=False)

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

        if frame_index < 0 or frame_index >= len(self.activations):
            return 0.0
        row = self.activations[frame_index]
        if note_index < 0 or note_index >= len(row):
            return 0.0
        return max(0.0, min(1.0, float(row[note_index])))

    def activation_matrix(self) -> Any:
        """Return a cached numpy (frame, note) float32 matrix, or None.

        Returns ``None`` when numpy is not installed so callers fall back to the
        pure-Python :meth:`activation` accessor.  The matrix is clamped to
        ``[0, 1]`` to match :meth:`activation`.
        """

        if self._activation_matrix is not False:
            return self._activation_matrix
        try:
            import numpy as np  # type: ignore[import-not-found]
        except Exception:
            object.__setattr__(self, "_activation_matrix", None)
            return None
        if not self.activations:
            matrix = np.zeros((0, self.note_count), dtype=np.float32)
        else:
            matrix = np.asarray(self.activations, dtype=np.float32)
            np.clip(matrix, 0.0, 1.0, out=matrix)
        object.__setattr__(self, "_activation_matrix", matrix)
        return matrix


MIN_GUI_NOTE_DURATION_SECONDS = 0.001


@dataclass
class ProjectState:
    """Mutable application state shared by the standalone GUI widgets."""

    audio_path: Path | None = None
    rendered_midi_wav: Path | None = None
    heatmap: GuiHeatmap | None = None
    extracted_notes: list[GuiMidiNote] = field(default_factory=list)
    tuned_notes: list[GuiMidiNote] | None = None
    backend: BackendName = "basic-pitch"
    threshold: float = CQT_THRESHOLD
    onset_threshold: float = BASIC_PITCH_ONSET_THRESHOLD
    frame_threshold: float = BASIC_PITCH_FRAME_THRESHOLD
    min_duration: float = BASIC_PITCH_MIN_DURATION_SECONDS
    analysis_start_seconds: float = 0.0
    analysis_duration_seconds: float | None = None
    midi_preview_offset_seconds: float = 0.0

    @property
    def current_notes(self) -> list[GuiMidiNote]:
        """Return tuned notes when available, otherwise extracted notes."""

        return self.tuned_notes if self.tuned_notes is not None else self.extracted_notes


def heatmap_from_document(document: dict[str, Any]) -> GuiHeatmap:
    """Convert the common heatmap JSON document into a GUI model."""

    frames = document.get("frames", [])
    midi_notes = [int(note) for note in document.get("midi_notes", [])]
    frame_times: list[float] = []
    activations: list[list[float]] = []

    for frame in frames:
        frame_times.append(float(frame.get("time_seconds", 0.0)))
        row = [max(0.0, min(1.0, float(value))) for value in frame.get("activations", [])]
        if len(row) != len(midi_notes):
            raise ValueError("heatmap frame activation count does not match midi_notes")
        activations.append(row)

    return GuiHeatmap(
        backend=str(document.get("backend", "unknown")),
        midi_notes=midi_notes,
        frame_times=frame_times,
        activations=activations,
        sample_rate=int(document.get("sample_rate", 0)),
        hop_size=int(document.get("hop_size", 0)),
        window_size=int(document.get("window_size", 0)),
    )


def midi_note_to_gui(note: MidiNote, source: str = "basic-pitch") -> GuiMidiNote:
    """Convert an internal MIDI note into a GUI note."""

    duration_seconds = note.duration_ticks / TICKS_PER_SECOND
    bends: tuple[tuple[float, float], ...] | None = None
    if note.pitch_bends:
        # Convert Basic Pitch's 1/3-semitone units to semitone offsets, spaced
        # evenly across the note's duration (matching how write_midi lays them out).
        count = len(note.pitch_bends)
        bends = tuple(
            (
                (index / count) * duration_seconds if count > 1 else 0.0,
                units / _BEND_UNITS_PER_SEMITONE,
            )
            for index, units in enumerate(note.pitch_bends)
        )

    return GuiMidiNote(
        pitch=int(note.pitch),
        start_seconds=note.start_tick / TICKS_PER_SECOND,
        duration_seconds=duration_seconds,
        velocity=int(note.velocity),
        source=source,
        pitch_bends=bends,
    )


def midi_notes_to_gui(notes: list[MidiNote], source: str = "basic-pitch") -> list[GuiMidiNote]:
    """Convert a list of internal MIDI notes into GUI notes."""

    return [midi_note_to_gui(note, source=source) for note in notes]


def normalized_gui_note(note: GuiMidiNote) -> GuiMidiNote:
    """Return a GUI note clamped to valid MIDI/editing ranges."""

    return replace(
        note,
        pitch=max(0, min(127, int(note.pitch))),
        start_seconds=max(0.0, float(note.start_seconds)),
        duration_seconds=max(MIN_GUI_NOTE_DURATION_SECONDS, float(note.duration_seconds)),
        velocity=max(1, min(127, int(note.velocity))),
    )


def gui_note_to_midi(note: GuiMidiNote) -> MidiNote:
    """Convert a GUI note back to the internal MIDI writer representation."""

    note = normalized_gui_note(note)
    return MidiNote(
        pitch=int(note.pitch),
        start_tick=max(0, round(note.start_seconds * TICKS_PER_SECOND)),
        duration_ticks=max(1, round(note.duration_seconds * TICKS_PER_SECOND)),
        velocity=max(1, min(127, int(note.velocity))),
    )


def gui_notes_to_midi(notes: list[GuiMidiNote]) -> list[MidiNote]:
    """Convert GUI notes back to internal MIDI notes for export."""

    return [gui_note_to_midi(note) for note in notes]


def delete_gui_note(notes: list[GuiMidiNote], index: int) -> list[GuiMidiNote]:
    """Return a copy of notes with the indexed note removed."""

    if index < 0 or index >= len(notes):
        return list(notes)
    return [note for note_index, note in enumerate(notes) if note_index != index]


def update_gui_note(notes: list[GuiMidiNote], index: int, **changes: object) -> list[GuiMidiNote]:
    """Return a copy of notes with a dataclass-replaced indexed note."""

    if index < 0 or index >= len(notes):
        return list(notes)
    updated = list(notes)
    updated[index] = normalized_gui_note(replace(updated[index], **changes))
    return updated


def heatmap_to_document(heatmap: GuiHeatmap) -> dict[str, Any]:
    """Convert a GUI heatmap model back to the common JSON-like document."""

    return {
        "version": 1,
        "backend": heatmap.backend,
        "sample_rate": heatmap.sample_rate,
        "hop_size": heatmap.hop_size,
        "window_size": heatmap.window_size,
        "midi_notes": list(heatmap.midi_notes),
        "frames": [
            {"time_seconds": time_seconds, "activations": list(activations)}
            for time_seconds, activations in zip(heatmap.frame_times, heatmap.activations)
        ],
    }


def retune_notes_from_heatmap(
    heatmap: GuiHeatmap,
    *,
    threshold: float,
    min_duration_seconds: float,
) -> list[GuiMidiNote]:
    """Extract tuned notes from a GUI heatmap without rerunning analysis.

    Runs on the GUI thread on every retune-knob commit, so it uses a vectorized
    numpy pass over the cached activation matrix (~1.3s -> ~0.07s at 3-min
    scale). Falls back to the pure-Python document path when numpy is missing.
    """

    matrix = heatmap.activation_matrix()
    if matrix is not None:
        notes = _notes_from_matrix(
            matrix,
            midi_notes=heatmap.midi_notes,
            sample_rate=heatmap.sample_rate,
            hop_size=heatmap.hop_size,
            threshold=threshold,
            min_duration_seconds=min_duration_seconds,
        )
    else:  # pragma: no cover - exercised only without numpy
        document = heatmap_to_document(heatmap)
        notes = notes_from_heatmap(document, threshold=threshold, min_duration_seconds=min_duration_seconds)
    return midi_notes_to_gui(notes, source=heatmap.backend)


def _notes_from_matrix(
    matrix: Any,
    *,
    midi_notes: list[int],
    sample_rate: int,
    hop_size: int,
    threshold: float,
    min_duration_seconds: float,
) -> list[MidiNote]:
    """Vectorized equivalent of ``notes_from_heatmap`` over a (frame, note) matrix.

    Matches the pure-Python extraction exactly: a frame is active for a note when
    its activation is >= ``threshold`` and a local peak across the note dimension
    (>= its lower and upper pitch neighbours, out-of-range neighbours treated as
    -1). Contiguous active runs per note become notes; runs shorter than
    ``min_note_frames`` are dropped; velocity comes from the run's peak.
    """

    import numpy as np

    frame_count, note_count = matrix.shape
    if frame_count == 0 or note_count == 0:
        return []

    hop_seconds = hop_size / sample_rate
    seconds_per_tick = 1.0 / TICKS_PER_SECOND
    min_note_frames = max(MIN_NOTE_FRAMES, math.ceil(min_duration_seconds / hop_seconds))

    # Local peak across the note (column) dimension, out-of-range neighbours = -1.
    left = np.full_like(matrix, -1.0)
    left[:, 1:] = matrix[:, :-1]
    right = np.full_like(matrix, -1.0)
    right[:, :-1] = matrix[:, 1:]
    active = (matrix >= threshold) & (matrix >= left) & (matrix >= right)

    notes: list[MidiNote] = []
    # Pad each note column with a False frame at both ends so diff catches runs
    # that touch frame 0 or the last frame; +1 = onset, -1 = offset.
    padded = np.zeros((frame_count + 2, note_count), dtype=bool)
    padded[1:-1] = active
    edges = padded[1:].astype(np.int8) - padded[:-1].astype(np.int8)

    for note_index, pitch in enumerate(midi_notes):
        column_edges = edges[:, note_index]
        starts = np.flatnonzero(column_edges == 1)
        ends = np.flatnonzero(column_edges == -1)
        for start_frame, end_frame in zip(starts.tolist(), ends.tolist()):
            if end_frame - start_frame < min_note_frames:
                continue
            peak = float(matrix[start_frame:end_frame, note_index].max())
            start_tick = round(start_frame * hop_seconds / seconds_per_tick)
            duration_ticks = max(1, round((end_frame - start_frame) * hop_seconds / seconds_per_tick))
            velocity = max(1, min(127, round(peak * 127)))
            notes.append(MidiNote(pitch=pitch, start_tick=start_tick, duration_ticks=duration_ticks, velocity=velocity))

    return sorted(notes, key=lambda note: (note.start_tick, note.pitch))
