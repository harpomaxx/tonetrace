"""One decode per file open feeds both the waveform preview and the overview.

Guards issue #33: AudioLoadWorker must decode the audio exactly once and emit
both the waveform preview and the pitch overview from that shared buffer, rather
than the two old workers each decoding the whole file independently.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("numpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _tone(sample_rate: int, seconds: float, freq: float = 440.0):
    import numpy as np

    t = np.linspace(0.0, seconds, int(sample_rate * seconds), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_decodes_once_and_emits_both_payloads(monkeypatch, tmp_path):
    import librosa

    from notegrabber.gui.audio_load_worker import (
        AudioLoadWorker,
        OverviewReady,
        WaveformReady,
    )

    calls: list[int] = []

    def fake_load(path, sr=None, mono=True):
        calls.append(1)
        return _tone(sr, 3.0), sr

    monkeypatch.setattr(librosa, "load", fake_load)

    path = tmp_path / "song.mp3"
    worker = AudioLoadWorker(path)

    waveforms: list[WaveformReady] = []
    overviews: list[OverviewReady] = []
    done: list[bool] = []
    worker.waveform_ready.connect(waveforms.append)
    worker.overview_ready.connect(overviews.append)
    worker.done.connect(lambda: done.append(True))

    worker.run()  # synchronous: signals fire inline

    # Decoded exactly once, not once per consumer.
    assert sum(calls) == 1
    # Both payloads produced from the shared buffer, tagged with the same path.
    assert len(waveforms) == 1 and waveforms[0].audio_path == path
    assert len(overviews) == 1 and overviews[0].audio_path == path
    # Duration derived from the decoded length (no extra get_duration open).
    assert waveforms[0].duration_seconds == pytest.approx(3.0, abs=0.05)
    assert waveforms[0].samples  # non-empty preview
    assert overviews[0].overview.frame_count > 0
    assert done == [True]


def test_decode_failure_reports_both_stages(monkeypatch, tmp_path):
    import librosa

    from notegrabber.gui.audio_load_worker import AudioLoadWorker

    def boom(*_args, **_kwargs):
        raise RuntimeError("cannot decode")

    monkeypatch.setattr(librosa, "load", boom)

    worker = AudioLoadWorker(tmp_path / "bad.mp3")
    failures: list[tuple] = []
    done: list[bool] = []
    worker.failed.connect(lambda p, stage, msg: failures.append((stage, msg)))
    worker.done.connect(lambda: done.append(True))

    worker.run()

    stages = {stage for stage, _ in failures}
    assert stages == {"waveform", "overview"}  # neither view is left hanging
    assert all("cannot decode" in msg for _, msg in failures)
    assert done == [True]


def test_overview_failure_still_delivers_waveform(monkeypatch, tmp_path):
    """A CQT/overview error must not sink the already-computed waveform preview."""

    import librosa

    import notegrabber.gui.audio_load_worker as worker_module
    from notegrabber.gui.audio_load_worker import AudioLoadWorker, WaveformReady

    monkeypatch.setattr(librosa, "load", lambda path, sr=None, mono=True: (_tone(sr, 2.0), sr))

    def boom(*_args, **_kwargs):
        raise ValueError("overview blew up")

    monkeypatch.setattr(worker_module, "build_overview_from_samples", boom)

    worker = AudioLoadWorker(tmp_path / "song.wav")
    waveforms: list[WaveformReady] = []
    failures: list[tuple] = []
    worker.waveform_ready.connect(waveforms.append)
    worker.failed.connect(lambda p, stage, msg: failures.append((stage, msg)))

    worker.run()

    assert len(waveforms) == 1  # waveform still delivered
    assert [stage for stage, _ in failures] == ["overview"]  # only overview failed
