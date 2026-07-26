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
