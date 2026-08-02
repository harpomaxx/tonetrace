"""Pure-Python MIDI-to-WAV synthesis, no external tools required.

Renders a MIDI file to a WAV using numpy: each note becomes a few enveloped sine
partials, summed onto one buffer. It sounds like a synthesizer, not a real
instrument, which is fine for a transcription *preview* -- the point is to hear
whether the notes and timing are right. Unlike TiMidity, it needs no external
binary and no soundfont, so it behaves identically on Windows, Linux, and macOS.

Reading the MIDI file needs ``mido`` (pure-Python); synthesis needs ``numpy``.
"""

from __future__ import annotations

import wave
from pathlib import Path

SAMPLE_RATE = 44100


def _midi_note_to_freq(note: int) -> float:
    """Convert a MIDI note number to frequency in Hz (A4 = note 69 = 440 Hz)."""

    return 440.0 * (2.0 ** ((note - 69) / 12.0))


DEFAULT_BEND_RANGE_SEMITONES = 2.0  # MIDI default when no RPN sets it otherwise


def _render_note(freq: float, duration: float, velocity: float, bend_semitones=None):
    """Render one note as a short, enveloped tone (fundamental + 2 harmonics).

    ``bend_semitones`` optionally applies a time-varying pitch bend: a sequence of
    (time_in_note_seconds, semitone_offset) points sampled during the note. The
    instantaneous frequency is ``freq * 2**(offset/12)``; because the frequency
    changes over time, the phase is integrated (cumsum) rather than ``freq*t``.
    """

    import numpy as np

    n = max(1, int(duration * SAMPLE_RATE))
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE

    # Phase must accumulate in float64: float32 cumsum drifts audibly over a long
    # note. The common no-bend path has a closed-form phase (2*pi*freq*t) that
    # needs no cumsum; only a real pitch bend needs the integrated path.
    if bend_semitones:
        t64 = np.arange(n, dtype=np.float64) / SAMPLE_RATE
        times = np.array([p[0] for p in bend_semitones], dtype=np.float64)
        offsets = np.array([p[1] for p in bend_semitones], dtype=np.float64)
        # Step-and-hold interpolation of the bend offset onto every sample, then a
        # frequency multiplier per sample.
        semis = np.interp(t64, times, offsets, left=offsets[0], right=offsets[-1])
        inst_freq = freq * (2.0 ** (semis / 12.0))
        phase = (2 * np.pi * np.cumsum(inst_freq) / SAMPLE_RATE).astype(np.float32)
    else:
        phase = (np.arange(n, dtype=np.float64) * (2 * np.pi * freq / SAMPLE_RATE)).astype(np.float32)

    # Fundamental + one harmonic. Dropping the (weak) 3rd partial and running the
    # sines in float32 roughly halves the per-note synthesis cost.
    wave_data = np.sin(phase)
    wave_data += 0.35 * np.sin(2.0 * phase)

    env = np.ones(n, dtype=np.float32)
    attack = min(int(0.01 * SAMPLE_RATE), n)
    release = min(int(0.08 * SAMPLE_RATE), n)
    if attack > 0:
        env[:attack] = np.linspace(0.0, 1.0, attack, dtype=np.float32)
    if release > 0:
        env[-release:] *= np.linspace(1.0, 0.0, release, dtype=np.float32)
    env *= np.exp(-1.5 * t / max(duration, 1e-3)) * 0.6 + 0.4

    return (wave_data * env * velocity).astype(np.float32)


def _read_note_events(midi_path: Path):
    """Read note events (with per-note bend contours) and total length from a MIDI file.

    Each event is ``(start, duration, note, velocity, bends)`` where ``bends`` is a
    list of ``(time_in_note_seconds, semitone_offset)`` points, or ``None``. Pitch
    bend is tracked per channel (matching how the writer emits it); a note inherits
    whatever bend samples occur on its channel during its lifetime.
    """

    import mido  # type: ignore[import-not-found]

    mid = mido.MidiFile(str(midi_path))
    events: list[tuple[float, float, int, float, list | None]] = []
    pending: dict[tuple[int, int], tuple[float, float]] = {}
    # Per-channel bend range (semitones) set via RPN 0, and the bend history as a
    # list of (time_seconds, semitone_offset) so a note can slice out its own span.
    bend_range: dict[int, float] = {}
    bend_history: dict[int, list[tuple[float, float]]] = {}
    rpn_selected: dict[int, tuple[int, int]] = {}
    now = 0.0
    total = 0.0
    for msg in mid:  # merged iteration yields real-time deltas in seconds
        now += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            pending[(msg.channel, msg.note)] = (now, msg.velocity / 127.0)
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            key = (msg.channel, msg.note)
            if key in pending:
                start, vel = pending.pop(key)
                duration = max(now - start, 0.05)
                bends = _slice_bend(bend_history.get(msg.channel), start, now)
                events.append((start, duration, msg.note, vel, bends))
                total = max(total, now)
        elif msg.type == "pitchwheel":
            rng = bend_range.get(msg.channel, DEFAULT_BEND_RANGE_SEMITONES)
            # mido pitch is -8192..8191, 0 = center.
            semis = (msg.pitch / 8192.0) * rng
            bend_history.setdefault(msg.channel, []).append((now, semis))
        elif msg.type == "control_change":
            _track_bend_range_rpn(msg, rpn_selected, bend_range)
    return events, total


def _track_bend_range_rpn(msg, rpn_selected: dict, bend_range: dict) -> None:
    """Interpret RPN 0 (pitch-bend range) control changes to learn each channel's range."""

    ch = msg.channel
    if msg.control == 101:  # RPN MSB
        prev = rpn_selected.get(ch, (0x7F, 0x7F))
        rpn_selected[ch] = (msg.value, prev[1])
    elif msg.control == 100:  # RPN LSB
        prev = rpn_selected.get(ch, (0x7F, 0x7F))
        rpn_selected[ch] = (prev[0], msg.value)
    elif msg.control == 6:  # data entry MSB = range in semitones (only if RPN 0 selected)
        if rpn_selected.get(ch) == (0, 0):
            bend_range[ch] = float(msg.value)


def _slice_bend(history, start: float, end: float):
    """Return bend points within [start, end], re-based to time-in-note, or None."""

    if not history:
        return None
    points = [(t - start, semis) for (t, semis) in history if start <= t <= end]
    return points or None


def render_midi_to_wav_native(midi_path: Path, wav_path: Path) -> tuple[Path | None, str | None]:
    """Synthesize ``midi_path`` to ``wav_path`` with the built-in numpy synth.

    Returns ``(wav_path, None)`` on success or ``(None, message)`` on failure,
    matching :func:`notegrabber.midi_render.render_midi_to_wav`.
    """

    try:
        import numpy as np
    except ModuleNotFoundError:
        return None, "Native MIDI synthesis requires numpy; install with `.[gui]` or `.[ml]`."
    try:
        import mido  # noqa: F401  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return None, "Native MIDI synthesis requires `mido` to read MIDI; install with `.[gui]` or `.[ml]`."

    try:
        events, total = _read_note_events(midi_path)
    except Exception as exc:
        return None, f"Native MIDI synthesis could not read {midi_path}: {exc}"

    n = max(1, int((total + 0.3) * SAMPLE_RATE))
    buffer = np.zeros(n, dtype="float32")
    for start, duration, note, velocity, bends in events:
        tone = _render_note(_midi_note_to_freq(note), duration, velocity, bend_semitones=bends)
        i = int(start * SAMPLE_RATE)
        end = min(i + len(tone), n)
        buffer[i:end] += tone[: end - i]

    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > 0:
        buffer = buffer / peak * 0.9

    pcm16 = (buffer * 32767).astype("int16")
    try:
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(wav_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(pcm16.tobytes())
    except Exception as exc:
        return None, f"Native MIDI synthesis could not write {wav_path}: {exc}"

    return wav_path, None
