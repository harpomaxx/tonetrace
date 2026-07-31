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
