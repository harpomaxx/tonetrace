"""Headless tests for standalone GUI model conversion helpers."""

from __future__ import annotations

import pytest

from notegrabber.gui.state import (
    GuiHeatmap,
    GuiMidiNote,
    ProjectState,
    delete_gui_note,
    gui_note_to_midi,
    update_gui_note,
    heatmap_from_document,
    heatmap_to_document,
    midi_note_to_gui,
    retune_notes_from_heatmap,
)
from notegrabber.gui.analysis_worker import _offset_heatmap_document, _offset_midi_notes
from notegrabber.midi import MidiNote, TICKS_PER_SECOND


@pytest.mark.gui
def test_gui_note_round_trip_preserves_timing_and_velocity() -> None:
    midi = MidiNote(pitch=69, start_tick=TICKS_PER_SECOND, duration_ticks=TICKS_PER_SECOND // 2, velocity=91)

    gui = midi_note_to_gui(midi, source="test")
    round_trip = gui_note_to_midi(gui)

    assert gui == GuiMidiNote(pitch=69, start_seconds=1.0, duration_seconds=0.5, velocity=91, source="test")
    assert round_trip == midi


@pytest.mark.gui
def test_project_state_current_notes_preserves_empty_tuned_list() -> None:
    state = ProjectState(extracted_notes=[GuiMidiNote(pitch=69, start_seconds=0.0, duration_seconds=1.0, velocity=90)])

    assert [note.pitch for note in state.current_notes] == [69]
    state.tuned_notes = []
    assert state.current_notes == []


@pytest.mark.gui
def test_analysis_worker_offsets_range_results_to_original_timeline() -> None:
    notes = [MidiNote(pitch=60, start_tick=TICKS_PER_SECOND, duration_ticks=TICKS_PER_SECOND // 2, velocity=80)]
    document = {"frames": [{"time_seconds": 0.0, "activations": [0.5]}, {"time_seconds": 0.1, "activations": [0.2]}]}

    shifted_notes = _offset_midi_notes(notes, 10.0)
    _offset_heatmap_document(document, 10.0)

    assert shifted_notes[0].start_tick == 11 * TICKS_PER_SECOND
    assert [frame["time_seconds"] for frame in document["frames"]] == [10.0, 10.1]


@pytest.mark.gui
def test_update_gui_note_returns_validated_edited_copy() -> None:
    notes = [GuiMidiNote(pitch=60, start_seconds=0.5, duration_seconds=0.5, velocity=80)]

    edited = update_gui_note(notes, 0, pitch=140, start_seconds=-1.0, duration_seconds=0.0, velocity=999)

    assert edited[0] == GuiMidiNote(pitch=127, start_seconds=0.0, duration_seconds=0.001, velocity=127)
    assert notes[0] == GuiMidiNote(pitch=60, start_seconds=0.5, duration_seconds=0.5, velocity=80)


@pytest.mark.gui
def test_delete_gui_note_returns_edited_copy() -> None:
    notes = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=80),
        GuiMidiNote(pitch=64, start_seconds=0.0, duration_seconds=0.5, velocity=80),
    ]

    edited = delete_gui_note(notes, 0)

    assert [note.pitch for note in edited] == [64]
    assert [note.pitch for note in notes] == [60, 64]


@pytest.mark.gui
def test_heatmap_document_round_trip_validates_dimensions() -> None:
    document = {
        "version": 1,
        "backend": "cqt",
        "sample_rate": 100,
        "hop_size": 1,
        "window_size": 1,
        "midi_notes": [68, 69, 70],
        "frames": [
            {"time_seconds": 0.0, "activations": [0.0, 1.0, 0.1]},
            {"time_seconds": 0.1, "activations": [0.0, 0.9, 0.0]},
        ],
    }

    heatmap = heatmap_from_document(document)

    assert heatmap == GuiHeatmap(
        backend="cqt",
        midi_notes=[68, 69, 70],
        frame_times=[0.0, 0.1],
        activations=[[0.0, 1.0, 0.1], [0.0, 0.9, 0.0]],
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )
    assert heatmap_to_document(heatmap) == document


@pytest.mark.gui
def test_heatmap_document_rejects_bad_activation_width() -> None:
    with pytest.raises(ValueError, match="activation count"):
        heatmap_from_document(
            {
                "backend": "cqt",
                "sample_rate": 100,
                "hop_size": 1,
                "window_size": 1,
                "midi_notes": [69],
                "frames": [{"time_seconds": 0.0, "activations": [0.5, 0.4]}],
            }
        )


@pytest.mark.gui
def test_retune_notes_from_heatmap_extracts_gui_notes() -> None:
    heatmap = heatmap_from_document(
        {
            "version": 1,
            "backend": "cqt",
            "sample_rate": 100,
            "hop_size": 1,
            "window_size": 1,
            "midi_notes": [68, 69, 70],
            "frames": [
                {"time_seconds": 0.0, "activations": [0.1, 0.9, 0.2]},
                {"time_seconds": 0.1, "activations": [0.0, 0.8, 0.1]},
                {"time_seconds": 0.2, "activations": [0.0, 0.1, 0.0]},
            ],
        }
    )

    notes = retune_notes_from_heatmap(heatmap, threshold=0.5, min_duration_seconds=0.0)

    assert len(notes) == 1
    assert notes[0].pitch == 69
    assert notes[0].source == "cqt"
    assert notes[0].start_seconds == pytest.approx(0.0)
    assert notes[0].duration_seconds == pytest.approx(0.02, abs=0.001)
