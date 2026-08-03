"""GUI-facing state and conversion helpers.

The native GUI uses small explicit models instead of passing raw heatmap JSON or
MIDI ticks around widgets.  This module deliberately has no Qt dependency so it
can be tested in headless environments and reused by future non-Qt frontends.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from notegrabber.analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    CQT_THRESHOLD,
    BackendName,
)
from notegrabber.heatmap import (
    HeatmapData,
    heatmap_from_document as heatmap_from_document,
    heatmap_to_document as heatmap_to_document,
    notes_from_heatmap_data,
)
from notegrabber.midi import (
    MidiNote,
    PITCH_BEND_UNITS_PER_SEMITONE as _BEND_UNITS_PER_SEMITONE,
    TICKS_PER_SECOND,
)


__all__ = [
    "GuiHeatmap",
    "GuiMidiNote",
    "ProjectState",
    "add_gui_note",
    "delete_gui_note",
    "gui_note_to_midi",
    "gui_notes_to_midi",
    "heatmap_from_document",
    "heatmap_to_document",
    "midi_note_to_gui",
    "midi_notes_to_gui",
    "normalized_gui_note",
    "retune_notes_from_heatmap",
    "update_gui_note",
]


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


GuiHeatmap = HeatmapData


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


def add_gui_note(notes: list[GuiMidiNote], note: GuiMidiNote) -> tuple[list[GuiMidiNote], int]:
    """Return a copy of notes with ``note`` inserted, plus its insertion index.

    The note is normalized first, then inserted in start-time order so the
    sequence table stays tidy (the piano roll hit-tests every note and does not
    require sorted order).  The index is returned because the caller selects the
    new note, and inserting in order means it is not simply ``len(notes)``.

    Uses a linear scan rather than a bisect: extraction yields sorted notes, but
    drag-edits move start times without re-sorting, so the list is not reliably
    ordered at runtime.  This lands the note after every earlier-starting note,
    which degrades to "append" on an unsorted list instead of misplacing it.
    """

    note = normalized_gui_note(note)
    index = len(notes)
    for position, existing in enumerate(notes):
        if existing.start_seconds > note.start_seconds:
            index = position
            break
    updated = list(notes)
    updated.insert(index, note)
    return updated, index


def update_gui_note(notes: list[GuiMidiNote], index: int, **changes: object) -> list[GuiMidiNote]:
    """Return a copy of notes with a dataclass-replaced indexed note."""

    if index < 0 or index >= len(notes):
        return list(notes)
    updated = list(notes)
    updated[index] = normalized_gui_note(replace(updated[index], **changes))
    return updated


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

    notes = notes_from_heatmap_data(
        heatmap,
        threshold=threshold,
        min_duration_seconds=min_duration_seconds,
        min_note_frames=1,
    )
    return midi_notes_to_gui(notes, source=heatmap.backend)
