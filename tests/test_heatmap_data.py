"""Compact heatmap storage contract tests (issue #27)."""

from __future__ import annotations

import json
import os
import random
import re
import stat
from pathlib import Path

import pytest

from notegrabber.heatmap import HeatmapData, heatmap_from_document, heatmap_to_document, notes_from_heatmap_data, write_heatmap_json
from notegrabber.midi import MidiNote


@pytest.mark.heatmap
def test_heatmap_data_uses_primary_float32_c_contiguous_matrix_and_reuses_compliant_input() -> None:
    np = pytest.importorskip("numpy")

    matrix = np.array([[0.0, 0.5, 1.0], [0.25, 0.75, 0.1]], dtype=np.float32)
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=matrix,
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )

    assert heatmap.activations is matrix
    assert heatmap.activation_matrix() is matrix
    assert matrix.dtype == np.float32
    assert matrix.flags.c_contiguous
    assert heatmap.activation(1, 1) == pytest.approx(0.75)


@pytest.mark.heatmap
def test_heatmap_data_converts_float64_and_non_contiguous_inputs() -> None:
    np = pytest.importorskip("numpy")

    float64 = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float64)
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=float64,
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )
    matrix = heatmap.activation_matrix()
    assert matrix is not None
    assert matrix.dtype == np.float32
    assert matrix.flags.c_contiguous
    assert matrix is not float64

    source = np.asfortranarray(np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dtype=np.float32))
    assert not source.flags.c_contiguous
    converted = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=source,
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )
    converted_matrix = converted.activation_matrix()
    assert converted_matrix is not source
    assert converted_matrix.dtype == np.float32
    assert converted_matrix.flags.c_contiguous


@pytest.mark.heatmap
def test_heatmap_data_accepts_zero_frame_matrix_with_note_width() -> None:
    np = pytest.importorskip("numpy")

    matrix = np.zeros((0, 3), dtype=np.float32)
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[],
        activations=matrix,
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )

    assert heatmap.frame_count == 0
    assert heatmap.note_count == 3
    assert heatmap.activation_matrix() is matrix

    from_rows = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[],
        activations=[],
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )
    assert from_rows.activation_matrix().shape == (0, 3)

    from_document = heatmap_from_document(
        {
            "version": 1,
            "backend": "cqt",
            "sample_rate": 100,
            "hop_size": 10,
            "window_size": 20,
            "midi_notes": [60, 61, 62],
            "frames": [],
        }
    )
    assert from_document.activation_matrix().shape == (0, 3)

    empty_pitch_axis = HeatmapData(
        backend="cqt",
        midi_notes=[],
        frame_times=[0.0, 0.1],
        activations=np.zeros((2, 0), dtype=np.float32),
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )
    assert empty_pitch_axis.activation_matrix().shape == (2, 0)


@pytest.mark.heatmap
def test_heatmap_data_clips_and_validates_matrix_shape() -> None:
    np = pytest.importorskip("numpy")

    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=[[-1.0, 0.5, 2.0], [0.25, 0.75, 0.1]],
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )
    matrix = heatmap.activation_matrix()
    assert matrix is not None
    assert matrix.dtype == np.float32
    assert matrix.shape == (2, 3)
    assert matrix.flags.c_contiguous
    assert float(matrix[0, 0]) == 0.0
    assert float(matrix[0, 2]) == 1.0

    with pytest.raises(ValueError, match="shape|frame/note"):
        HeatmapData(
            backend="cqt",
            midi_notes=[60, 61, 62],
            frame_times=[0.0, 0.1],
            activations=np.zeros((2, 2), dtype=np.float32),
            sample_rate=100,
            hop_size=10,
            window_size=20,
        )


@pytest.mark.heatmap
def test_heatmap_data_rejects_non_finite_values() -> None:
    np = pytest.importorskip("numpy")

    with pytest.raises(ValueError, match="finite"):
        HeatmapData(
            backend="cqt",
            midi_notes=[60],
            frame_times=[0.0],
            activations=np.array([[np.nan]], dtype=np.float32),
            sample_rate=100,
            hop_size=10,
            window_size=20,
        )
    with pytest.raises(ValueError, match="finite"):
        HeatmapData(
            backend="simple",
            midi_notes=[60],
            frame_times=[0.0],
            activations=[[float("inf")]],
            sample_rate=100,
            hop_size=10,
            window_size=20,
        )
    with pytest.raises(ValueError, match="frame times.*finite"):
        HeatmapData(
            backend="cqt",
            midi_notes=[60],
            frame_times=[float("nan")],
            activations=[[0.5]],
            sample_rate=100,
            hop_size=10,
            window_size=20,
        )


@pytest.mark.heatmap
def test_heatmap_time_offset_preserves_activation_matrix_identity() -> None:
    np = pytest.importorskip("numpy")

    matrix = np.zeros((2, 3), dtype=np.float32)
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=matrix,
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )

    shifted = heatmap.with_time_offset(12.5)

    assert shifted.frame_times == [12.5, 12.6]
    assert shifted.activations is matrix
    assert shifted.activation_matrix() is matrix


@pytest.mark.heatmap
def test_heatmap_custom_equality_handles_numpy_arrays_and_document_round_trip() -> None:
    np = pytest.importorskip("numpy")

    matrix = np.array([[0.0, 0.5, 1.0], [0.25, 0.75, 0.1]], dtype=np.float32)
    first = HeatmapData(
        backend="basic-pitch",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=matrix,
        sample_rate=86,
        hop_size=1,
        window_size=1,
    )
    second = HeatmapData(
        backend="basic-pitch",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=matrix.copy(),
        sample_rate=86,
        hop_size=1,
        window_size=1,
    )

    assert first == second
    document = heatmap_to_document(first)
    assert document["version"] == 1
    assert document["backend"] == "basic-pitch"
    assert document["midi_notes"] == [60, 61, 62]
    assert document["frames"][0]["time_seconds"] == pytest.approx(0.0)
    assert document["frames"][0]["activations"] == pytest.approx([0.0, 0.5, 1.0])
    assert heatmap_from_document(document) == first


@pytest.mark.heatmap
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("threshold,min_duration", [(0.3, 0.0), (0.5, 0.05), (0.7, 0.1)])
def test_matrix_extraction_matches_legacy_document_extractor(seed: int, threshold: float, min_duration: float) -> None:
    pytest.importorskip("numpy")
    from notegrabber.analyzer import notes_from_heatmap

    rng = random.Random(seed)
    frame_count = 80
    note_count = 16
    rows = [[round(rng.random(), 4) for _ in range(note_count)] for _ in range(frame_count)]
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=list(range(48, 48 + note_count)),
        frame_times=[i * 0.05 for i in range(frame_count)],
        activations=rows,
        sample_rate=100,
        hop_size=5,
        window_size=1,
    )

    matrix_notes = notes_from_heatmap_data(
        heatmap,
        threshold=threshold,
        min_duration_seconds=min_duration,
        min_note_frames=1,
    )
    legacy_notes = notes_from_heatmap(
        heatmap_to_document(heatmap),
        threshold=threshold,
        min_duration_seconds=min_duration,
    )

    assert matrix_notes == legacy_notes


@pytest.mark.heatmap
@pytest.mark.parametrize("value", [0.35, 0.7])
def test_matrix_extraction_keeps_inclusive_decimal_threshold_parity(monkeypatch, value: float) -> None:
    np = pytest.importorskip("numpy")
    from notegrabber.analyzer import notes_from_heatmap
    import notegrabber.heatmap as heatmap_module

    rows = [[0.0, value, 0.0]]
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0],
        activations=np.asarray(rows, dtype=np.float32),
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )

    matrix_notes = notes_from_heatmap_data(heatmap, threshold=value, min_duration_seconds=0.0, min_note_frames=1)
    legacy_notes = notes_from_heatmap(heatmap_to_document(heatmap), threshold=value, min_duration_seconds=0.0)

    monkeypatch.setattr(heatmap_module, "_load_numpy", lambda: None)
    fallback = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0],
        activations=rows,
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    fallback_notes = notes_from_heatmap_data(fallback, threshold=value, min_duration_seconds=0.0, min_note_frames=1)

    assert matrix_notes == legacy_notes == fallback_notes == [MidiNote(pitch=61, start_tick=0, duration_ticks=96, velocity=round(value * 127))]


@pytest.mark.heatmap
def test_matrix_extraction_handles_runs_at_first_and_last_frame() -> None:
    notes = [60, 61, 62]
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=notes,
        frame_times=[0.0, 0.1, 0.2, 0.3],
        activations=[
            [0.9, 0.1, 0.1],
            [0.8, 0.1, 0.1],
            [0.1, 0.1, 0.7],
            [0.1, 0.1, 0.8],
        ],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )

    extracted = notes_from_heatmap_data(heatmap, threshold=0.5, min_duration_seconds=0.0, min_note_frames=1)

    assert extracted == [
        MidiNote(pitch=60, start_tick=0, duration_ticks=192, velocity=114),
        MidiNote(pitch=62, start_tick=192, duration_ticks=192, velocity=102),
    ]


@pytest.mark.heatmap
@pytest.mark.parametrize("backend", ["cqt", "simple"])
def test_cqt_and_simple_document_and_streamed_json_keep_six_decimal_activations(tmp_path: Path, backend: str) -> None:
    np = pytest.importorskip("numpy")
    heatmap = HeatmapData(
        backend=backend,
        midi_notes=[60, 61],
        frame_times=[0.0],
        activations=np.array([[0.123456789, 0.7]], dtype=np.float32),
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )

    document = heatmap_to_document(heatmap)
    assert document["frames"][0]["activations"] == [0.123457, 0.7]

    output = tmp_path / f"{backend}.heatmap.json"
    write_heatmap_json(output, heatmap)
    text = output.read_text(encoding="utf-8")
    assert "0.123456" not in text
    numbers = re.findall(r'"activations":\[([^\]]+)\]', text)[0].split(",")
    assert all(len(number.partition(".")[2]) <= 6 for number in numbers if "." in number)
    assert json.loads(text)["frames"][0]["activations"] == [0.123457, 0.7]


@pytest.mark.heatmap
def test_basic_pitch_document_keeps_float32_precision_behavior(tmp_path: Path) -> None:
    np = pytest.importorskip("numpy")
    heatmap = HeatmapData(
        backend="basic-pitch",
        midi_notes=[60],
        frame_times=[0.0],
        activations=np.array([[0.123456789]], dtype=np.float32),
        sample_rate=86,
        hop_size=1,
        window_size=1,
    )

    document = heatmap_to_document(heatmap)
    assert document["frames"][0]["activations"][0] == float(np.float32(0.123456789))
    output = tmp_path / "basic-pitch.heatmap.json"
    write_heatmap_json(output, heatmap)
    assert json.loads(output.read_text(encoding="utf-8"))["frames"][0]["activations"] == document["frames"][0]["activations"]


@pytest.mark.heatmap
def test_streaming_heatmap_json_preserves_destination_permissions(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits only")
    np = pytest.importorskip("numpy")
    heatmap = HeatmapData(
        backend="cqt",
        midi_notes=[60],
        frame_times=[0.0],
        activations=np.array([[0.5]], dtype=np.float32),
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )

    current_umask = os.umask(0)
    os.umask(current_umask)
    expected_new_mode = 0o666 & ~current_umask

    new_output = tmp_path / "new.heatmap.json"
    write_heatmap_json(new_output, heatmap)
    assert stat.S_IMODE(new_output.stat().st_mode) == expected_new_mode

    existing = tmp_path / "existing.heatmap.json"
    existing.write_text("old", encoding="utf-8")
    existing.chmod(0o640)
    write_heatmap_json(existing, heatmap)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640


@pytest.mark.heatmap
def test_heatmap_data_falls_back_to_validated_rows_when_numpy_unavailable(monkeypatch) -> None:
    import notegrabber.heatmap as heatmap_module

    monkeypatch.setattr(heatmap_module, "_load_numpy", lambda: None)
    heatmap = HeatmapData(
        backend="simple",
        midi_notes=[60, 61],
        frame_times=[0.0, 0.1],
        activations=[[-1.0, 0.5], [0.25, 2.0]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )

    assert heatmap.activation_matrix() is None
    assert heatmap.activations == [[0.0, 0.5], [0.25, 1.0]]
    assert heatmap.activation(1, 1) == 1.0
    notes = notes_from_heatmap_data(heatmap, threshold=0.5, min_duration_seconds=0.0, min_note_frames=1)
    assert [note.pitch for note in notes] == [61]


@pytest.mark.heatmap
def test_simple_backend_heatmap_data_stays_dependency_free_when_numpy_unavailable(monkeypatch) -> None:
    import notegrabber.heatmap as heatmap_module
    from notegrabber.analyzer import AudioData, build_simple_heatmap_data

    monkeypatch.setattr(heatmap_module, "_load_numpy", lambda: None)

    heatmap = build_simple_heatmap_data(AudioData(samples=[0.0] * 1024, sample_rate=8000))

    assert heatmap.backend == "simple"
    assert heatmap.activation_matrix() is None
    assert isinstance(heatmap.activations, list)
    assert heatmap.note_count == 88
