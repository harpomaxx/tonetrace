"""Analysis work directories must not leak to /tmp (issue #31).

Each analysis renders heatmap.json + a preview WAV (tens of MB) into a fresh
mkdtemp work dir. The window owns that dir: it removes the previous one when a
newer analysis supersedes it, and removes the current one on close. A failed
analysis cleans up its own dir since no result reaches the window.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_result(work_dir: Path, notes):
    """Build an AnalysisResult backed by a real temp dir with placeholder files."""

    from notegrabber.gui.analysis_worker import AnalysisResult
    from notegrabber.gui.state import GuiHeatmap

    (work_dir / "heatmap.json").write_text("{}", encoding="utf-8")
    (work_dir / "analysis.mid").write_bytes(b"")
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60],
        frame_times=[0.0],
        activations=[[0.0]],
        sample_rate=22_050,
        hop_size=512,
        window_size=2_048,
    )
    return AnalysisResult(
        audio_path=work_dir / "song.wav",
        backend="basic-pitch",
        midi_path=work_dir / "analysis.mid",
        heatmap_path=work_dir / "heatmap.json",
        rendered_midi_wav=None,
        render_error=None,
        notes=list(notes),
        heatmap=heatmap,
        work_dir=work_dir,
    )


def test_superseded_analysis_dir_is_removed_and_current_kept(tmp_path):
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    first_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-", dir=tmp_path))
    second_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-", dir=tmp_path))
    note = GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.3, velocity=90)

    window._analysis_finished(_make_result(first_dir, [note]))
    assert window._analysis_dir == first_dir
    assert first_dir.exists()

    # A second analysis supersedes the first: the first dir is removed, the
    # second becomes current and survives.
    window._analysis_finished(_make_result(second_dir, [note]))
    assert not first_dir.exists()
    assert window._analysis_dir == second_dir
    assert second_dir.exists()

    window.close()
    app.processEvents()


def test_close_removes_current_analysis_dir(tmp_path):
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    work_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-", dir=tmp_path))
    note = GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.3, velocity=90)
    window._analysis_finished(_make_result(work_dir, [note]))
    assert work_dir.exists()

    window.close()
    app.processEvents()
    assert not work_dir.exists()
    assert window._analysis_dir is None


def test_worker_removes_work_dir_when_analysis_fails(tmp_path, monkeypatch):
    import notegrabber.gui.analysis_worker as worker_module
    from notegrabber.gui.analysis_worker import AnalysisRequest, AnalysisWorker

    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        kwargs.setdefault("dir", str(tmp_path))
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    def boom(*_args, **_kwargs):
        raise RuntimeError("analysis exploded")

    monkeypatch.setattr(worker_module.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(worker_module, "analyze_wav_to_midi", boom)

    request = AnalysisRequest(
        audio_path=tmp_path / "song.wav",
        backend="basic-pitch",
        render_midi=False,
        threshold=0.5,
        onset_threshold=0.5,
        frame_threshold=0.3,
        min_duration_seconds=0.05,
    )
    worker = AnalysisWorker(request)

    failures: list[str] = []
    worker.failed.connect(failures.append)
    worker.run()

    assert failures and "analysis exploded" in failures[0]
    assert created, "worker should have created a work dir"
    assert not created[0].exists(), "failed analysis must remove its work dir"
