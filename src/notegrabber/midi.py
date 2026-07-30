"""Minimal Standard MIDI File writer used by the CLI baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TICKS_PER_BEAT = 480
TEMPO_MICROSECONDS_PER_BEAT = 500_000  # 120 BPM
TICKS_PER_SECOND = round(TICKS_PER_BEAT * 1_000_000 / TEMPO_MICROSECONDS_PER_BEAT)

# Pitch-bend configuration. MIDI pitch bend is a 14-bit value centered at 8192;
# its musical range in semitones is set per channel via RPN 0 (default 2). We set
# it explicitly so bends written here are interpreted the same by every reader.
PITCH_BEND_CENTER = 8192
PITCH_BEND_MAX = 16383
PITCH_BEND_RANGE_SEMITONES = 12
# Basic Pitch reports bends in units of 1/3 semitone (3 contour bins per semitone).
PITCH_BEND_UNITS_PER_SEMITONE = 3.0


@dataclass(frozen=True)
class MidiNote:
    """A note event expressed in MIDI ticks.

    ``pitch_bends`` optionally carries a per-note pitch contour: evenly-spaced
    samples across the note's duration, in units of 1/3 semitone (Basic Pitch's
    native unit -- 3 contour bins per semitone). ``None`` means no bend data (the
    note sits on its nominal pitch). Used by :func:`write_midi` to emit MIDI
    pitch-bend events so expressive slides/vibrato survive into the exported file.
    """

    pitch: int
    start_tick: int
    duration_ticks: int
    velocity: int = 80
    pitch_bends: tuple[int, ...] | None = None


def _varlen(value: int) -> bytes:
    """Encode an integer as a MIDI variable-length quantity."""

    if value < 0:
        raise ValueError("variable-length values must be non-negative")

    buffer = value & 0x7F
    value >>= 7
    while value:
        buffer <<= 8
        buffer |= (value & 0x7F) | 0x80
        value >>= 7

    encoded = bytearray()
    while True:
        encoded.append(buffer & 0xFF)
        if buffer & 0x80:
            buffer >>= 8
        else:
            break
    return bytes(encoded)


def _bend_units_to_wheel(bend_units: int) -> int:
    """Convert a 1/3-semitone bend value to a 14-bit MIDI pitch-wheel value.

    Centered at 8192; scaled so PITCH_BEND_RANGE_SEMITONES maps to the full range.
    Values beyond the configured range are clamped rather than wrapping.
    """

    semitones = bend_units / PITCH_BEND_UNITS_PER_SEMITONE
    fraction = semitones / PITCH_BEND_RANGE_SEMITONES  # -1.0 .. 1.0 at the extremes
    wheel = PITCH_BEND_CENTER + round(fraction * (PITCH_BEND_CENTER - 1))
    return max(0, min(PITCH_BEND_MAX, wheel))


def _bend_event(tick: int, wheel: int, channel: int = 0) -> tuple[int, int, bytes]:
    """Build a pitch-bend MIDI event (status 0xE0).

    Sort order 1: at a shared tick, note-offs (0) fire first, then bends, then
    note-ons (2), so a departing note releases before the arriving note bends/starts.
    """

    lsb = wheel & 0x7F
    msb = (wheel >> 7) & 0x7F
    return (tick, 1, bytes((0xE0 | channel, lsb, msb)))


def _append_bend_events(events: list[tuple[int, int, bytes]], note: MidiNote, start_tick: int, duration_ticks: int) -> None:
    """Emit pitch-bend events spread across a note, plus a reset to center at its end."""

    bends = note.pitch_bends
    if not bends:
        return
    n = len(bends)
    # Spread the bend samples evenly from the note's start across its duration.
    span = max(1, duration_ticks)
    for i, bend_units in enumerate(bends):
        offset = round(i * span / n) if n > 1 else 0
        tick = start_tick + min(offset, span - 1)
        events.append(_bend_event(tick, _bend_units_to_wheel(int(bend_units))))
    # Reset the wheel to center at note-off so the bend does not leak to later notes.
    events.append(_bend_event(start_tick + duration_ticks, PITCH_BEND_CENTER))


def write_midi(path: Path, notes: list[MidiNote]) -> None:
    """Write a type-0 Standard MIDI File containing the supplied notes.

    Notes carrying ``pitch_bends`` also emit MIDI pitch-bend events across their
    duration (channel 0), with the bend range set to PITCH_BEND_RANGE_SEMITONES via
    RPN 0 and the wheel reset to center at each note-off. Bends are per-channel, so
    they are ideal for monophonic material; overlapping notes share one bend.
    """

    events: list[tuple[int, int, bytes]] = []
    any_bends = False
    for note in notes:
        pitch = max(0, min(127, int(note.pitch)))
        velocity = max(1, min(127, int(note.velocity)))
        start_tick = max(0, int(note.start_tick))
        duration_ticks = max(1, int(note.duration_ticks))
        # Bend events use sort-order 0 so they land just before the note-on at the
        # same tick, and the center-reset at note-off precedes the next note-on.
        _append_bend_events(events, note, start_tick, duration_ticks)
        if note.pitch_bends:
            any_bends = True
        events.append((start_tick, 2, bytes((0x90, pitch, velocity))))
        events.append((start_tick + duration_ticks, 0, bytes((0x80, pitch, 0))))

    events.sort(key=lambda event: (event[0], event[1], event[2]))

    track = bytearray()
    # 120 BPM tempo metadata keeps the file conventional for MIDI readers.
    # TICKS_PER_SECOND must match this tempo and TICKS_PER_BEAT, otherwise
    # rendered MIDI plays at the wrong speed.
    track.extend(_varlen(0))
    track.extend(b"\xff\x51\x03" + TEMPO_MICROSECONDS_PER_BEAT.to_bytes(3, "big"))

    # Set the pitch-bend range (RPN 0) on channel 0 so readers interpret the wheel
    # values in the same semitone range we scaled them to. Only needed when any
    # note actually carries bends.
    if any_bends:
        for data in (
            (0xB0, 0x65, 0x00),  # RPN MSB = 0
            (0xB0, 0x64, 0x00),  # RPN LSB = 0 -> pitch-bend range
            (0xB0, 0x06, PITCH_BEND_RANGE_SEMITONES & 0x7F),  # data entry MSB = semitones
            (0xB0, 0x26, 0x00),  # data entry LSB = 0 cents
        ):
            track.extend(_varlen(0))
            track.extend(bytes(data))

    previous_tick = 0
    for absolute_tick, _order, payload in events:
        track.extend(_varlen(absolute_tick - previous_tick))
        track.extend(payload)
        previous_tick = absolute_tick

    track.extend(_varlen(0))
    track.extend(b"\xff\x2f\x00")

    header = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + TICKS_PER_BEAT.to_bytes(2, "big")
    track_chunk = b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + track_chunk)
