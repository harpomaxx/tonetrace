"""Exporting notes must produce a valid file for MIDI and each audio format."""

from __future__ import annotations

import wave

import pytest

from notegrabber.export import export_notes, format_for_path
from notegrabber.midi import MidiNote, TICKS_PER_SECOND


def _notes():
    return [
        MidiNote(pitch=60, start_tick=0, duration_ticks=TICKS_PER_SECOND // 2, velocity=90),
        MidiNote(pitch=64, start_tick=TICKS_PER_SECOND // 2, duration_ticks=TICKS_PER_SECOND // 2, velocity=90),
    ]


@pytest.mark.parametrize(
    "name,ext",
    [("out.mid", "mid"), ("out.midi", "mid"), ("out.wav", "wav"), ("out.MP3", "mp3"), ("out.flac", "flac")],
)
def test_format_for_path(tmp_path, name, ext):
    assert format_for_path(tmp_path / name) == ext


def test_format_for_path_rejects_unknown(tmp_path):
    assert format_for_path(tmp_path / "out.xyz") is None


def test_export_midi_writes_smf(tmp_path):
    out = tmp_path / "song.mid"
    result, error = export_notes(_notes(), out)
    assert error is None
    assert result == out
    assert out.read_bytes().startswith(b"MThd")


def test_export_wav_is_playable(tmp_path):
    pytest.importorskip("numpy")
    out = tmp_path / "song.wav"
    result, error = export_notes(_notes(), out)
    assert error is None, error
    assert out.exists()
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() > 0  # actual audio was synthesized


@pytest.mark.parametrize("ext", ["mp3", "flac", "ogg"])
def test_export_compressed_audio(tmp_path, ext):
    pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    if ext.upper() not in sf.available_formats():
        pytest.skip(f"{ext} not supported by this libsndfile build")
    out = tmp_path / f"song.{ext}"
    result, error = export_notes(_notes(), out)
    assert error is None, error
    assert out.exists() and out.stat().st_size > 0
    # soundfile can read back what it wrote.
    data, sr = sf.read(str(out))
    assert sr > 0 and len(data) > 0


def test_export_unsupported_extension_returns_message(tmp_path):
    result, error = export_notes(_notes(), tmp_path / "song.xyz")
    assert result is None
    assert error and "Unsupported" in error


def test_audio_export_trims_leading_silence(tmp_path):
    """A range analysis places notes at their absolute time (e.g. 40s). Audio export
    must shift to t=0 so the clip is not mostly leading silence (the 'no sound' bug).
    """

    pytest.importorskip("numpy")
    import numpy as np

    offset_ticks = 40 * TICKS_PER_SECOND
    notes = [
        MidiNote(pitch=60, start_tick=offset_ticks, duration_ticks=TICKS_PER_SECOND // 2, velocity=90),
        MidiNote(pitch=64, start_tick=offset_ticks + TICKS_PER_SECOND // 2, duration_ticks=TICKS_PER_SECOND // 2, velocity=90),
    ]
    out = tmp_path / "range.wav"
    result, error = export_notes(notes, out)
    assert error is None, error

    with wave.open(str(out), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        raw = w.readframes(frames)
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    duration = frames / rate
    # Clip is a couple of seconds, not 40+; audio is present in the first second.
    assert duration < 5.0, f"export kept leading silence: {duration:.1f}s"
    assert np.max(np.abs(samples[:rate])) > 0.05, "no audio at the start of the clip"


def test_midi_export_keeps_absolute_timeline(tmp_path):
    """MIDI export must NOT shift notes -- a .mid should align to the original song."""

    from notegrabber.midi import write_midi

    offset_ticks = 40 * TICKS_PER_SECOND
    notes = [MidiNote(pitch=60, start_tick=offset_ticks, duration_ticks=TICKS_PER_SECOND, velocity=90)]
    exported = tmp_path / "a.mid"
    reference = tmp_path / "b.mid"
    export_notes(notes, exported)
    write_midi(reference, notes)
    # Byte-identical to a direct write: no shifting applied on the MIDI path.
    assert exported.read_bytes() == reference.read_bytes()
