"""Minimal Standard MIDI File writer used by the CLI baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TICKS_PER_BEAT = 480
TEMPO_MICROSECONDS_PER_BEAT = 500_000  # 120 BPM
TICKS_PER_SECOND = round(TICKS_PER_BEAT * 1_000_000 / TEMPO_MICROSECONDS_PER_BEAT)


@dataclass(frozen=True)
class MidiNote:
    """A note event expressed in MIDI ticks."""

    pitch: int
    start_tick: int
    duration_ticks: int
    velocity: int = 80


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


def write_midi(path: Path, notes: list[MidiNote]) -> None:
    """Write a type-0 Standard MIDI File containing the supplied notes."""

    events: list[tuple[int, int, bytes]] = []
    for note in notes:
        pitch = max(0, min(127, int(note.pitch)))
        velocity = max(1, min(127, int(note.velocity)))
        start_tick = max(0, int(note.start_tick))
        duration_ticks = max(1, int(note.duration_ticks))
        events.append((start_tick, 1, bytes((0x90, pitch, velocity))))
        events.append((start_tick + duration_ticks, 0, bytes((0x80, pitch, 0))))

    events.sort(key=lambda event: (event[0], event[1], event[2]))

    track = bytearray()
    # 120 BPM tempo metadata keeps the file conventional for MIDI readers.
    # TICKS_PER_SECOND must match this tempo and TICKS_PER_BEAT, otherwise
    # rendered MIDI plays at the wrong speed.
    track.extend(_varlen(0))
    track.extend(b"\xff\x51\x03" + TEMPO_MICROSECONDS_PER_BEAT.to_bytes(3, "big"))

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
