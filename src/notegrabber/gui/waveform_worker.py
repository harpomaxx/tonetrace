"""Qt worker for loading waveform previews without blocking the GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .widgets.waveform import load_waveform_preview

try:  # pragma: no cover - imported only when GUI deps are installed
    from PySide6.QtCore import QObject, Signal, Slot
except ModuleNotFoundError:  # pragma: no cover
    QObject = object  # type: ignore[assignment,misc]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *_args: object) -> None:
            pass

    def Slot(*_args: object, **_kwargs: object):  # type: ignore[no-redef]
        def decorator(func):
            return func

        return decorator


@dataclass(frozen=True)
class WaveformResult:
    """Waveform preview payload emitted from the worker thread."""

    audio_path: Path
    samples: list[float]
    sample_rate: int
    duration_seconds: float


class WaveformWorker(QObject):
    """Load/downsample waveform data in a background Qt thread."""

    finished = Signal(object)
    failed = Signal(object, str)

    def __init__(self, audio_path: Path) -> None:
        super().__init__()
        self.audio_path = audio_path

    @Slot()
    def run(self) -> None:
        try:
            samples, sample_rate, duration_seconds = load_waveform_preview(self.audio_path)
        except Exception as exc:  # pragma: no cover - defensive UI path
            self.failed.emit(self.audio_path, str(exc))
            return
        self.finished.emit(
            WaveformResult(
                audio_path=self.audio_path,
                samples=samples,
                sample_rate=sample_rate,
                duration_seconds=duration_seconds,
            )
        )
