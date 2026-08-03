"""Spotify Basic Pitch backend contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("basic_pitch")
pytest.importorskip("onnxruntime")

from tests.helpers import (  # noqa: E402  (import after optional dependency checks)
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
@pytest.mark.basic_pitch
def test_basic_pitch_backend_writes_ml_heatmap_schema(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.basic_pitch.mid"
    output_heatmap = tmp_path / "a4.basic_pitch.heatmap.json"

    result = run_notegrabber_analyze(
        input_wav,
        output_mid,
        extra_args=("--backend", "basic-pitch", "--heatmap", output_heatmap),
        timeout_seconds=90.0,
    )

    assert_successful_analysis(result, output_mid)
    heatmap = load_heatmap(output_heatmap)
    assert heatmap["version"] == 1
    assert heatmap["backend"] == "basic-pitch"
    assert heatmap["sample_rate"] == 86
    assert heatmap["hop_size"] == 1
    assert heatmap["window_size"] == 1
    assert heatmap["midi_notes"] == EXPECTED_MIDI_NOTES
    assert heatmap["frames"], "Basic Pitch backend should emit at least one probability frame"
    assert all(len(frame["activations"]) == len(EXPECTED_MIDI_NOTES) for frame in heatmap["frames"])
    assert all(0.0 <= activation <= 1.0 for frame in heatmap["frames"] for activation in frame["activations"])


@pytest.mark.basic_pitch
def test_basic_pitch_range_extraction_uses_onnx_without_native_runtime_abort(tmp_path: Path) -> None:
    """Range extraction before inference must not trigger Basic Pitch's TFLite probe.

    Keep this in a subprocess because the regression is a native SIGABRT/SIGSEGV,
    not a catchable Python exception. A failure should fail this test without
    taking down the complete pytest process.
    """

    input_wav = write_single_note_wav(tmp_path / "range-source.wav", note=69, duration_seconds=3.0)
    range_wav = tmp_path / "range.wav"
    script = """
from pathlib import Path
import sys
from notegrabber.analyzer import analyze_basic_pitch_data
from notegrabber.gui.analysis_worker import _extract_audio_range
source, extracted = map(Path, sys.argv[1:3])
_extract_audio_range(source, extracted, start_seconds=0.5, duration_seconds=1.0)
notes, heatmap = analyze_basic_pitch_data(extracted)
assert notes
assert heatmap.frame_count > 0
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(input_wav), str(range_wav)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )

    assert result.returncode == 0, (
        f"Basic Pitch range subprocess exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.cli
@pytest.mark.basic_pitch
def test_basic_pitch_backend_a4_outputs_note_69_with_real_timing(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.basic_pitch.mid"
    output_heatmap = tmp_path / "a4.basic_pitch.heatmap.json"

    result = run_notegrabber_analyze(
        input_wav,
        output_mid,
        extra_args=("--backend", "basic-pitch", "--heatmap", output_heatmap),
        timeout_seconds=90.0,
    )

    assert_successful_analysis(result, output_mid)
    assert set(read_note_pitches(output_mid)) == {69}
    heatmap = load_heatmap(output_heatmap)
    assert global_peak_note(heatmap) == 69
    intervals = read_note_intervals_seconds(output_mid)
    assert len(intervals) == 1
    assert intervals[0].duration_seconds == pytest.approx(0.8, abs=0.25)
