"""QProcess-backed GUI job controller and stdout protocol helpers.

Opaque audio/ML/synthesis calls run in isolated Python child processes so the
GUI can cancel them without terminating a Qt/Python worker thread in-process.

The request/result/artifact payloads use pickle intentionally as trusted local
IPC between the GUI process and ``sys.executable -m notegrabber.gui.job_runner``.
They are never a file-format or network boundary; only progress/control events
cross stdout as framed JSON lines.
"""

from __future__ import annotations

import json
import os
import pickle
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - imported only when GUI deps are installed
    from PySide6.QtCore import QObject, QProcess, QTimer, Signal
except ModuleNotFoundError:  # pragma: no cover
    QObject = object  # type: ignore[assignment,misc]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *_args: object) -> None:
            pass


PROTOCOL_PREFIX = "NOTEGRABBER_JOB_V1 "
REQUEST_FILE = "request.pkl"
RESULT_FILE = "result.pkl"
REQUIRED_RESULT_KINDS = {"audio-load", "analysis", "preview", "test-noisy", "test-success-no-result"}


@dataclass(frozen=True)
class JobProgress:
    """Progress update emitted by a child process."""

    kind: str
    stage: str
    message: str
    completed: int | None = None
    total: int | None = None


@dataclass(frozen=True)
class JobArtifact:
    """Intermediate artifact emitted before a job's terminal event."""

    kind: str
    name: str
    path: Path


class ProcessJob(QObject):
    """Run one GUI background job in a cancellable Python child process."""

    progress = Signal(object)  # JobProgress
    artifact = Signal(object)  # JobArtifact
    stage_failed = Signal(str, str)  # stage, message
    succeeded = Signal(object)  # result object loaded from result.pkl, or None
    failed = Signal(str)
    cancelled = Signal()
    done = Signal()

    def __init__(self, kind: str, *, parent: QObject | None = None, kill_after_ms: int = 1500) -> None:
        super().__init__(parent)
        self.kind = kind
        self.work_dir = Path(tempfile.mkdtemp(prefix=f"notegrabber-gui-{kind}-"))
        self.request_path = self.work_dir / REQUEST_FILE
        self.result_path = self.work_dir / RESULT_FILE
        self.kill_after_ms = kill_after_ms
        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments(["-m", "notegrabber.gui.job_runner", kind, str(self.work_dir)])
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        # POSIX: start a new process group so terminate/kill can include common
        # descendants spawned by audioread/TiMidity without touching the GUI.
        if hasattr(QProcess, "UnixProcessFlag"):
            flags = QProcess.UnixProcessFlag.CreateNewSession
            params = QProcess.UnixProcessParameters()
            params.flags = flags
            self.process.setUnixProcessParameters(params)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill_if_running)
        self._stdout_buffer = b""
        self._stderr_buffer = b""
        self._terminal_status: str | None = None
        self._terminal_message: str | None = None
        self._cancel_requested = False
        self._start_blocked = False
        self._done_emitted = False
        self._work_dir_taken = False

    def set_request(self, request: Any) -> None:
        """Serialize the trusted local request object for the child."""

        self.work_dir.mkdir(parents=True, exist_ok=True)
        with self.request_path.open("wb") as handle:
            pickle.dump(request, handle, protocol=pickle.HIGHEST_PROTOCOL)

    def start(self) -> None:
        if self._done_emitted or self._start_blocked:
            return
        self.process.start()

    def cancel(self) -> None:
        """Request bounded cancellation with terminate->kill escalation."""

        if self._done_emitted:
            return
        self._cancel_requested = True
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._start_blocked = True
            self._emit_cancelled_once()
            return
        self._terminate_process_tree()
        if not self._kill_timer.isActive():
            self._kill_timer.start(self.kill_after_ms)

    def take_work_dir(self) -> Path:
        """Transfer ownership of the job directory to the caller."""

        self._work_dir_taken = True
        return self.work_dir

    @property
    def is_finished(self) -> bool:
        return self.process.state() == QProcess.ProcessState.NotRunning and self._done_emitted

    @property
    def is_running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning and not self._done_emitted

    def cleanup(self) -> None:
        if not self._work_dir_taken:
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def _read_stdout(self) -> None:
        if self._done_emitted:
            return
        self._stdout_buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split(b"\n", 1)
            self._handle_stdout_line(line.decode("utf-8", errors="replace"))

    def _read_stderr(self) -> None:
        self._stderr_buffer += bytes(self.process.readAllStandardError())

    def _handle_stdout_line(self, line: str) -> None:
        if self._done_emitted or not line.startswith(PROTOCOL_PREFIX):
            return
        try:
            payload = json.loads(line[len(PROTOCOL_PREFIX) :])
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        event = payload.get("event")
        if not isinstance(event, str):
            return
        if event == "progress":
            self.progress.emit(
                JobProgress(
                    kind=str(payload.get("kind") or self.kind),
                    stage=str(payload.get("stage") or "running"),
                    message=str(payload.get("message") or "Working…"),
                    completed=self._optional_int(payload.get("completed")),
                    total=self._optional_int(payload.get("total")),
                )
            )
        elif event == "artifact":
            safe_path = self._safe_child_path(payload.get("path"))
            if safe_path is None:
                self._terminal_status = "failed"
                self._terminal_message = "Child emitted an unsafe artifact path"
                if self.process.state() != QProcess.ProcessState.NotRunning:
                    self._terminate_process_tree()
                    if not self._kill_timer.isActive():
                        self._kill_timer.start(self.kill_after_ms)
                return
            self.artifact.emit(
                JobArtifact(
                    kind=str(payload.get("kind") or self.kind),
                    name=str(payload.get("name") or safe_path.name),
                    path=safe_path,
                )
            )
        elif event == "stage_failed":
            self.stage_failed.emit(str(payload.get("stage") or "job"), str(payload.get("message") or "Unknown error"))
        elif event in {"succeeded", "failed", "cancelled"}:
            if self._terminal_status is None:
                self._terminal_status = event
                self._terminal_message = str(payload.get("message") or "")

    def _process_finished(self, exit_code: int, _exit_status: object) -> None:
        if self._done_emitted:
            return
        self._kill_timer.stop()
        # Drain bytes that may have arrived with process termination before Qt
        # delivered the corresponding readyRead signals.
        self._read_stdout()
        self._read_stderr()
        if self._stdout_buffer:
            line = self._stdout_buffer.decode("utf-8", errors="replace")
            self._stdout_buffer = b""
            self._handle_stdout_line(line.rstrip("\r\n"))
        if self._cancel_requested:
            self._emit_cancelled_once()
            return
        if self._terminal_status == "succeeded" and exit_code == 0:
            if self.kind in REQUIRED_RESULT_KINDS and not self.result_path.exists():
                self._emit_failed_once(f"{self.kind} job reported success without a result")
                return
            result = None
            if self.result_path.exists():
                try:
                    with self.result_path.open("rb") as handle:
                        result = pickle.load(handle)
                except Exception as exc:  # pragma: no cover - defensive UI path
                    self._emit_failed_once(f"Could not read {self.kind} result: {exc}")
                    return
                self._remove_transport_file(self.result_path)
            self.succeeded.emit(result)
            self._emit_done_once()
            return
        if self._terminal_status == "cancelled":
            self._emit_cancelled_once()
            return
        message = self._terminal_message or self._stderr_text().strip() or f"{self.kind} job exited with code {exit_code}"
        self._emit_failed_once(message)

    def _process_error(self, error: object) -> None:
        if self._done_emitted:
            return
        # FailedToStart is the one QProcess error for which Qt does not emit
        # finished(), so it must become terminal here. Other errors (including a
        # crash) can arrive while Qt is still reaping the child; record the
        # failure and wait for finished() before cleanup/deletion so no live
        # process can outlast its controller or lose its working directory.
        if error == QProcess.ProcessError.FailedToStart:
            if self._cancel_requested:
                self._emit_cancelled_once()
                return
            message = f"{self.kind} process error: {self._process_error_name(error)}"
            extra = self.process.errorString()
            if extra:
                message = f"{message}: {extra}"
            self._emit_failed_once(message)
            return
        if self._cancel_requested:
            return
        if self._terminal_status != "failed":
            message = f"{self.kind} process error: {self._process_error_name(error)}"
            extra = self.process.errorString()
            if extra:
                message = f"{message}: {extra}"
            self._terminal_status = "failed"
            self._terminal_message = message
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._terminate_process_tree()
            if not self._kill_timer.isActive():
                self._kill_timer.start(self.kill_after_ms)

    def _stderr_text(self) -> str:
        return self._stderr_buffer.decode("utf-8", errors="replace")

    def _terminate_process_tree(self) -> None:
        if sys.platform != "win32":
            pid = int(self.process.processId())
            if pid > 0:
                try:
                    os.killpg(pid, signal.SIGTERM)
                    return
                except ProcessLookupError:
                    return
                except OSError:
                    pass
        self.process.terminate()

    def _kill_if_running(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        if sys.platform != "win32":
            pid = int(self.process.processId())
            if pid > 0:
                try:
                    os.killpg(pid, signal.SIGKILL)
                    return
                except ProcessLookupError:
                    return
                except OSError:
                    pass
        self.process.kill()

    def _emit_cancelled_once(self) -> None:
        if self._done_emitted:
            return
        self.cancelled.emit()
        self._emit_done_once()

    def _emit_failed_once(self, message: str) -> None:
        if self._done_emitted:
            return
        self.failed.emit(message)
        self._emit_done_once()

    def _emit_done_once(self) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        self._kill_timer.stop()
        self._remove_transport_file(self.request_path)
        self.done.emit()
        self.cleanup()
        self.deleteLater()

    def _safe_child_path(self, value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        child = Path(value)
        if child.is_absolute():
            return None
        root = self.work_dir.resolve()
        candidate = (root / child).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        return None

    @staticmethod
    def _remove_transport_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _process_error_name(error: object) -> str:
        try:
            return error.name  # type: ignore[attr-defined]
        except Exception:
            return str(error)


def emit_protocol(event: str, **payload: object) -> None:
    """Emit one framed JSON-line event from a child process."""

    data = {"event": event, **payload}
    print(PROTOCOL_PREFIX + json.dumps(data, separators=(",", ":"), allow_nan=False), flush=True)


def load_request(work_dir: Path) -> Any:
    path = work_dir / REQUEST_FILE
    with path.open("rb") as handle:
        request = pickle.load(handle)
    try:
        path.unlink()
    except OSError:
        pass
    return request


def write_result(work_dir: Path, result: Any) -> Path:
    path = work_dir / RESULT_FILE
    with path.open("wb") as handle:
        pickle.dump(result, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def write_artifact(work_dir: Path, name: str, value: Any) -> Path:
    path = work_dir / f"{name}.pkl"
    with path.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    return path
