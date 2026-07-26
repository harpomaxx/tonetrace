"""Small deterministic DSP baselines for converting simple WAV tones to MIDI notes."""

from __future__ import annotations

import json
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .midi import MidiNote, TICKS_PER_SECOND, write_midi

MIN_MIDI_NOTE = 21
MAX_MIDI_NOTE = 108
MIDI_NOTES = tuple(range(MIN_MIDI_NOTE, MAX_MIDI_NOTE + 1))
WINDOW_SIZE = 1024
HOP_SIZE = 512
SILENCE_RMS_FLOOR = 0.01
ACTIVITY_RATIO = 0.20
PITCH_RATIO = 0.35
CQT_THRESHOLD = 0.45
MIN_NOTE_FRAMES = 1
BASIC_PITCH_ONSET_THRESHOLD = 0.5
BASIC_PITCH_FRAME_THRESHOLD = 0.3
BackendName = Literal["simple", "cqt", "basic-pitch"]


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


def analyze_wav_to_midi(
    input_wav: Path,
    output_midi: Path,
    heatmap_path: Path | None = None,
    backend: BackendName = "simple",
) -> list[MidiNote]:
    """Analyze a WAV file and write detected notes to a MIDI file."""

    if backend == "simple":
        audio = read_wav(input_wav)
        notes = analyze_simple(audio)
        write_midi(output_midi, notes)
        if heatmap_path is not None:
            write_heatmap(heatmap_path, build_simple_heatmap(audio))
        return notes

    if backend == "cqt":
        heatmap = build_cqt_heatmap(input_wav)
        notes = notes_from_heatmap(heatmap)
        write_midi(output_midi, notes)
        if heatmap_path is not None:
            write_heatmap(heatmap_path, heatmap)
        return notes

    if backend == "basic-pitch":
        notes, heatmap = analyze_basic_pitch(input_wav)
        write_midi(output_midi, notes)
        if heatmap_path is not None:
            write_heatmap(heatmap_path, heatmap)
        return notes

    raise ValueError(f"unsupported backend: {backend}")


def analyze_simple(audio: AudioData) -> list[MidiNote]:
    """Analyze deterministic simple-tone fixtures with direct pitch correlation."""

    segments = find_active_segments(audio.samples)

    notes: list[MidiNote] = []
    for segment in segments:
        segment_samples = audio.samples[segment.start : segment.end]
        pitches = detect_pitches(segment_samples, audio.sample_rate)
        start_tick = round(segment.start * TICKS_PER_SECOND / audio.sample_rate)
        duration_ticks = max(1, round((segment.end - segment.start) * TICKS_PER_SECOND / audio.sample_rate))
        for pitch in pitches:
            notes.append(MidiNote(pitch=pitch, start_tick=start_tick, duration_ticks=duration_ticks))
    return notes


def write_heatmap(path: Path, heatmap: dict[str, object]) -> None:
    """Write per-frame MIDI-note salience values as deterministic JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(heatmap, indent=2) + "\n", encoding="utf-8")


def build_heatmap(audio: AudioData) -> dict[str, object]:
    """Backward-compatible alias for the simple heatmap backend."""

    return build_simple_heatmap(audio)


def build_simple_heatmap(audio: AudioData) -> dict[str, object]:
    """Build normalized per-analysis activations using direct sine correlation."""

    midi_notes = list(MIDI_NOTES)
    raw_frames: list[tuple[float, list[float]]] = []
    max_magnitude = 0.0
    max_rms = 0.0

    for start, end in _analysis_windows(audio.samples):
        frame_samples = audio.samples[start:end]
        rms = _rms(frame_samples)
        max_rms = max(max_rms, rms)
        magnitudes = [_tone_magnitude(frame_samples, audio.sample_rate, midi_note_frequency(note)) for note in midi_notes]
        max_magnitude = max(max_magnitude, max(magnitudes, default=0.0))
        raw_frames.append((start / audio.sample_rate, magnitudes))

    normalizer = max_magnitude if max_rms >= SILENCE_RMS_FLOOR else 0.0
    frames = _normalize_frames(raw_frames, midi_notes, normalizer)

    return _heatmap_document(
        backend="simple",
        sample_rate=audio.sample_rate,
        hop_size=HOP_SIZE,
        window_size=WINDOW_SIZE,
        midi_notes=midi_notes,
        frames=frames,
    )


def build_cqt_heatmap(input_wav: Path) -> dict[str, object]:
    """Build a Constant-Q Transform salience heatmap aligned to piano MIDI notes."""

    try:
        import librosa
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            "CQT backend requires optional dependencies; install with `python3 -m pip install -e .[cqt]` "
            "or `python3 -m pip install -r requirements-dev.txt`"
        ) from exc

    audio, sample_rate = librosa.load(input_wav, sr=None, mono=True)
    if sample_rate <= 0:
        raise ValueError("audio file has an invalid sample rate")

    midi_notes = list(MIDI_NOTES)
    cqt = librosa.cqt(
        audio,
        sr=sample_rate,
        hop_length=HOP_SIZE,
        fmin=librosa.midi_to_hz(MIN_MIDI_NOTE),
        n_bins=len(midi_notes),
        bins_per_octave=12,
    )
    magnitudes = np.abs(cqt)
    max_magnitude = float(magnitudes.max()) if magnitudes.size else 0.0

    frames: list[dict[str, object]] = []
    for frame_index in range(magnitudes.shape[1] if magnitudes.ndim == 2 else 0):
        column = magnitudes[:, frame_index]
        if max_magnitude > 0.0:
            activations = np.clip(column / max_magnitude, 0.0, 1.0)
            activation_list = [round(float(value), 6) for value in activations]
        else:
            activation_list = [0.0 for _note in midi_notes]
        frames.append(
            {
                "time_seconds": frame_index * HOP_SIZE / sample_rate,
                "activations": activation_list,
            }
        )

    return _heatmap_document(
        backend="cqt",
        sample_rate=int(sample_rate),
        hop_size=HOP_SIZE,
        window_size=WINDOW_SIZE,
        midi_notes=midi_notes,
        frames=frames,
    )


def analyze_basic_pitch(input_audio: Path) -> tuple[list[MidiNote], dict[str, object]]:
    """Analyze audio with Spotify Basic Pitch and return notes plus model salience."""

    try:
        import basic_pitch
        from basic_pitch import constants as basic_pitch_constants
        from basic_pitch.inference import predict
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            "Basic Pitch backend requires optional dependencies; install with "
            "`python3 -m pip install -e .[basic-pitch]` or `python3 -m pip install -r requirements-dev.txt`"
        ) from exc

    model_path = Path(basic_pitch.__file__).parent / "saved_models" / "icassp_2022" / "nmp.onnx"
    if not model_path.exists():
        raise RuntimeError("Basic Pitch ONNX model was not found; install `basic-pitch[onnx]`")

    model_output, _midi_data, note_events = predict(
        input_audio,
        model_or_model_path=model_path,
        onset_threshold=BASIC_PITCH_ONSET_THRESHOLD,
        frame_threshold=BASIC_PITCH_FRAME_THRESHOLD,
        midi_tempo=120,
    )
    notes = [_basic_pitch_event_to_midi_note(event) for event in note_events]
    heatmap = build_basic_pitch_heatmap(model_output, basic_pitch_constants)
    return sorted(notes, key=lambda note: (note.start_tick, note.pitch)), heatmap


def _basic_pitch_event_to_midi_note(event: object) -> MidiNote:
    start_seconds, end_seconds, pitch, confidence, _pitch_bends = event  # type: ignore[misc]
    start_tick = round(float(start_seconds) * TICKS_PER_SECOND)
    duration_ticks = max(1, round((float(end_seconds) - float(start_seconds)) * TICKS_PER_SECOND))
    velocity = max(1, min(127, round(float(confidence) * 127)))
    return MidiNote(pitch=int(pitch), start_tick=start_tick, duration_ticks=duration_ticks, velocity=velocity)


def build_basic_pitch_heatmap(model_output: dict[str, object], basic_pitch_constants: object) -> dict[str, object]:
    """Convert Basic Pitch note probabilities into the common heatmap JSON schema."""

    try:
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover - basic-pitch depends on numpy
        raise RuntimeError("Basic Pitch backend requires numpy") from exc

    note_probabilities = np.asarray(model_output["note"], dtype=float)
    midi_notes = list(MIDI_NOTES)
    annotation_fps = int(getattr(basic_pitch_constants, "ANNOTATIONS_FPS"))
    annotation_hop = float(getattr(basic_pitch_constants, "ANNOTATION_HOP"))
    frames: list[dict[str, object]] = []

    for frame_index in range(note_probabilities.shape[0] if note_probabilities.ndim == 2 else 0):
        row = note_probabilities[frame_index, : len(midi_notes)]
        activations = np.clip(row, 0.0, 1.0)
        frames.append(
            {
                "time_seconds": frame_index * annotation_hop,
                "activations": [round(float(value), 6) for value in activations],
            }
        )

    return _heatmap_document(
        backend="basic-pitch",
        sample_rate=annotation_fps,
        hop_size=1,
        window_size=1,
        midi_notes=midi_notes,
        frames=frames,
    )


def notes_from_heatmap(heatmap: dict[str, object], threshold: float = CQT_THRESHOLD) -> list[MidiNote]:
    """Extract note events from a normalized heatmap by grouping active frames."""

    sample_rate = int(heatmap["sample_rate"])
    hop_size = int(heatmap["hop_size"])
    midi_notes = [int(note) for note in heatmap["midi_notes"]]  # type: ignore[index]
    frames = heatmap["frames"]  # type: ignore[assignment]

    if not isinstance(frames, list) or not frames:
        return []

    seconds_per_tick = 1.0 / TICKS_PER_SECOND
    hop_seconds = hop_size / sample_rate
    notes: list[MidiNote] = []

    for note_index, pitch in enumerate(midi_notes):
        active_start: int | None = None
        active_peak = 0.0
        for frame_index, frame in enumerate(frames):
            activations = frame["activations"]  # type: ignore[index]
            activation = float(activations[note_index])
            is_local_peak = _is_local_peak(activations, note_index)
            is_active = activation >= threshold and is_local_peak
            if is_active:
                if active_start is None:
                    active_start = frame_index
                    active_peak = activation
                else:
                    active_peak = max(active_peak, activation)
            elif active_start is not None:
                _append_heatmap_note(notes, pitch, active_start, frame_index, hop_seconds, seconds_per_tick, active_peak)
                active_start = None
                active_peak = 0.0

        if active_start is not None:
            _append_heatmap_note(notes, pitch, active_start, len(frames), hop_seconds, seconds_per_tick, active_peak)

    return sorted(notes, key=lambda note: (note.start_tick, note.pitch))


def _append_heatmap_note(
    notes: list[MidiNote],
    pitch: int,
    start_frame: int,
    end_frame: int,
    hop_seconds: float,
    seconds_per_tick: float,
    peak_activation: float,
) -> None:
    if end_frame - start_frame < MIN_NOTE_FRAMES:
        return
    start_tick = round(start_frame * hop_seconds / seconds_per_tick)
    duration_ticks = max(1, round((end_frame - start_frame) * hop_seconds / seconds_per_tick))
    velocity = max(1, min(127, round(peak_activation * 127)))
    notes.append(MidiNote(pitch=pitch, start_tick=start_tick, duration_ticks=duration_ticks, velocity=velocity))


def _is_local_peak(activations: object, note_index: int) -> bool:
    values = activations  # type: ignore[assignment]
    activation = float(values[note_index])  # type: ignore[index]
    left = float(values[note_index - 1]) if note_index > 0 else -1.0  # type: ignore[index]
    right = float(values[note_index + 1]) if note_index + 1 < len(values) else -1.0  # type: ignore[arg-type,index]
    return activation >= left and activation >= right


def _heatmap_document(
    *,
    backend: str,
    sample_rate: int,
    hop_size: int,
    window_size: int,
    midi_notes: list[int],
    frames: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "version": 1,
        "backend": backend,
        "sample_rate": sample_rate,
        "hop_size": hop_size,
        "window_size": window_size,
        "midi_notes": midi_notes,
        "frames": frames,
    }


def _normalize_frames(
    raw_frames: list[tuple[float, list[float]]],
    midi_notes: list[int],
    normalizer: float,
) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    for time_seconds, magnitudes in raw_frames:
        if normalizer > 0.0:
            activations = [round(max(0.0, min(1.0, magnitude / normalizer)), 6) for magnitude in magnitudes]
        else:
            activations = [0.0 for _note in midi_notes]
        frames.append(
            {
                "time_seconds": time_seconds,
                "activations": activations,
            }
        )
    return frames


def _analysis_windows(samples: list[float]) -> list[tuple[int, int]]:
    """Return deterministic analysis windows over the full sample range."""

    windows: list[tuple[int, int]] = []
    for start in range(0, len(samples), HOP_SIZE):
        end = min(len(samples), start + WINDOW_SIZE)
        if end <= start:
            break
        windows.append((start, end))
    return windows


def _rms(samples: list[float]) -> float:
    """Return root-mean-square amplitude for a sample window."""

    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


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

    magnitudes = [(note, _tone_magnitude(samples, sample_rate, midi_note_frequency(note))) for note in MIDI_NOTES]
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
