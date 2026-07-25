"""Small helpers for notegrabber CLI contract tests.

The helpers intentionally stay test-only: they generate deterministic WAV files,
execute the configured CLI, and inspect MIDI note-on events.
"""

from __future__ import annotations

import math
import os
import shlex
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pytest

SAMPLE_RATE = 16_000
SAMPLE_WIDTH_BYTES = 2
MAX_INT16 = 32_767


@dataclass(frozen=True)
class MidiNoteEvent:
    """A parsed MIDI note-on event with its absolute tick position."""

    note: int
    tick: int
    track: int


def midi_note_frequency(note: int) -> float:
    """Return the equal-tempered frequency for a MIDI note number."""

    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def notegrabber_command() -> list[str]:
    """Return the configured CLI command or skip when it is unavailable."""

    configured = os.environ.get("NOTEGRABBER_BIN", "notegrabber")
    command = shlex.split(configured)
    if not command:
        pytest.skip("NOTEGRABBER_BIN is empty; set it to the notegrabber executable")

    executable = command[0]
    resolved = executable if Path(executable).exists() else shutil.which(executable)
    if resolved is None:
        pytest.skip(
            f"notegrabber CLI not found ({configured!r}); set NOTEGRABBER_BIN to run CLI contract tests"
        )

    return [str(resolved), *command[1:]]


def render_wav(path: Path, frames: Sequence[Sequence[float]]) -> Path:
    """Write mono 16-bit PCM WAV samples in the inclusive range [-1.0, 1.0]."""

    samples: list[int] = []
    for frame in frames:
        value = max(-1.0, min(1.0, sum(frame)))
        samples.append(int(value * MAX_INT16))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))

    return path


def sine_frames(notes: Iterable[int], duration_seconds: float, amplitude: float = 0.45) -> list[list[float]]:
    """Render one or more MIDI notes as additive sine-wave sample frames."""

    note_list = list(notes)
    if not note_list:
        return silence_frames(duration_seconds)

    per_tone_amplitude = amplitude / len(note_list)
    frame_count = int(SAMPLE_RATE * duration_seconds)
    frequencies = [midi_note_frequency(note) for note in note_list]
    return [
        [per_tone_amplitude * math.sin(2.0 * math.pi * frequency * index / SAMPLE_RATE) for frequency in frequencies]
        for index in range(frame_count)
    ]


def silence_frames(duration_seconds: float) -> list[list[float]]:
    """Render silence for the requested duration."""

    return [[0.0] for _ in range(int(SAMPLE_RATE * duration_seconds))]


def write_single_note_wav(path: Path, note: int, duration_seconds: float = 0.8) -> Path:
    """Create a WAV containing one sustained sine note."""

    return render_wav(path, sine_frames([note], duration_seconds))


def write_sequence_wav(path: Path, notes: Sequence[int], note_seconds: float = 0.45, gap_seconds: float = 0.08) -> Path:
    """Create a WAV containing notes in sequence separated by short silence."""

    frames: list[list[float]] = []
    for index, note in enumerate(notes):
        if index:
            frames.extend(silence_frames(gap_seconds))
        frames.extend(sine_frames([note], note_seconds))
    return render_wav(path, frames)


def write_chord_wav(path: Path, notes: Sequence[int], duration_seconds: float = 0.8) -> Path:
    """Create a WAV containing a simple simultaneous chord."""

    return render_wav(path, sine_frames(notes, duration_seconds))


def write_silence_wav(path: Path, duration_seconds: float = 0.5) -> Path:
    """Create a silent WAV file."""

    return render_wav(path, silence_frames(duration_seconds))


def run_notegrabber_analyze(input_wav: Path, output_mid: Path, timeout_seconds: float = 20.0) -> subprocess.CompletedProcess[str]:
    """Run `$NOTEGRABBER_BIN analyze <input> --out <output>` and capture output."""

    command = [*notegrabber_command(), "analyze", str(input_wav), "--out", str(output_mid)]
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds)


def read_note_on_events(midi_path: Path) -> list[MidiNoteEvent]:
    """Read all note-on events with non-zero velocity from a Standard MIDI File."""

    try:
        from mido import MidiFile
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only in incomplete test envs
        pytest.fail("mido is required for MIDI contract tests; install requirements-dev.txt")
        raise exc

    midi = MidiFile(midi_path)
    events: list[MidiNoteEvent] = []
    for track_index, track in enumerate(midi.tracks):
        absolute_tick = 0
        for message in track:
            absolute_tick += message.time
            if message.type == "note_on" and message.velocity > 0:
                events.append(MidiNoteEvent(note=message.note, tick=absolute_tick, track=track_index))
    return sorted(events, key=lambda event: (event.tick, event.track, event.note))


def read_note_pitches(midi_path: Path) -> list[int]:
    """Return note-on pitches in event order."""

    return [event.note for event in read_note_on_events(midi_path)]


def unique_pitches(midi_path: Path) -> set[int]:
    """Return the unique note-on pitches from a MIDI file."""

    return set(read_note_pitches(midi_path))


def assert_successful_analysis(result: subprocess.CompletedProcess[str], output_mid: Path) -> None:
    """Assert a CLI analyze invocation succeeded and produced MIDI output."""

    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert output_mid.exists(), "analyze command did not create the requested MIDI output"
    assert output_mid.stat().st_size > 0, "analyze command created an empty MIDI output file"
