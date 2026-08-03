"""Child-process entry point for cancellable GUI background jobs."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path
from typing import Callable

from .audio_load_worker import OverviewReady, WaveformReady
from .overview import OVERVIEW_SAMPLE_RATE, build_overview_from_samples
from .process_jobs import emit_protocol, load_request, write_artifact, write_result
from .widgets.waveform import downsample_waveform_preview


def _progress(kind: str, stage: str, message: str, completed: int | None = None, total: int | None = None) -> None:
    payload: dict[str, object] = {"kind": kind, "stage": stage, "message": message}
    if completed is not None and total is not None:
        payload["completed"] = completed
        payload["total"] = total
    emit_protocol("progress", **payload)


def _run_audio_load(work_dir: Path) -> None:
    request = load_request(work_dir)
    audio_path = Path(request["audio_path"])
    sample_rate = int(request.get("sample_rate") or OVERVIEW_SAMPLE_RATE)

    try:
        import librosa  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - GUI deps missing
        raise RuntimeError(str(exc)) from exc

    _progress("audio-load", "decoding", "Decoding audio…", 0, 3)
    audio, actual_sample_rate = librosa.load(audio_path, sr=sample_rate, mono=True)
    actual_sample_rate = int(actual_sample_rate)
    duration_seconds = float(len(audio) / actual_sample_rate) if actual_sample_rate > 0 else 0.0

    _progress("audio-load", "waveform", "Building waveform preview…", 1, 3)
    waveform = WaveformReady(
        audio_path=audio_path,
        samples=downsample_waveform_preview(audio),
        sample_rate=actual_sample_rate,
        duration_seconds=duration_seconds,
    )
    waveform_path = write_artifact(work_dir, "waveform", waveform)
    emit_protocol("artifact", kind="audio-load", name="waveform", path=waveform_path.name)

    _progress("audio-load", "overview", "Building low-resolution pitch overview…", 2, 3)
    try:
        overview = build_overview_from_samples(audio, actual_sample_rate)
    except Exception as exc:  # keep the already-delivered waveform usable
        emit_protocol("stage_failed", kind="audio-load", stage="overview", message=str(exc))
    else:
        overview_path = write_artifact(work_dir, "overview", OverviewReady(audio_path=audio_path, overview=overview))
        emit_protocol("artifact", kind="audio-load", name="overview", path=overview_path.name)
    _progress("audio-load", "finalizing", "Audio preview ready.", 3, 3)
    write_result(work_dir, {"audio_path": audio_path})
    emit_protocol("succeeded", kind="audio-load", path="result.pkl")


def _run_analysis(work_dir: Path) -> None:
    from .analysis_worker import run_analysis_request

    request = load_request(work_dir)

    def progress(message: str) -> None:
        stage = "running"
        completed = 0
        total = 3 if request.render_midi else 2
        if message.startswith("Preparing"):
            stage = "preparing"
            completed = 0
        elif message.startswith("Analyzing"):
            stage = "transcribing"
            completed = 1
        elif message.startswith("Rendering"):
            stage = "rendering-preview"
            completed = 2
        _progress("analysis", stage, message, completed, total)

    result = run_analysis_request(request, work_dir, progress=progress)
    _progress("analysis", "finalizing", "Analysis ready.", 3 if request.render_midi else 2, 3 if request.render_midi else 2)
    write_result(work_dir, result)
    emit_protocol("succeeded", kind="analysis", path="result.pkl")


def _run_preview(work_dir: Path) -> None:
    from .midi_preview_worker import MidiPreviewResult, render_midi_preview

    request = load_request(work_dir)
    _progress("preview", "rendering-preview", "Rendering MIDI preview…", 0, 1)
    rendered_wav = render_midi_preview(request)
    _progress("preview", "finalizing", "MIDI preview ready.", 1, 1)
    write_result(work_dir, MidiPreviewResult(render_id=request.render_id, rendered_wav=rendered_wav))
    emit_protocol("succeeded", kind="preview", path="result.pkl")


def _run_test_block(work_dir: Path) -> None:
    _progress("test", "blocked", "Blocked test child ready…")
    write_result(work_dir, {"ready": True})
    while True:
        time.sleep(0.1)


def _run_test_ignore_terminate(work_dir: Path) -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _run_test_block(work_dir)


def _run_test_noisy(work_dir: Path) -> None:
    print("unframed stdout noise that must be ignored", flush=True)
    _progress("test", "noise", "Framed progress survives noise…", 1, 1)
    write_result(work_dir, {"ok": True})
    emit_protocol("succeeded", kind="test", path="result.pkl")


def _run_test_success_no_result(_work_dir: Path) -> None:
    emit_protocol("succeeded", kind="test")


def _run_test_unsafe_artifact(_work_dir: Path) -> None:
    emit_protocol("artifact", kind="test", name="bad", path="../escape.pkl")
    while True:
        time.sleep(0.1)


RUNNERS: dict[str, Callable[[Path], None]] = {
    "audio-load": _run_audio_load,
    "analysis": _run_analysis,
    "preview": _run_preview,
    "test-block": _run_test_block,
    "test-ignore-terminate": _run_test_ignore_terminate,
    "test-noisy": _run_test_noisy,
    "test-success-no-result": _run_test_success_no_result,
    "test-unsafe-artifact": _run_test_unsafe_artifact,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in RUNNERS:
        print("usage: python -m notegrabber.gui.job_runner <kind> <work-dir>", file=sys.stderr)
        return 2
    kind = args[0]
    work_dir = Path(args[1])
    try:
        RUNNERS[kind](work_dir)
    except KeyboardInterrupt:
        emit_protocol("cancelled", kind=kind)
        return 130
    except Exception as exc:  # pragma: no cover - defensive child boundary
        emit_protocol("failed", kind=kind, message=str(exc))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
