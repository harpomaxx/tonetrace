"""Qt worker that decodes an audio file once and feeds both consumers.

Opening a file used to decode it up to three times: the waveform worker
decoded the whole file for its preview, the overview worker decoded it again
for the low-res CQT, and QMediaPlayer decodes for playback. This worker does a
single mono decode and derives both the waveform preview and the pitch overview
from that shared buffer, so time-to-waveform and time-to-overview no longer race
each other on the CPU (issue #33). The duration comes free from the decoded
length, removing the extra ``librosa.get_duration`` open too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .overview import OVERVIEW_SAMPLE_RATE, PitchOverview, build_overview_from_samples
from .widgets.waveform import downsample_waveform_preview

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
class WaveformReady:
    """Waveform preview derived from the shared decode."""

    audio_path: Path
    samples: list[float]
    sample_rate: int
    duration_seconds: float


@dataclass(frozen=True)
class OverviewReady:
    """Pitch overview derived from the shared decode."""

    audio_path: Path
    overview: PitchOverview


class AudioLoadWorker(QObject):
    """Decode an audio file once and emit the waveform preview then the overview."""

    waveform_ready = Signal(object)
    overview_ready = Signal(object)
    # (audio_path, stage, message): stage is "waveform" or "overview" so the
    # window can degrade just the affected view without discarding the other.
    failed = Signal(object, str, str)
    progress = Signal(str)
    # Emitted once when run() returns (success or failure), so the owning thread
    # can quit only after both payloads have been produced.
    done = Signal()

    def __init__(self, audio_path: Path, *, sample_rate: int = OVERVIEW_SAMPLE_RATE) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.sample_rate = sample_rate

    @Slot()
    def run(self) -> None:
        try:
            self._run()
        finally:
            self.done.emit()

    def _run(self) -> None:
        try:
            import librosa  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:  # pragma: no cover - GUI deps missing
            self.failed.emit(self.audio_path, "waveform", str(exc))
            self.failed.emit(self.audio_path, "overview", str(exc))
            return

        # One mono decode at the overview rate feeds both the waveform envelope
        # and the CQT; the waveform preview only needs enough resolution to draw
        # a bounded envelope, so this rate is ample for it too.
        try:
            audio, actual_sample_rate = librosa.load(self.audio_path, sr=self.sample_rate, mono=True)
        except Exception as exc:  # pragma: no cover - defensive UI path
            self.failed.emit(self.audio_path, "waveform", str(exc))
            self.failed.emit(self.audio_path, "overview", str(exc))
            return

        actual_sample_rate = int(actual_sample_rate)
        duration_seconds = float(len(audio) / actual_sample_rate) if actual_sample_rate > 0 else 0.0

        # Waveform preview first (cheap): gets a visible waveform up fast.
        try:
            samples = downsample_waveform_preview(audio)
            self.waveform_ready.emit(
                WaveformReady(
                    audio_path=self.audio_path,
                    samples=samples,
                    sample_rate=actual_sample_rate,
                    duration_seconds=duration_seconds,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive UI path
            self.failed.emit(self.audio_path, "waveform", str(exc))

        # Overview CQT next (slower) from the same buffer.
        try:
            self.progress.emit("Building low-resolution pitch overview…")
            overview = build_overview_from_samples(audio, actual_sample_rate)
            self.overview_ready.emit(OverviewReady(audio_path=self.audio_path, overview=overview))
        except Exception as exc:  # pragma: no cover - defensive UI path
            self.failed.emit(self.audio_path, "overview", str(exc))
