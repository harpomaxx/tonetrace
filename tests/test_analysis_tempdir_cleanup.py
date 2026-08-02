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


def test_analysis_worker_emits_compact_offset_heatmap_without_document_conversion(tmp_path, monkeypatch):
    import notegrabber.gui.analysis_worker as worker_module
    from notegrabber.gui.analysis_worker import AnalysisRequest, AnalysisWorker
    from notegrabber.heatmap import HeatmapData
    from notegrabber.midi import MidiNote

    np = pytest.importorskip("numpy")
    matrix = np.zeros((2, 3), dtype=np.float32)
    compact = HeatmapData(
        backend="cqt",
        midi_notes=[60, 61, 62],
        frame_times=[0.0, 0.1],
        activations=matrix,
        sample_rate=100,
        hop_size=10,
        window_size=20,
    )

    def fake_analyze(*_args, **_kwargs):
        return [MidiNote(pitch=60, start_tick=0, duration_ticks=10, velocity=80)], compact

    def fake_extract(_input_audio, output_wav, *, start_seconds, duration_seconds):
        assert start_seconds == 12.5
        assert duration_seconds == 1.0
        return output_wav

    monkeypatch.setattr(worker_module, "analyze_wav_to_midi_with_heatmap_data", fake_analyze)
    monkeypatch.setattr(worker_module, "_extract_audio_range", fake_extract)

    request = AnalysisRequest(
        audio_path=tmp_path / "song.wav",
        backend="cqt",
        render_midi=False,
        threshold=0.5,
        onset_threshold=0.5,
        frame_threshold=0.3,
        min_duration_seconds=0.05,
        range_start_seconds=12.5,
        range_duration_seconds=1.0,
    )
    worker = AnalysisWorker(request)
    results: list[object] = []
    worker.finished.connect(results.append)

    worker.run()

    assert len(results) == 1
    result = results[0]
    assert result.heatmap is not compact
    assert result.heatmap.frame_times == [12.5, 12.6]
    assert result.heatmap.activation_matrix() is matrix
    assert result.heatmap_path is None
    assert result.notes[0].start_seconds == pytest.approx(12.5)
    assert result.analysis_start_seconds == pytest.approx(12.5)


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
    monkeypatch.setattr(worker_module, "analyze_wav_to_midi_with_heatmap_data", boom)

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
