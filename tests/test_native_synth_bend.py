"""The native synth must audibly bend pitch when a note carries a bend contour."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("mido")

import numpy as np

from notegrabber.midi import TICKS_PER_SECOND, MidiNote, write_midi
from notegrabber.native_synth import render_midi_to_wav_native


def _peak_freq(signal: np.ndarray, sr: int) -> float:
    windowed = signal * np.hanning(len(signal))
    spectrum = np.abs(np.fft.rfft(windowed))
    return float(np.fft.rfftfreq(len(windowed), 1 / sr)[int(np.argmax(spectrum))])


def _render(tmp_path: Path, note: MidiNote) -> tuple[np.ndarray, int]:
    midi = tmp_path / "n.mid"
    wav = tmp_path / "n.wav"
    write_midi(midi, [note])
    path, err = render_midi_to_wav_native(midi, wav)
    assert path is not None and err is None, err
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="int16").astype(float)
    return raw, sr


def test_bend_contour_raises_pitch_over_the_note(tmp_path):
    """A note bending +2 semitones ends ~2 semitones above where it started."""

    dur_ticks = round(1.5 * TICKS_PER_SECOND)
    # Hold at 0 for the first third, then rise to +2 semitones (6 units of 1/3).
    bends = (0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 6, 6, 6, 6)
    raw, sr = _render(tmp_path, MidiNote(pitch=57, start_tick=0, duration_ticks=dur_ticks, pitch_bends=bends))

    win = int(0.15 * sr)
    f_early = _peak_freq(raw[int(0.05 * sr) : int(0.05 * sr) + win], sr)
    f_late = _peak_freq(raw[int(1.25 * sr) : int(1.25 * sr) + win], sr)
    semitones = 12 * np.log2(f_late / f_early)
    assert 1.5 < semitones < 2.5, f"expected ~+2 semitones, got {semitones:.2f}"


def test_note_without_bend_holds_a_steady_pitch(tmp_path):
    """A plain note keeps essentially the same pitch start to end."""

    dur_ticks = round(1.5 * TICKS_PER_SECOND)
    raw, sr = _render(tmp_path, MidiNote(pitch=57, start_tick=0, duration_ticks=dur_ticks))

    win = int(0.15 * sr)
    f_early = _peak_freq(raw[int(0.05 * sr) : int(0.05 * sr) + win], sr)
    f_late = _peak_freq(raw[int(1.25 * sr) : int(1.25 * sr) + win], sr)
    semitones = abs(12 * np.log2(f_late / f_early))
    assert semitones < 0.3, f"pitch drifted {semitones:.2f} semitones on a plain note"
