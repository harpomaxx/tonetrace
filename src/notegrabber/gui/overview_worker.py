"""Qt worker for building low-resolution full-song pitch overviews."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .overview import PitchOverview, build_pitch_overview

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
class OverviewResult:
    """Pitch overview payload emitted from the worker thread."""

    audio_path: Path
    overview: PitchOverview


class OverviewWorker(QObject):
    """Build a reduced full-song pitch overview in a background Qt thread."""

    finished = Signal(object)
    failed = Signal(object, str)
    progress = Signal(str)

    def __init__(self, audio_path: Path) -> None:
        super().__init__()
        self.audio_path = audio_path

    @Slot()
    def run(self) -> None:
        try:
            self.progress.emit("Building low-resolution pitch overview…")
            overview = build_pitch_overview(self.audio_path)
        except Exception as exc:  # pragma: no cover - defensive UI path
            self.failed.emit(self.audio_path, str(exc))
            return
        self.finished.emit(OverviewResult(audio_path=self.audio_path, overview=overview))
