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
def test_process_job_cancel_terminates_blocked_child_once() -> None:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QProcess

    from notegrabber.gui.process_jobs import JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-block", kill_after_ms=100)
    terminals: list[str] = []
    progress: list[JobProgress] = []
    job.progress.connect(progress.append)
    job.cancelled.connect(lambda: terminals.append("cancelled"))
    job.failed.connect(lambda _message: terminals.append("failed"))
    job.succeeded.connect(lambda _result: terminals.append("succeeded"))
    job.start()

    assert _wait_until(app, lambda: any(item.stage == "blocked" for item in progress))
    job.cancel()

    assert _wait_until(app, lambda: job.is_finished)
    assert terminals == ["cancelled"]
    assert job.process.state() == QProcess.ProcessState.NotRunning
    assert not job.work_dir.exists()


@pytest.mark.gui
def test_process_job_kill_escalates_when_child_ignores_terminate() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-ignore-terminate", kill_after_ms=50)
    terminals: list[str] = []
    progress: list[JobProgress] = []
    job.progress.connect(progress.append)
    job.cancelled.connect(lambda: terminals.append("cancelled"))
    job.start()
    # Wait for the framed blocked progress, which is emitted only after the child
    # has installed SIGTERM-ignore for this escalation test.
    assert _wait_until(app, lambda: any(item.stage == "blocked" for item in progress))

    job.cancel()

    assert _wait_until(app, lambda: job.is_finished, timeout=3.0)
    assert terminals == ["cancelled"]
    assert not job.work_dir.exists()


@pytest.mark.gui
def test_process_job_ignores_noisy_stdout_and_loads_result() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-noisy")
    progress: list[JobProgress] = []
    results: list[object] = []
    terminals: list[str] = []
    job.progress.connect(progress.append)
    job.succeeded.connect(lambda result: (terminals.append("succeeded"), results.append(result)))
    job.failed.connect(lambda _message: terminals.append("failed"))
    job.cancelled.connect(lambda: terminals.append("cancelled"))

    job.start()

    assert _wait_until(app, lambda: job.is_finished)
    assert terminals == ["succeeded"]
    assert results == [{"ok": True}]
    assert [item.stage for item in progress] == ["noise"]
    assert not (job.work_dir / "request.pkl").exists()
    assert not (job.work_dir / "result.pkl").exists()


@pytest.mark.gui
def test_process_job_ignores_malformed_framed_json_shapes() -> None:
    from notegrabber.gui.process_jobs import PROTOCOL_PREFIX, ProcessJob

    job = ProcessJob("test-noisy")
    events: list[str] = []
    job.progress.connect(lambda _value: events.append("progress"))
    job.failed.connect(lambda _value: events.append("failed"))

    job._handle_stdout_line(PROTOCOL_PREFIX + "[]")
    job._handle_stdout_line(PROTOCOL_PREFIX + '{"event":[]}')
    job._handle_stdout_line(PROTOCOL_PREFIX + '{"event":null}')

    assert events == []
    job.cancel()
    assert not job.work_dir.exists()


@pytest.mark.gui
def test_process_job_schedules_qobject_delete_after_terminal_event() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-noisy")
    destroyed: list[bool] = []
    job.destroyed.connect(lambda _obj=None: destroyed.append(True))

    job.start()

    assert _wait_until(app, lambda: job.is_finished)
    assert _wait_until(app, lambda: destroyed)


@pytest.mark.gui
def test_process_job_cancel_before_start_is_terminal_and_blocks_later_start() -> None:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QProcess

    from notegrabber.gui.process_jobs import ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-block")
    terminals: list[str] = []
    done: list[bool] = []
    job.cancelled.connect(lambda: terminals.append("cancelled"))
    job.failed.connect(lambda _message: terminals.append("failed"))
    job.done.connect(lambda: done.append(True))

    job.cancel()
    job.start()
    app.processEvents()

    assert terminals == ["cancelled"]
    assert done == [True]
    assert job.is_finished
    assert job.process.state() == QProcess.ProcessState.NotRunning
    assert not job.work_dir.exists()


@pytest.mark.gui
def test_process_job_failed_to_start_emits_failed_once_and_cleans_work_dir() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-noisy")
    work_dir = job.work_dir
    job.process.setProgram(str(work_dir / "missing-python"))
    terminals: list[str] = []
    done: list[bool] = []
    job.failed.connect(lambda message: terminals.append(message))
    job.cancelled.connect(lambda: terminals.append("cancelled"))
    job.succeeded.connect(lambda _result: terminals.append("succeeded"))
    job.done.connect(lambda: done.append(True))

    job.start()

    assert _wait_until(app, lambda: job.is_finished)
    assert len(terminals) == 1
    assert "process error" in terminals[0]
    assert done == [True]
    assert not work_dir.exists()


@pytest.mark.gui
def test_non_startup_process_error_waits_for_child_exit_before_cleanup() -> None:
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import JobProgress, ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-ignore-terminate", kill_after_ms=100)
    progress: list[JobProgress] = []
    terminals: list[str] = []
    job.progress.connect(progress.append)
    job.failed.connect(terminals.append)
    job.start()
    assert _wait_until(app, lambda: any(item.stage == "blocked" for item in progress))

    job._process_error(QProcess.ProcessError.WriteError)

    assert not job.is_finished
    assert job.work_dir.exists()
    assert _wait_until(app, lambda: job.is_finished)
    assert len(terminals) == 1
    assert "WriteError" in terminals[0]
    assert not job.work_dir.exists()


@pytest.mark.gui
def test_process_job_success_without_required_result_fails_and_cleans() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-success-no-result")
    terminals: list[str] = []
    job.failed.connect(lambda message: terminals.append(message))
    job.succeeded.connect(lambda _result: terminals.append("succeeded"))

    job.start()

    assert _wait_until(app, lambda: job.is_finished)
    assert terminals == ["test-success-no-result job reported success without a result"]
    assert not job.work_dir.exists()


@pytest.mark.gui
def test_process_job_rejects_unsafe_artifact_path_and_cleans() -> None:
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.process_jobs import ProcessJob

    app = QApplication.instance() or QApplication([])
    job = ProcessJob("test-unsafe-artifact", kill_after_ms=50)
    terminals: list[str] = []
    artifacts: list[object] = []
    job.artifact.connect(artifacts.append)
    job.failed.connect(lambda message: terminals.append(message))

    job.start()

    assert _wait_until(app, lambda: job.is_finished)
    assert artifacts == []
    assert terminals == ["Child emitted an unsafe artifact path"]
    assert not job.work_dir.exists()
