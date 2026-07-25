"""Small deterministic DSP baseline for converting simple WAV tones to MIDI notes."""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from .midi import MidiNote, TICKS_PER_SECOND, write_midi

MIN_MIDI_NOTE = 21
MAX_MIDI_NOTE = 108
WINDOW_SIZE = 1024
HOP_SIZE = 512
SILENCE_RMS_FLOOR = 0.01
ACTIVITY_RATIO = 0.20
PITCH_RATIO = 0.35


@dataclass(frozen=True)
class AudioData:
    """Mono floating-point audio samples and sample rate."""

    samples: list[float]
    sample_rate: int


@dataclass(frozen=True)
class Segment:
    """An active audio region expressed as sample offsets."""

    start: int
    end: int


def midi_note_frequency(note: int) -> float:
    """Return the equal-tempered frequency for a MIDI note number."""

    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def read_wav(path: Path) -> AudioData:
    """Read a PCM WAV file and return mono samples in roughly [-1.0, 1.0]."""

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)

    if channels < 1:
        raise ValueError("WAV file has no audio channels")
    if sample_rate <= 0:
        raise ValueError("WAV file has an invalid sample rate")
    if sample_width not in (1, 2, 3, 4):
        raise ValueError(f"unsupported WAV sample width: {sample_width} bytes")

    samples: list[float] = []
    frame_width = channels * sample_width
    for frame_start in range(0, len(raw), frame_width):
        channel_values = []
        for channel in range(channels):
            offset = frame_start + channel * sample_width
            chunk = raw[offset : offset + sample_width]
            if len(chunk) != sample_width:
                continue
            channel_values.append(_decode_pcm_sample(chunk, sample_width))
        if channel_values:
            samples.append(sum(channel_values) / len(channel_values))

    return AudioData(samples=samples, sample_rate=sample_rate)


def analyze_wav_to_midi(input_wav: Path, output_midi: Path) -> list[MidiNote]:
    """Analyze a simple WAV fixture and write detected notes to a MIDI file."""

    audio = read_wav(input_wav)
    segments = find_active_segments(audio.samples)

    notes: list[MidiNote] = []
    for segment in segments:
        segment_samples = audio.samples[segment.start : segment.end]
        pitches = detect_pitches(segment_samples, audio.sample_rate)
        start_tick = round(segment.start * TICKS_PER_SECOND / audio.sample_rate)
        duration_ticks = max(1, round((segment.end - segment.start) * TICKS_PER_SECOND / audio.sample_rate))
        for pitch in pitches:
            notes.append(MidiNote(pitch=pitch, start_tick=start_tick, duration_ticks=duration_ticks))

    write_midi(output_midi, notes)
    return notes


def _decode_pcm_sample(chunk: bytes, sample_width: int) -> float:
    if sample_width == 1:
        return (chunk[0] - 128) / 128.0
    if sample_width == 2:
        return struct.unpack("<h", chunk)[0] / 32768.0
    if sample_width == 3:
        value = int.from_bytes(chunk, "little", signed=False)
        if value & 0x800000:
            value -= 0x1000000
        return value / 8388608.0
    return struct.unpack("<i", chunk)[0] / 2147483648.0


def find_active_segments(samples: list[float]) -> list[Segment]:
    """Find contiguous non-silent regions using short-window RMS energy."""

    if not samples:
        return []

    window_size = min(WINDOW_SIZE, len(samples))
    hop_size = min(HOP_SIZE, window_size)
    windows: list[tuple[int, int, float]] = []
    for start in range(0, len(samples), hop_size):
        end = min(len(samples), start + window_size)
        if end <= start:
            break
        rms = math.sqrt(sum(sample * sample for sample in samples[start:end]) / (end - start))
        windows.append((start, end, rms))
        if end == len(samples):
            break

    max_rms = max((rms for _start, _end, rms in windows), default=0.0)
    if max_rms < SILENCE_RMS_FLOOR:
        return []

    threshold = max(SILENCE_RMS_FLOOR, max_rms * ACTIVITY_RATIO)
    segments: list[Segment] = []
    current_start: int | None = None
    current_end: int | None = None
    for start, end, rms in windows:
        if rms >= threshold:
            if current_start is None:
                current_start = start
            current_end = end
        elif current_start is not None and current_end is not None:
            segments.append(_trim_segment(samples, current_start, current_end, threshold * 0.5))
            current_start = None
            current_end = None

    if current_start is not None and current_end is not None:
        segments.append(_trim_segment(samples, current_start, current_end, threshold * 0.5))

    return [segment for segment in segments if segment.end > segment.start]


def _trim_segment(samples: list[float], start: int, end: int, amplitude_threshold: float) -> Segment:
    """Trim leading and trailing near-zero samples from an active segment."""

    while start < end and abs(samples[start]) < amplitude_threshold:
        start += 1
    while end > start and abs(samples[end - 1]) < amplitude_threshold:
        end -= 1
    return Segment(start=start, end=end)


def detect_pitches(samples: list[float], sample_rate: int) -> list[int]:
    """Detect one or more MIDI pitches in a sustained simple-tone segment."""

    if not samples:
        return []

    magnitudes = [(note, _tone_magnitude(samples, sample_rate, midi_note_frequency(note))) for note in range(MIN_MIDI_NOTE, MAX_MIDI_NOTE + 1)]
    max_magnitude = max((magnitude for _note, magnitude in magnitudes), default=0.0)
    if max_magnitude <= 0.0:
        return []

    pitches = [note for note, magnitude in magnitudes if magnitude >= max_magnitude * PITCH_RATIO]
    return sorted(pitches)


def _tone_magnitude(samples: list[float], sample_rate: int, frequency: float) -> float:
    """Return the Hann-windowed correlation magnitude at a target frequency."""

    count = len(samples)
    if count == 1:
        return abs(samples[0])

    re = 0.0
    im = 0.0
    phase_cos = 1.0
    phase_sin = 0.0
    step = 2.0 * math.pi * frequency / sample_rate
    step_cos = math.cos(step)
    step_sin = math.sin(step)

    for index, sample in enumerate(samples):
        window = 0.5 - 0.5 * math.cos(2.0 * math.pi * index / (count - 1))
        value = sample * window
        re += value * phase_cos
        im -= value * phase_sin
        next_cos = phase_cos * step_cos - phase_sin * step_sin
        phase_sin = phase_sin * step_cos + phase_cos * step_sin
        phase_cos = next_cos

    return math.hypot(re, im)
