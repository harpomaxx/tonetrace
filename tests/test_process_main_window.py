from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _wait_until(app, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


@pytest.mark.gui
def test_cancel_blocked_analysis_restores_busy_and_preserves_previous_result() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.process_jobs import JobProgress, ProcessJob
    from notegrabber.gui.state import GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    previous = [GuiMidiNote(pitch=60, start_seconds=1.0, duration_seconds=0.5, velocity=90)]
    window._set_display_notes(previous)
    window.state.extracted_notes = previous
    window.state.backend = "simple"
    window.state.threshold = 0.11

    window.controls.backend_combo.setCurrentText("cqt")
    window.controls.cqt_threshold.setValue(91)
    window._analysis_generation += 1
    generation = window._analysis_generation
    job = ProcessJob("test-block", parent=window, kill_after_ms=50)
    window.analysis_job = job
    job.progress.connect(lambda progress, generation=generation, job=job: window._analysis_progress(generation, job, progress))
    job.cancelled.connect(lambda generation=generation: window._analysis_cancelled(generation))
    job.done.connect(lambda generation=generation, job=job: window._analysis_job_done(generation, job))
    window._analysis_settings_by_generation[generation] = ("cqt", 0.91, 0.5, 0.5, 0.05)
    window._refresh_job_ui()
    job.start()
    assert _wait_until(app, lambda: job.is_running)

    window._cancel_current_job()

    assert _wait_until(app, lambda: window.analysis_job is None)
    assert window.state.current_notes == previous
    assert window.state.backend == "simple"
    assert window.state.threshold == pytest.approx(0.11)
    assert window.controls.analyze_button.isEnabled()
    assert "cancelled" in window.statusBar().currentMessage().lower()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_same_path_audio_load_stale_generation_and_progress_are_ignored(tmp_path) -> None:
    import pickle

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.audio_load_worker import WaveformReady
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.process_jobs import JobArtifact, JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    path = tmp_path / "song.wav"
    window.state.audio_path = path
    old_job = ProcessJob("test-noisy", parent=window)
    new_job = ProcessJob("test-noisy", parent=window)
    window.audio_load_job = new_job
    window._audio_load_generation = 2
    artifact_path = old_job.work_dir / "waveform.pkl"
    with artifact_path.open("wb") as handle:
        pickle.dump(WaveformReady(audio_path=path, samples=[0.1], sample_rate=1, duration_seconds=1.0), handle)

    window._audio_load_progress(1, old_job, JobProgress(kind="audio-load", stage="decoding", message="stale", completed=1, total=3))
    window._audio_load_artifact(1, old_job, JobArtifact(kind="audio-load", name="waveform", path=artifact_path))

    assert window.waveform.samples == []
    assert window.statusBar().currentMessage() != "stale"
    old_job.cleanup()
    new_job.cleanup()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_analysis_busy_completion_leaves_audio_cancel_enabled() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.process_jobs import ProcessJob

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.show()
    app.processEvents()
    analysis_job = ProcessJob("test-noisy", parent=window)
    audio_job = ProcessJob("test-noisy", parent=window)
    window.analysis_job = analysis_job
    window.audio_load_job = audio_job
    window._refresh_job_ui()
    assert not window.controls.analyze_button.isEnabled()
    assert window.controls.cancel_button.isEnabled()
    assert window.progress_bar.isVisible()

    window._analysis_job_done(window._analysis_generation, analysis_job)
    # This synthetic test invokes only the window's done handler; in production
    # ProcessJob._emit_done_once() performs this cleanup immediately afterward.
    analysis_job.cleanup()
    assert not analysis_job.work_dir.exists()

    assert window.controls.analyze_button.isEnabled()
    assert window.controls.cancel_button.isEnabled()
    assert window.progress_bar.isVisible()
    audio_job.cleanup()
    window.audio_load_job = None
    window._refresh_job_ui()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_close_during_blocked_process_defers_then_kills_and_cleans() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.process_jobs import JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    job = ProcessJob("test-ignore-terminate", parent=window, kill_after_ms=600)
    work_dir = job.work_dir
    progress: list[JobProgress] = []
    window.audio_load_job = job
    generation = window._audio_load_generation
    job.progress.connect(progress.append)
    job.done.connect(lambda generation=generation, job=job: window._audio_load_done(generation, job))
    job.start()
    assert _wait_until(app, lambda: any(item.stage == "blocked" for item in progress))

    window.close()

    assert window._closing is True
    assert not window.isVisible()
    assert window in MainWindow._deferred_close_windows
    assert not window._final_close
    assert _wait_until(app, lambda: job.is_finished, timeout=4.0)
    assert _wait_until(app, lambda: window._final_close, timeout=3.0)
    assert window not in MainWindow._deferred_close_windows
    assert not work_dir.exists()


@pytest.mark.gui
def test_progress_priority_ignores_background_preview_under_analysis() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.process_jobs import JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    analysis_job = ProcessJob("test-noisy", parent=window)
    preview_job = ProcessJob("test-noisy", parent=window)
    window.analysis_job = analysis_job
    window._analysis_generation = 1
    window.preview_jobs.append((1, preview_job))
    window._preview_request_id = 1

    window._analysis_progress(1, analysis_job, JobProgress(kind="analysis", stage="transcribing", message="analysis wins"))
    window._preview_progress(1, preview_job, JobProgress(kind="preview", stage="rendering", message="preview loses", completed=1, total=1))

    assert window.statusBar().currentMessage() == "analysis wins"
    analysis_job.cleanup()
    preview_job.cleanup()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_progress_bar_is_themed_and_determinate_progress_visible() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.process_jobs import JobProgress
    from notegrabber.gui.theme import THEMES, build_stylesheet

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.show()
    app.processEvents()
    assert "QProgressBar" in window.styleSheet()
    assert all("QProgressBar" in build_stylesheet(theme) for theme in THEMES.values())

    window._job_progress(JobProgress(kind="analysis", stage="finalizing", message="Almost done", completed=2, total=3), priority="analysis")

    assert window.progress_bar.isVisible()
    assert window.progress_bar.maximum() == 3
    assert window.progress_bar.value() == 2
    assert window.statusBar().currentMessage() == "Almost done"
    window.close()
    app.processEvents()
