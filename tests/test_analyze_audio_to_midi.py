"""Incremental audio-to-MIDI behavior tests for the notegrabber CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import (
    assert_successful_analysis,
    read_note_on_events,
    read_note_pitches,
    run_notegrabber_analyze,
    unique_pitches,
    write_chord_wav,
    write_sequence_wav,
    write_silence_wav,
    write_single_note_wav,
)


@pytest.mark.cli
@pytest.mark.tier1
def test_single_a4_sine_produces_midi_note_69(tmp_path: Path) -> None:
    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    output_mid = tmp_path / "a4.mid"

    result = run_notegrabber_analyze(input_wav, output_mid)

    assert_successful_analysis(result, output_mid)
    assert unique_pitches(output_mid) == {69}


@pytest.mark.cli
@pytest.mark.tier2
def test_two_sequential_notes_are_preserved_in_order(tmp_path: Path) -> None:
    expected_notes = [69, 72]
    input_wav = write_sequence_wav(tmp_path / "a4_then_c5.wav", notes=expected_notes)
    output_mid = tmp_path / "a4_then_c5.mid"

    result = run_notegrabber_analyze(input_wav, output_mid)

    assert_successful_analysis(result, output_mid)
    events = read_note_on_events(output_mid)
    assert {event.note for event in events} == set(expected_notes)
    first_tick_by_note = {note: min(event.tick for event in events if event.note == note) for note in expected_notes}
    assert first_tick_by_note[69] < first_tick_by_note[72]


@pytest.mark.cli
@pytest.mark.tier3
def test_simple_c_major_chord_produces_polyphonic_notes(tmp_path: Path) -> None:
    expected_notes = {60, 64, 67}
    input_wav = write_chord_wav(tmp_path / "c_major.wav", notes=sorted(expected_notes))
    output_mid = tmp_path / "c_major.mid"

    result = run_notegrabber_analyze(input_wav, output_mid)

    assert_successful_analysis(result, output_mid)
    assert unique_pitches(output_mid) == expected_notes


@pytest.mark.cli
@pytest.mark.edge
def test_silence_produces_no_note_events(tmp_path: Path) -> None:
    input_wav = write_silence_wav(tmp_path / "silence.wav")
    output_mid = tmp_path / "silence.mid"

    result = run_notegrabber_analyze(input_wav, output_mid)

    assert_successful_analysis(result, output_mid)
    assert read_note_pitches(output_mid) == []


@pytest.mark.cli
@pytest.mark.edge
def test_analyze_missing_input_returns_nonzero_without_midi(tmp_path: Path) -> None:
    missing_wav = tmp_path / "does-not-exist.wav"
    output_mid = tmp_path / "should-not-exist.mid"

    result = run_notegrabber_analyze(missing_wav, output_mid)

    assert result.returncode != 0, "analyze should fail for a missing input file"
    assert not output_mid.exists(), "analyze should not create MIDI output when input is missing"
