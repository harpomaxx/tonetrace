"""Qt worker wrapper around notegrabber analysis backends."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from notegrabber.analyzer import BackendName, analyze_wav_to_midi
from notegrabber.visualizer import render_midi_to_wav

from .state import GuiHeatmap, GuiMidiNote, heatmap_from_document, midi_notes_to_gui

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
class AnalysisRequest:
    """Parameters for a background analysis run."""

    audio_path: Path
    backend: BackendName
    render_midi: bool
    threshold: float
    onset_threshold: float
    frame_threshold: float
    min_duration_seconds: float


@dataclass(frozen=True)
class AnalysisResult:
    """Result payload emitted by the analysis worker."""

    audio_path: Path
    backend: BackendName
    midi_path: Path
    heatmap_path: Path
    rendered_midi_wav: Path | None
    render_error: str | None
    notes: list[GuiMidiNote]
    heatmap: GuiHeatmap


class AnalysisWorker(QObject):
    """Run analysis in a Qt thread and emit a GUI-friendly result."""

    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, request: AnalysisRequest) -> None:
        super().__init__()
        self.request = request

    @Slot()
    def run(self) -> None:
        """Execute the analysis request."""

        try:
            self.progress.emit("Preparing analysis…")
            work_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-"))
            midi_path = work_dir / "analysis.mid"
            heatmap_path = work_dir / "heatmap.json"
            rendered_path = work_dir / "analysis.wav"

            self.progress.emit(f"Analyzing with {self.request.backend}…")
            midi_notes = analyze_wav_to_midi(
                self.request.audio_path,
                midi_path,
                heatmap_path=heatmap_path,
                backend=self.request.backend,
                threshold=self.request.threshold,
                onset_threshold=self.request.onset_threshold,
                frame_threshold=self.request.frame_threshold,
                min_duration_seconds=self.request.min_duration_seconds,
            )
            heatmap_document = json.loads(heatmap_path.read_text(encoding="utf-8"))
            heatmap = heatmap_from_document(heatmap_document)
            notes = midi_notes_to_gui(midi_notes, source=self.request.backend)

            rendered_midi_wav: Path | None = None
            render_error: str | None = None
            if self.request.render_midi:
                self.progress.emit("Rendering MIDI preview…")
                rendered_midi_wav, render_error = render_midi_to_wav(midi_path, rendered_path)

            self.finished.emit(
                AnalysisResult(
                    audio_path=self.request.audio_path,
                    backend=self.request.backend,
                    midi_path=midi_path,
                    heatmap_path=heatmap_path,
                    rendered_midi_wav=rendered_midi_wav,
                    render_error=render_error,
                    notes=notes,
                    heatmap=heatmap,
                )
            )
        except Exception as exc:  # pragma: no cover - exercised by manual GUI flows
            self.failed.emit(str(exc))
