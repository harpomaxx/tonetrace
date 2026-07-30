"""Regression: opening a new file while audio is playing must not hang.

Calling QMediaPlayer.setSource() on a player that is actively playing can deadlock
the audio backend. load_audio must stop playback (and cancel any queued preview
render) before swapping sources.
"""

from __future__ import annotations

import math
import os
import struct
import wave
from pathlib import Path

import pytest


def _write_sine_wav(path: Path, seconds: float = 0.5, freq: float = 440.0, sr: int = 8000) -> None:
    frames = bytearray()
    for i in range(int(seconds * sr)):
        value = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / sr))
        frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def test_load_audio_stops_playback_before_swapping_source(tmp_path) -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    _write_sine_wav(first)
    _write_sine_wav(second, freq=660.0)

    window.load_audio(first)
    app.processEvents()

    # Put the window into a "playing" state and queue a preview render.
    window.playback_mode = "both"
    window.playback_timer.start()
    window.preview_debounce_timer.start()
    assert window.playback_timer.isActive()
    assert window.preview_debounce_timer.isActive()

    # Opening a new file must tear playback down first (this is what previously hung).
    window.load_audio(second)
    app.processEvents()

    assert window.playback_mode == "stopped"
    assert not window.playback_timer.isActive()
    assert not window.preview_debounce_timer.isActive()
    assert window.state.audio_path == second.expanduser().resolve()

    window.close()
    app.processEvents()
