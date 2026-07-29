"""Qt worker wrapper around notegrabber analysis backends."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notegrabber.analyzer import BackendName, analyze_wav_to_midi
from notegrabber.midi import MidiNote, TICKS_PER_SECOND, write_midi
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
    range_start_seconds: float = 0.0
    range_duration_seconds: float | None = None


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
    analysis_start_seconds: float = 0.0
    analysis_duration_seconds: float | None = None
    midi_preview_offset_seconds: float = 0.0


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

            analysis_audio_path = self.request.audio_path
            offset_seconds = self._range_start_seconds()
            if self.request.range_duration_seconds is not None:
                self.progress.emit(
                    f"Preparing {self.request.range_duration_seconds:.1f}s range starting at {offset_seconds:.1f}s…"
                )
                analysis_audio_path = _extract_audio_range(
                    self.request.audio_path,
                    work_dir / "analysis-range.wav",
                    start_seconds=offset_seconds,
                    duration_seconds=self.request.range_duration_seconds,
                )

            self.progress.emit(f"Analyzing with {self.request.backend}…")
            local_midi_notes = analyze_wav_to_midi(
                analysis_audio_path,
                midi_path,
                heatmap_path=heatmap_path,
                backend=self.request.backend,
                threshold=self.request.threshold,
                onset_threshold=self.request.onset_threshold,
                frame_threshold=self.request.frame_threshold,
                min_duration_seconds=self.request.min_duration_seconds,
            )
            heatmap_document = json.loads(heatmap_path.read_text(encoding="utf-8"))
            preview_midi_notes = _clip_midi_notes_to_duration(local_midi_notes, self.request.range_duration_seconds)
            midi_notes = local_midi_notes
            if offset_seconds:
                midi_notes = _offset_midi_notes(local_midi_notes, offset_seconds)
                _offset_heatmap_document(heatmap_document, offset_seconds)
                write_midi(midi_path, midi_notes)
                heatmap_path.write_text(json.dumps(heatmap_document, indent=2), encoding="utf-8")
            heatmap = heatmap_from_document(heatmap_document)
            notes = midi_notes_to_gui(midi_notes, source=self.request.backend)

            rendered_midi_wav: Path | None = None
            render_error: str | None = None
            if self.request.render_midi:
                self.progress.emit("Rendering MIDI preview…")
                preview_midi_path = midi_path
                if self.request.range_duration_seconds is not None:
                    preview_midi_path = work_dir / "analysis-preview-local.mid"
                    write_midi(preview_midi_path, preview_midi_notes)
                rendered_midi_wav, render_error = render_midi_to_wav(preview_midi_path, rendered_path)

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
                    analysis_start_seconds=offset_seconds if self.request.range_duration_seconds is not None else 0.0,
                    analysis_duration_seconds=self.request.range_duration_seconds,
                    midi_preview_offset_seconds=offset_seconds if self.request.range_duration_seconds is not None else 0.0,
                )
            )
        except Exception as exc:  # pragma: no cover - exercised by manual GUI flows
            self.failed.emit(str(exc))

    def _range_start_seconds(self) -> float:
        return max(0.0, float(self.request.range_start_seconds))


def _offset_midi_notes(notes: list[MidiNote], offset_seconds: float) -> list[MidiNote]:
    """Return MIDI notes shifted later by offset seconds."""

    offset_ticks = max(0, round(offset_seconds * TICKS_PER_SECOND))
    return [
        MidiNote(
            pitch=note.pitch,
            start_tick=max(0, note.start_tick + offset_ticks),
            duration_ticks=note.duration_ticks,
            velocity=note.velocity,
        )
        for note in notes
    ]


def _offset_heatmap_document(document: dict[str, Any], offset_seconds: float) -> None:
    """Shift a heatmap document's frame times in-place by offset seconds."""

    for frame in document.get("frames", []):
        frame["time_seconds"] = float(frame.get("time_seconds", 0.0)) + offset_seconds


def _clip_midi_notes_to_duration(notes: list[MidiNote], duration_seconds: float | None) -> list[MidiNote]:
    """Return notes clipped to a local preview duration."""

    if duration_seconds is None:
        return list(notes)
    end_tick = max(1, round(max(0.0, duration_seconds) * TICKS_PER_SECOND))
    clipped: list[MidiNote] = []
    for note in notes:
        start_tick = max(0, note.start_tick)
        if start_tick >= end_tick:
            continue
        duration_ticks = max(1, min(note.duration_ticks, end_tick - start_tick))
        clipped.append(
            MidiNote(
                pitch=note.pitch,
                start_tick=start_tick,
                duration_ticks=duration_ticks,
                velocity=note.velocity,
            )
        )
    return clipped


def _extract_audio_range(input_audio: Path, output_wav: Path, *, start_seconds: float, duration_seconds: float) -> Path:
    """Decode a time range from any librosa-supported audio file into a temporary WAV."""

    if duration_seconds <= 0:
        raise ValueError("analysis range duration must be greater than 0 seconds")
    try:
        import librosa  # type: ignore[import-not-found]
        import soundfile as sf  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Range analysis requires librosa/soundfile; reinstall the GUI with `python3 -m pip install -e '.[gui]'`."
        ) from exc

    audio, sample_rate = librosa.load(
        input_audio,
        sr=None,
        mono=True,
        offset=max(0.0, start_seconds),
        duration=duration_seconds,
    )
    if len(audio) == 0:
        raise ValueError("selected analysis range contains no decoded audio")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_wav, audio, sample_rate)
    return output_wav
