"""CQT/librosa backend contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("librosa")

from tests.helpers import (  # noqa: E402  (import after optional dependency check)
    assert_successful_analysis,
    read_note_intervals_seconds,
    read_note_pitches,
    run_notegrabber_analyze,
    write_single_note_wav,
)

EXPECTED_MIDI_NOTES = list(range(21, 109))


def load_heatmap(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def global_peak_note(heatmap: dict[str, Any]) -> int:
    midi_notes = heatmap["midi_notes"]
    best_note = None
    best_activation = -1.0
    for frame in heatmap["frames"]:
        for index, activation in enumerate(frame["activations"]):
            if activation > best_activation:
                best_activation = activation
                best_note = midi_notes[index]
    assert best_note is not None
    return int(best_note)


@pytest.mark.cli
@pytest.mark.heatmap
@pytest.mark.cqt
def test_cqt_backend_writes_heatmap_with_music_aligned_schema(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.cqt.mid"
    output_heatmap = tmp_path / "a4.cqt.heatmap.json"

    result = run_notegrabber_analyze(
        input_wav,
        output_mid,
        extra_args=("--backend", "cqt", "--heatmap", output_heatmap),
        timeout_seconds=60.0,
    )

    assert_successful_analysis(result, output_mid)
    heatmap = load_heatmap(output_heatmap)
    assert heatmap["version"] == 1
    assert heatmap["backend"] == "cqt"
    assert heatmap["hop_size"] == 512
    assert heatmap["window_size"] == 1024
    assert heatmap["midi_notes"] == EXPECTED_MIDI_NOTES
    assert heatmap["frames"], "CQT backend should emit at least one frame"
    assert all(len(frame["activations"]) == len(EXPECTED_MIDI_NOTES) for frame in heatmap["frames"])
    assert all(0.0 <= activation <= 1.0 for frame in heatmap["frames"] for activation in frame["activations"])


@pytest.mark.cli
@pytest.mark.heatmap
@pytest.mark.cqt
def test_cqt_backend_a4_heatmap_peak_and_midi_note_are_69(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.cqt.mid"
    output_heatmap = tmp_path / "a4.cqt.heatmap.json"

    result = run_notegrabber_analyze(
        input_wav,
        output_mid,
        extra_args=("--backend", "cqt", "--heatmap", output_heatmap),
        timeout_seconds=60.0,
    )

    assert_successful_analysis(result, output_mid)
    heatmap = load_heatmap(output_heatmap)
    assert global_peak_note(heatmap) == 69
    assert set(read_note_pitches(output_mid)) == {69}
    intervals = read_note_intervals_seconds(output_mid)
    assert intervals
    longest_duration = max(interval.duration_seconds for interval in intervals)
    assert longest_duration == pytest.approx(0.8, abs=0.25)


@pytest.mark.cqt
def test_cqt_compact_builder_preserves_python_six_decimal_rounding(monkeypatch, tmp_path: Path) -> None:
    import librosa
    import numpy as np

    from notegrabber.analyzer import build_cqt_heatmap_data
    from notegrabber.heatmap import heatmap_to_document, notes_from_heatmap_data

    cqt = np.zeros((88, 1), dtype=np.complex64)
    target_index = 5
    cqt[target_index, 0] = np.float32(0.6545714736)
    cqt[10, 0] = 1.0  # normalization reference, away from target neighbours

    monkeypatch.setattr(librosa, "load", lambda *_args, **_kwargs: (np.zeros(1024, dtype=np.float32), 22050))
    monkeypatch.setattr(librosa, "cqt", lambda *_args, **_kwargs: cqt)

    heatmap = build_cqt_heatmap_data(tmp_path / "synthetic.wav")
    document = heatmap_to_document(heatmap)

    assert document["frames"][0]["activations"][target_index] == 0.654571
    below_boundary = notes_from_heatmap_data(
        heatmap,
        threshold=0.654571,
        min_duration_seconds=0.0,
        min_note_frames=1,
    )
    above_boundary = notes_from_heatmap_data(
        heatmap,
        threshold=0.654572,
        min_duration_seconds=0.0,
        min_note_frames=1,
    )
    target_pitch = EXPECTED_MIDI_NOTES[target_index]
    assert target_pitch in {note.pitch for note in below_boundary}
    assert target_pitch not in {note.pitch for note in above_boundary}
