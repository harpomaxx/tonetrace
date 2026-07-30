"""Tests for the MIDI-to-WAV render backends (native default, TiMidity opt-in)."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

mido = pytest.importorskip("mido")
pytest.importorskip("numpy")

from notegrabber import midi_render
from notegrabber.midi_render import render_midi_to_wav


def _write_triad_midi(path: Path) -> None:
    """Write a tiny 3-note (C-E-G) MIDI file for rendering."""

    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    for note in (60, 64, 67):
        track.append(mido.Message("note_on", note=note, velocity=90, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=480))
    mid.save(str(path))


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def test_native_is_the_default_backend(tmp_path, monkeypatch):
    """With no env var set, rendering uses the native synth and writes a WAV."""

    monkeypatch.delenv(midi_render.SYNTH_ENV_VAR, raising=False)
    midi = tmp_path / "t.mid"
    wav = tmp_path / "t.wav"
    _write_triad_midi(midi)

    result, error = render_midi_to_wav(midi, wav)

    assert error is None
    assert result == wav and wav.exists()
    assert _wav_seconds(wav) > 0.0


def test_timidity_request_falls_back_to_native_when_missing(tmp_path, monkeypatch):
    """Requesting TiMidity when it is not on PATH still produces audio (native fallback)."""

    monkeypatch.setenv(midi_render.SYNTH_ENV_VAR, "timidity")
    monkeypatch.setattr(midi_render.shutil, "which", lambda name: None)
    midi = tmp_path / "t.mid"
    wav = tmp_path / "t.wav"
    _write_triad_midi(midi)

    result, error = render_midi_to_wav(midi, wav)

    assert error is None
    assert result == wav and wav.exists()


def test_native_render_reports_error_on_unreadable_midi(tmp_path, monkeypatch):
    """A non-MIDI input is reported as an error, not a crash."""

    monkeypatch.delenv(midi_render.SYNTH_ENV_VAR, raising=False)
    bad = tmp_path / "not.mid"
    bad.write_bytes(b"this is not a midi file")
    wav = tmp_path / "t.wav"

    result, error = render_midi_to_wav(bad, wav)

    assert result is None
    assert error and "MIDI" in error
