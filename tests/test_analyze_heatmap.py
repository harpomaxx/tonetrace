"""Incremental heatmap output contract tests for the notegrabber CLI."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import (
    SAMPLE_RATE,
    assert_successful_analysis,
    run_notegrabber_analyze,
    write_silence_wav,
    write_single_note_wav,
)

EXPECTED_MIDI_NOTES = list(range(21, 109))


def load_heatmap(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_valid_heatmap_schema(heatmap: dict[str, Any], expected_sample_count: int) -> None:
    assert heatmap["version"] == 1
    assert heatmap["sample_rate"] == SAMPLE_RATE
    assert heatmap["hop_size"] == 512
    assert heatmap["window_size"] == 1024
    assert heatmap["midi_notes"] == EXPECTED_MIDI_NOTES

    frames = heatmap["frames"]
    assert isinstance(frames, list)
    assert len(frames) == math.ceil(expected_sample_count / heatmap["hop_size"])

    for index, frame in enumerate(frames):
        assert set(frame) == {"time_seconds", "activations"}
        assert frame["time_seconds"] == pytest.approx(index * heatmap["hop_size"] / SAMPLE_RATE)
        activations = frame["activations"]
        assert isinstance(activations, list)
        assert len(activations) == len(EXPECTED_MIDI_NOTES)
        assert all(isinstance(activation, int | float) for activation in activations)
        assert all(0.0 <= activation <= 1.0 for activation in activations)


@pytest.mark.cli
@pytest.mark.heatmap
def test_single_a4_sine_writes_heatmap_with_expected_schema_and_dimensions(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.mid"
    output_heatmap = tmp_path / "nested" / "a4.heatmap.json"

    result = run_notegrabber_analyze(input_wav, output_mid, extra_args=("--heatmap", output_heatmap))

    assert_successful_analysis(result, output_mid)
    assert output_heatmap.exists(), "analyze command did not create the requested heatmap JSON output"

    heatmap = load_heatmap(output_heatmap)
    assert_valid_heatmap_schema(heatmap, expected_sample_count=int(0.8 * SAMPLE_RATE))
    assert max(max(frame["activations"]) for frame in heatmap["frames"]) == pytest.approx(1.0)


@pytest.mark.cli
@pytest.mark.heatmap
def test_single_a4_sine_heatmap_peak_note_is_69(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.mid"
    output_heatmap = tmp_path / "a4.heatmap.json"

    result = run_notegrabber_analyze(input_wav, output_mid, extra_args=("--heatmap", output_heatmap))

    assert_successful_analysis(result, output_mid)
    heatmap = load_heatmap(output_heatmap)
    note_69_index = heatmap["midi_notes"].index(69)
    active_frames = [frame for frame in heatmap["frames"] if max(frame["activations"]) >= 0.5]

    assert active_frames, "A4 fixture should produce active heatmap frames"
    assert all(
        frame["activations"][note_69_index] == max(frame["activations"])
        for frame in active_frames
    ), "MIDI note 69 should have the strongest activation in active frames"


@pytest.mark.heatmap
def test_with_heatmap_returns_document_in_process_without_writing_json(tmp_path: Path) -> None:
    """analyze_wav_to_midi_with_heatmap returns the heatmap in memory and writes
    no heatmap file (issue #25: skip the serialize -> re-parse round-trip)."""

    from notegrabber.analyzer import analyze_wav_to_midi_with_heatmap

    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.mid"

    notes, heatmap = analyze_wav_to_midi_with_heatmap(input_wav, output_mid, backend="cqt")

    assert output_mid.exists()
    assert not (tmp_path / "a4.heatmap.json").exists()  # no stray heatmap file
    assert isinstance(heatmap, dict)
    assert heatmap["midi_notes"] == EXPECTED_MIDI_NOTES
    assert heatmap["frames"] and all(
        0.0 <= a <= 1.0 for frame in heatmap["frames"] for a in frame["activations"]
    )
    # The returned document must round-trip into the GUI model unchanged.
    from notegrabber.gui.state import heatmap_from_document

    model = heatmap_from_document(heatmap)
    assert model.midi_notes == EXPECTED_MIDI_NOTES
    assert len(model.frame_times) == len(heatmap["frames"])


@pytest.mark.heatmap
def test_written_heatmap_file_matches_returned_document(tmp_path: Path) -> None:
    """The CLI file path and the in-process document describe the same heatmap."""

    from notegrabber.analyzer import analyze_wav_to_midi, analyze_wav_to_midi_with_heatmap

    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)

    _, returned = analyze_wav_to_midi_with_heatmap(input_wav, tmp_path / "mem.mid", backend="cqt")
    heatmap_file = tmp_path / "written.heatmap.json"
    analyze_wav_to_midi(input_wav, tmp_path / "file.mid", heatmap_path=heatmap_file, backend="cqt")

    written = load_heatmap(heatmap_file)
    assert written["midi_notes"] == returned["midi_notes"]
    assert len(written["frames"]) == len(returned["frames"])
    for wf, rf in zip(written["frames"], returned["frames"]):
        assert wf["time_seconds"] == pytest.approx(rf["time_seconds"])
        assert wf["activations"] == pytest.approx(rf["activations"])


@pytest.mark.cli
@pytest.mark.heatmap
def test_silence_heatmap_is_valid_with_no_meaningful_activation(tmp_path: Path) -> None:
    input_wav = write_silence_wav(tmp_path / "silence.wav")
    output_mid = tmp_path / "silence.mid"
    output_heatmap = tmp_path / "silence.heatmap.json"

    result = run_notegrabber_analyze(input_wav, output_mid, extra_args=("--heatmap", output_heatmap))

    assert_successful_analysis(result, output_mid)
    assert output_heatmap.exists(), "analyze command did not create the requested heatmap JSON output"

    heatmap = load_heatmap(output_heatmap)
    assert_valid_heatmap_schema(heatmap, expected_sample_count=int(0.5 * SAMPLE_RATE))
    assert max(max(frame["activations"]) for frame in heatmap["frames"]) <= 1e-12
