"""Qt worker for rendering the edited-MIDI WAV preview off the UI thread.

Editing a note (drag release, inspector apply, delete, CQT retune) re-renders a
short MIDI preview WAV with TiMidity++.  That render is a blocking subprocess, so
running it on the GUI thread freezes the UI on every edit.  This worker moves it
to a background thread; the main window debounces edits and ignores stale
completions via ``render_id``.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from notegrabber.midi import write_midi
from notegrabber.midi_render import render_midi_to_wav

from .state import GuiMidiNote, gui_notes_to_midi

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
class MidiPreviewRequest:
    """A single preview-render request, tagged with a monotonic id."""

    render_id: int
    notes: list[GuiMidiNote]
    midi_path: Path
    wav_path: Path
    silent_duration_seconds: float


@dataclass(frozen=True)
class MidiPreviewResult:
    """Result of a preview render, echoing the request id so stale ones drop."""

    render_id: int
    rendered_wav: Path


def render_midi_preview(request: MidiPreviewRequest) -> Path:
    """Write the preview MIDI and render (or synthesize silence) to WAV.

    Shared by the worker thread and the synchronous test-flush path.  Raises on
    failure so callers can surface an error status.
    """

    write_midi(request.midi_path, gui_notes_to_midi(request.notes))
    if request.notes:
        rendered_path, render_error = render_midi_to_wav(request.midi_path, request.wav_path)
        if rendered_path is None:
            raise RuntimeError(render_error or "unknown render error")
        return rendered_path
    _write_silent_wav(request.wav_path, request.silent_duration_seconds)
    return request.wav_path


def _write_silent_wav(path: Path, duration_seconds: float) -> None:
    """Write a silent WAV preview for an intentionally empty edited note list."""

    sample_rate = 44_100
    frame_count = max(1, round(max(1.0, duration_seconds) * sample_rate))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frame_count)


class MidiPreviewWorker(QObject):
    """Render an edited-MIDI WAV preview in a background Qt thread."""

    finished = Signal(object)
    failed = Signal(object, str)

    def __init__(self, request: MidiPreviewRequest) -> None:
        super().__init__()
        self.request = request

    @Slot()
    def run(self) -> None:
        try:
            rendered_wav = render_midi_preview(self.request)
        except Exception as exc:  # pragma: no cover - defensive UI path
            self.failed.emit(self.request, str(exc))
            return
        self.finished.emit(MidiPreviewResult(render_id=self.request.render_id, rendered_wav=rendered_wav))
