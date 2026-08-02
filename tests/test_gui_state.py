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
from notegrabber.gui.analysis_worker import _clip_midi_notes_to_duration, _offset_midi_notes
from notegrabber.gui.overview import PitchOverview, downsample_overview_frames
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
def test_analysis_worker_offsets_range_notes_to_original_timeline() -> None:
    notes = [MidiNote(pitch=60, start_tick=TICKS_PER_SECOND, duration_ticks=TICKS_PER_SECOND // 2, velocity=80)]

    shifted_notes = _offset_midi_notes(notes, 10.0)

    assert shifted_notes[0].start_tick == 11 * TICKS_PER_SECOND


@pytest.mark.gui
def test_pitch_overview_downsamples_by_max_pooling() -> None:
    frame_times = [float(index) for index in range(6)]
    activations = [[index / 10.0, (5 - index) / 10.0] for index in range(6)]

    times, pooled = downsample_overview_frames(frame_times, activations, max_frames=3)
    overview = PitchOverview(frame_times=times, midi_notes=[60, 61], activations=pooled, duration_seconds=6.0)

    assert times == [0.0, 2.0, 4.0]
    assert pooled == [[0.1, 0.5], [0.3, 0.3], [0.5, 0.1]]
    assert overview.activation(0, 1) == pytest.approx(0.5)


@pytest.mark.gui
def test_clip_midi_notes_to_preview_duration() -> None:
    notes = [
        MidiNote(pitch=60, start_tick=0, duration_ticks=2 * TICKS_PER_SECOND, velocity=80),
        MidiNote(pitch=64, start_tick=TICKS_PER_SECOND, duration_ticks=2 * TICKS_PER_SECOND, velocity=90),
        MidiNote(pitch=67, start_tick=3 * TICKS_PER_SECOND, duration_ticks=TICKS_PER_SECOND, velocity=100),
    ]

    clipped = _clip_midi_notes_to_duration(notes, 2.0)

    assert clipped == [
        MidiNote(pitch=60, start_tick=0, duration_ticks=2 * TICKS_PER_SECOND, velocity=80),
        MidiNote(pitch=64, start_tick=TICKS_PER_SECOND, duration_ticks=TICKS_PER_SECOND, velocity=90),
    ]


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
    round_trip = heatmap_to_document(heatmap)
    assert round_trip["version"] == document["version"]
    assert round_trip["backend"] == document["backend"]
    assert round_trip["sample_rate"] == document["sample_rate"]
    assert round_trip["hop_size"] == document["hop_size"]
    assert round_trip["window_size"] == document["window_size"]
    assert round_trip["midi_notes"] == document["midi_notes"]
    assert [frame["time_seconds"] for frame in round_trip["frames"]] == pytest.approx(
        [frame["time_seconds"] for frame in document["frames"]]
    )
    for actual, expected in zip(round_trip["frames"], document["frames"]):
        assert actual["activations"] == pytest.approx(expected["activations"])


@pytest.mark.gui
def test_activation_matrix_matches_accessor_and_is_clamped() -> None:
    np = pytest.importorskip("numpy")

    heatmap = GuiHeatmap(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        # Include out-of-range values to confirm clamping to [0, 1].
        activations=[[-0.2, 1.5, 0.5], [0.0, 0.3, 0.9]],
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )

    matrix = heatmap.activation_matrix()
    assert matrix is not None
    assert matrix.shape == (2, 3)
    assert matrix.dtype == np.float32
    # Every cell equals the pure-Python clamped accessor.
    for frame in range(heatmap.frame_count):
        for note in range(heatmap.note_count):
            assert float(matrix[frame, note]) == pytest.approx(heatmap.activation(frame, note))
    # Clamping applied.
    assert float(matrix[0, 0]) == 0.0
    assert float(matrix[0, 1]) == 1.0
    # The matrix is primary storage: the accessor returns the same object stored
    # on the heatmap, not a second lazy cache.
    assert heatmap.activation_matrix() is matrix
    assert heatmap.activations is matrix
    assert matrix.flags.c_contiguous

    # Accessing the matrix must not affect equality.
    clamped = GuiHeatmap(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=[[0.0, 1.0, 0.5], [0.0, 0.3, 0.9]],
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )
    twin = GuiHeatmap(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=[[0.0, 1.0, 0.5], [0.0, 0.3, 0.9]],
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )
    clamped.activation_matrix()  # populate one side's cache only
    assert clamped == twin


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


def _random_heatmap(frames: int, notes: int, seed: int) -> GuiHeatmap:
    import random

    rng = random.Random(seed)
    activations = [[round(rng.random(), 4) for _ in range(notes)] for _ in range(frames)]
    return GuiHeatmap(
        backend="cqt",
        midi_notes=list(range(21, 21 + notes)),
        frame_times=[i * 0.05 for i in range(frames)],
        activations=activations,
        sample_rate=100,
        hop_size=5,
        window_size=1,
    )


@pytest.mark.gui
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("threshold,min_duration", [(0.3, 0.0), (0.5, 0.05), (0.7, 0.1)])
def test_vectorized_retune_matches_pure_python(seed, threshold, min_duration) -> None:
    """The numpy retune path must produce notes identical to the document path
    (issue #22: vectorize extraction, keep the pure-Python fallback)."""

    from notegrabber.analyzer import notes_from_heatmap
    from notegrabber.gui.state import midi_notes_to_gui

    heatmap = _random_heatmap(frames=120, notes=88, seed=seed)

    vectorized = retune_notes_from_heatmap(heatmap, threshold=threshold, min_duration_seconds=min_duration)
    document = heatmap_to_document(heatmap)
    pure = midi_notes_to_gui(
        notes_from_heatmap(document, threshold=threshold, min_duration_seconds=min_duration),
        source="cqt",
    )

    assert len(vectorized) == len(pure)
    for a, b in zip(vectorized, pure):
        assert (a.pitch, a.start_seconds, a.duration_seconds, a.velocity) == (
            b.pitch,
            b.start_seconds,
            b.duration_seconds,
            b.velocity,
        )


@pytest.mark.gui
def test_retune_falls_back_when_numpy_matrix_unavailable(monkeypatch) -> None:
    """When activation_matrix() returns None (no numpy), retune still works via
    the pure-Python document path and yields the same notes."""

    heatmap = _random_heatmap(frames=60, notes=88, seed=7)
    reference = retune_notes_from_heatmap(heatmap, threshold=0.5, min_duration_seconds=0.05)

    monkeypatch.setattr(type(heatmap), "activation_matrix", lambda self: None)
    fallback = retune_notes_from_heatmap(heatmap, threshold=0.5, min_duration_seconds=0.05)

    assert len(fallback) == len(reference)
    for a, b in zip(fallback, reference):
        assert (a.pitch, a.start_seconds, a.duration_seconds, a.velocity) == (
            b.pitch,
            b.start_seconds,
            b.duration_seconds,
            b.velocity,
        )
