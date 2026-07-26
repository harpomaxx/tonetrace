"""Waveform overview widget for the standalone GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from notegrabber.analyzer import read_wav

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

MAX_WAVEFORM_PREVIEW_SAMPLES = 48_000
FALLBACK_WAVEFORM_SAMPLE_RATE = 4_000


class WaveformWidget(QWidget):
    """Draw a lightweight mono waveform overview from an audio file."""

    seek_requested = Signal(float)
    range_selected = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.samples: list[float] = []
        self.sample_rate = 0
        self.audio_duration_seconds = 0.0
        self.empty_message = "Open an audio file to show waveform"
        self.playhead_seconds = 0.0
        self.selection_start_seconds: float | None = None
        self.selection_duration_seconds: float | None = None
        self._drag_start_x: float | None = None
        self._drag_current_x: float | None = None
        self._is_selecting = False
        self._selection_drag_mode: str | None = None
        self._drag_original_start_seconds = 0.0
        self._drag_original_duration_seconds = 0.0
        self._drag_threshold_pixels = 4.0
        self._handle_width_pixels = 8.0
        self.setMouseTracking(True)
        self.setToolTip("Click to seek. Drag to select a range; drag selection edges to refine it.")
        self.setMinimumHeight(90)

    def load_audio(self, path: Path) -> None:
        """Load audio samples for drawing.

        PCM WAV uses the project's tiny stdlib reader. Other formats such as
        MP3/FLAC/OGG fall back to librosa when installed through the standalone
        dependency set. The drawn data is downsampled to a bounded preview so
        long recordings do not make painting sluggish.
        """

        samples, sample_rate, duration = load_waveform_preview(path)
        self.set_preview(samples, sample_rate=sample_rate, duration_seconds=duration)

    def set_preview(self, samples: list[float], *, sample_rate: int, duration_seconds: float) -> None:
        """Set already-loaded waveform preview data."""

        self.samples = samples
        self.sample_rate = sample_rate
        self.audio_duration_seconds = duration_seconds
        self.empty_message = "Open an audio file to show waveform"
        self.update()

    def set_message(self, message: str) -> None:
        """Clear samples and show an informational placeholder."""

        self.samples = []
        self.sample_rate = 0
        self.audio_duration_seconds = 0.0
        self.selection_start_seconds = None
        self.selection_duration_seconds = None
        self.empty_message = message
        self.update()

    @staticmethod
    def _load_with_librosa(path: Path) -> tuple[list[float], int, float]:
        """Load non-WAV audio at a low sample rate for waveform preview."""

        import librosa  # type: ignore[import-not-found]

        sample_rate = FALLBACK_WAVEFORM_SAMPLE_RATE
        loaded, actual_sample_rate = librosa.load(path, sr=sample_rate, mono=True)
        try:
            duration = float(librosa.get_duration(path=str(path)))
        except Exception:
            duration = float(len(loaded) / actual_sample_rate) if actual_sample_rate > 0 else 0.0
        return [float(value) for value in loaded], int(actual_sample_rate), duration

    def duration_seconds(self) -> float:
        return max(0.0, self.audio_duration_seconds)

    def set_playhead(self, seconds: float) -> None:
        """Set the drawn playhead position."""

        self.playhead_seconds = max(0.0, seconds)
        self.update()

    def set_selection(self, start_seconds: float | None, duration_seconds: float | None) -> None:
        """Set or clear the highlighted waveform range selection."""

        if start_seconds is None or duration_seconds is None or duration_seconds <= 0:
            self.selection_start_seconds = None
            self.selection_duration_seconds = None
        else:
            duration = self.duration_seconds()
            start = max(0.0, min(float(start_seconds), duration))
            end = max(start, min(start + float(duration_seconds), duration))
            self.selection_start_seconds = start
            self.selection_duration_seconds = end - start
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.duration_seconds() > 0:
            x = self._clamped_x(float(event.position().x()))
            hit = self._selection_hit_at(x)
            self._drag_start_x = x
            self._drag_current_x = x
            self._is_selecting = False
            self._selection_drag_mode = hit
            self._drag_original_start_seconds = self.selection_start_seconds or 0.0
            self._drag_original_duration_seconds = self.selection_duration_seconds or 0.0
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        x = self._clamped_x(float(event.position().x()))
        if self._drag_start_x is not None and self.duration_seconds() > 0:
            self._drag_current_x = x
            if self._selection_drag_mode is not None:
                self._drag_existing_selection(x)
            elif abs(self._drag_current_x - self._drag_start_x) >= self._drag_threshold_pixels:
                self._is_selecting = True
                start, duration = self._selection_from_pixels(self._drag_start_x, self._drag_current_x)
                self.set_selection(start, duration)
        else:
            self._update_hover_cursor(x)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        duration = self.duration_seconds()
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start_x is not None and duration > 0:
            release_x = self._clamped_x(float(event.position().x()))
            if self._selection_drag_mode is not None:
                self._drag_existing_selection(release_x)
                self._emit_current_selection()
            elif self._is_selecting:
                start, selection_duration = self._selection_from_pixels(self._drag_start_x, release_x)
                self.set_selection(start, selection_duration)
                self._emit_current_selection()
            else:
                self.seek_requested.emit(self._seconds_at_x(release_x))
        self._drag_start_x = None
        self._drag_current_x = None
        self._is_selecting = False
        self._selection_drag_mode = None
        self._update_hover_cursor(float(event.position().x()))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_start_x is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def _drag_existing_selection(self, x: float) -> None:
        if self._drag_start_x is None or self._selection_drag_mode is None:
            return
        duration = self.duration_seconds()
        if duration <= 0:
            return
        delta_seconds = self._seconds_at_x(x) - self._seconds_at_x(self._drag_start_x)
        original_start = self._drag_original_start_seconds
        original_end = original_start + self._drag_original_duration_seconds
        minimum_duration = max(0.01, duration / max(1.0, self.width()))
        if self._selection_drag_mode == "resize_start":
            start = max(0.0, min(original_start + delta_seconds, original_end - minimum_duration))
            end = original_end
        elif self._selection_drag_mode == "resize_end":
            start = original_start
            end = min(duration, max(original_end + delta_seconds, original_start + minimum_duration))
        elif self._selection_drag_mode == "move":
            selection_duration = self._drag_original_duration_seconds
            start = max(0.0, min(original_start + delta_seconds, duration - selection_duration))
            end = start + selection_duration
        else:
            return
        self.set_selection(start, max(minimum_duration, end - start))

    def _emit_current_selection(self) -> None:
        if self.selection_start_seconds is not None and self.selection_duration_seconds is not None and self.selection_duration_seconds > 0:
            self.range_selected.emit(self.selection_start_seconds, self.selection_duration_seconds)

    def _clamped_x(self, x: float) -> float:
        return max(0.0, min(float(self.width()), x))

    def _seconds_at_x(self, x: float) -> float:
        duration = self.duration_seconds()
        if duration <= 0:
            return 0.0
        return duration * self._clamped_x(x) / max(1.0, float(self.width()))

    def _selection_from_pixels(self, start_x: float, end_x: float) -> tuple[float, float]:
        start_seconds = self._seconds_at_x(min(start_x, end_x))
        end_seconds = self._seconds_at_x(max(start_x, end_x))
        return start_seconds, max(0.0, end_seconds - start_seconds)

    def _selection_pixel_bounds(self) -> tuple[float, float] | None:
        duration = self.duration_seconds()
        if duration <= 0 or self.selection_start_seconds is None or self.selection_duration_seconds is None:
            return None
        start_x = self.width() * self.selection_start_seconds / duration
        end_x = self.width() * (self.selection_start_seconds + self.selection_duration_seconds) / duration
        return self._clamped_x(start_x), self._clamped_x(end_x)

    def _selection_hit_at(self, x: float) -> str | None:
        bounds = self._selection_pixel_bounds()
        if bounds is None:
            return None
        start_x, end_x = bounds
        if abs(x - start_x) <= self._handle_width_pixels:
            return "resize_start"
        if abs(x - end_x) <= self._handle_width_pixels:
            return "resize_end"
        if start_x < x < end_x:
            return "move"
        return None

    def _update_hover_cursor(self, x: float) -> None:
        hit = self._selection_hit_at(self._clamped_x(x))
        if hit in {"resize_start", "resize_end"}:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif hit == "move":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(16, 19, 28))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        width = max(1, self.width())
        height = max(1, self.height())
        mid_y = height / 2
        painter.setPen(QPen(QColor(55, 65, 90), 1))
        painter.drawLine(0, int(mid_y), width, int(mid_y))

        if not self.samples:
            painter.setPen(QColor(150, 160, 180))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_message)
            return

        self._draw_selection(painter, width, height)

        step = max(1, len(self.samples) // width)
        painter.setPen(QPen(QColor(255, 170, 72), 1))
        for x in range(width):
            start = x * step
            end = min(len(self.samples), start + step)
            if start >= end:
                break
            chunk = self.samples[start:end]
            low = min(chunk)
            high = max(chunk)
            y1 = int(mid_y - high * (height * 0.45))
            y2 = int(mid_y - low * (height * 0.45))
            painter.drawLine(x, y1, x, y2)

        duration = self.duration_seconds()
        if duration > 0:
            playhead_x = int(max(0.0, min(1.0, self.playhead_seconds / duration)) * width)
            painter.setPen(QPen(QColor(255, 230, 120), 2))
            painter.drawLine(playhead_x, 0, playhead_x, height)

    def _draw_selection(self, painter: QPainter, width: int, height: int) -> None:
        duration = self.duration_seconds()
        if duration <= 0 or self.selection_start_seconds is None or self.selection_duration_seconds is None:
            return
        start_ratio = max(0.0, min(1.0, self.selection_start_seconds / duration))
        end_ratio = max(0.0, min(1.0, (self.selection_start_seconds + self.selection_duration_seconds) / duration))
        x = int(start_ratio * width)
        selection_width = max(1, int((end_ratio - start_ratio) * width))
        end_x = x + selection_width
        painter.fillRect(x, 0, selection_width, height, QColor(255, 126, 24, 55))
        painter.setPen(QPen(QColor(255, 186, 64), 2))
        painter.drawLine(x, 0, x, height)
        painter.drawLine(end_x, 0, end_x, height)
        handle_width = 7
        painter.fillRect(x - handle_width // 2, 0, handle_width, height, QColor(255, 186, 64, 130))
        painter.fillRect(end_x - handle_width // 2, 0, handle_width, height, QColor(255, 186, 64, 130))


def load_waveform_preview(path: Path) -> tuple[list[float], int, float]:
    """Load and downsample waveform preview data for any supported local audio file."""

    try:
        audio = read_wav(path)
        duration = len(audio.samples) / audio.sample_rate if audio.sample_rate > 0 else 0.0
        return downsample_waveform_preview(audio.samples), audio.sample_rate, duration
    except Exception as wav_error:
        try:
            samples, sample_rate, duration = WaveformWidget._load_with_librosa(path)
        except Exception as fallback_error:
            raise RuntimeError(
                "Waveform preview supports PCM WAV directly. For MP3/FLAC/OGG previews, "
                "install standalone dependencies with `python3 -m pip install -e '.[standalone]'`. "
                f"WAV error: {wav_error}; fallback error: {fallback_error}"
            ) from fallback_error
        return downsample_waveform_preview(samples), sample_rate, duration


def downsample_waveform_preview(samples: Iterable[float], max_samples: int = MAX_WAVEFORM_PREVIEW_SAMPLES) -> list[float]:
    """Return a bounded-size waveform preview while preserving coarse shape."""

    values = [max(-1.0, min(1.0, float(value))) for value in samples]
    if len(values) <= max_samples:
        return values
    step = max(1, len(values) // max_samples)
    preview: list[float] = []
    for start in range(0, len(values), step):
        chunk = values[start : start + step]
        if not chunk:
            continue
        low = min(chunk)
        high = max(chunk)
        preview.extend((low, high) if abs(low) >= abs(high) else (high, low))
        if len(preview) >= max_samples:
            break
    return preview[:max_samples]
