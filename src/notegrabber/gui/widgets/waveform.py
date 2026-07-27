"""Waveform overview widget for the standalone GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from notegrabber.analyzer import read_wav
from notegrabber.gui.overview import PitchOverview

from PySide6.QtCore import QRect, QRectF, Qt, Signal
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
        # Cached numpy view of samples plus a per-pixel (min, max) envelope so the
        # paint loop is a bare drawLine per column instead of a Python slice +
        # min()/max() each. Rebuilt lazily when the samples or column step change.
        self._samples_array: object = None
        self._envelope_step = 0
        self._envelope_low: object = None
        self._envelope_high: object = None
        # Left gutter matching the piano roll's keyboard width so the waveform
        # timeline and the heatmap timeline share the same seconds->x mapping and
        # their playheads line up on screen.
        self.left_gutter = 54
        self.empty_message = "Open an audio file to show waveform"
        self.pitch_overview: PitchOverview | None = None
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
        self._invalidate_envelope()
        self.update()

    def _invalidate_envelope(self) -> None:
        self._samples_array = None
        self._envelope_step = 0
        self._envelope_low = None
        self._envelope_high = None

    def set_pitch_overview(self, overview: PitchOverview | None) -> None:
        """Set or clear the low-resolution pitch overview strip."""

        self.pitch_overview = overview
        self.update()

    def set_message(self, message: str) -> None:
        """Clear samples and show an informational placeholder."""

        self.samples = []
        self.sample_rate = 0
        self.audio_duration_seconds = 0.0
        self.selection_start_seconds = None
        self.selection_duration_seconds = None
        self.pitch_overview = None
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
        """Set the drawn playhead position without repainting the full waveform."""

        old_seconds = self.playhead_seconds
        self.playhead_seconds = max(0.0, seconds)
        if self.duration_seconds() <= 0:
            self.update()
            return
        self.update(self._playhead_update_rect(old_seconds).united(self._playhead_update_rect(self.playhead_seconds)))

    def _playhead_update_rect(self, seconds: float) -> QRect:
        duration = self.duration_seconds()
        if duration <= 0:
            return self.rect()
        x = int(self._x_for_seconds(seconds))
        return QRect(max(0, x - 3), 0, 7, self.height())

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

    def _time_width(self) -> float:
        """Pixel width of the drawable time area (canvas minus the left gutter)."""

        return max(1.0, float(self.width()) - self.left_gutter)

    def _x_for_seconds(self, seconds: float) -> float:
        """Map a time in seconds to a canvas x, including the left gutter."""

        duration = self.duration_seconds()
        if duration <= 0:
            return float(self.left_gutter)
        ratio = max(0.0, min(1.0, max(0.0, seconds) / duration))
        return self.left_gutter + ratio * self._time_width()

    def _clamped_x(self, x: float) -> float:
        return max(float(self.left_gutter), min(float(self.width()), x))

    def _seconds_at_x(self, x: float) -> float:
        duration = self.duration_seconds()
        if duration <= 0:
            return 0.0
        offset = self._clamped_x(x) - self.left_gutter
        return duration * max(0.0, offset) / self._time_width()

    def _selection_from_pixels(self, start_x: float, end_x: float) -> tuple[float, float]:
        start_seconds = self._seconds_at_x(min(start_x, end_x))
        end_seconds = self._seconds_at_x(max(start_x, end_x))
        return start_seconds, max(0.0, end_seconds - start_seconds)

    def _selection_pixel_bounds(self) -> tuple[float, float] | None:
        duration = self.duration_seconds()
        if duration <= 0 or self.selection_start_seconds is None or self.selection_duration_seconds is None:
            return None
        start_x = self._x_for_seconds(self.selection_start_seconds)
        end_x = self._x_for_seconds(self.selection_start_seconds + self.selection_duration_seconds)
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
        overview_height = self._overview_height(height)
        waveform_height = max(1, height - overview_height)
        mid_y = waveform_height / 2
        painter.setPen(QPen(QColor(55, 65, 90), 1))
        painter.drawLine(0, int(mid_y), width, int(mid_y))

        if not self.samples:
            painter.setPen(QColor(150, 160, 180))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_message)
            return

        gutter = self.left_gutter
        time_width = int(self._time_width())
        clip = painter.clipBoundingRect()
        clip_left = max(gutter, int(clip.left()) - 1) if not clip.isEmpty() else gutter
        clip_right = min(width, int(clip.right()) + 2) if not clip.isEmpty() else width
        step = max(1, len(self.samples) // max(1, time_width))
        low_env, high_env = self._pixel_envelope(step)
        amplitude = waveform_height * 0.45
        painter.setPen(QPen(QColor(255, 170, 72), 1))
        for x in range(clip_left, clip_right):
            column = x - gutter
            if column < 0 or column >= len(low_env):
                continue
            y1 = int(mid_y - high_env[column] * amplitude)
            y2 = int(mid_y - low_env[column] * amplitude)
            painter.drawLine(x, y1, x, y2)

        self._draw_pitch_overview(painter, width, height, overview_height)
        self._draw_selection(painter, width, height)

        duration = self.duration_seconds()
        if duration > 0:
            playhead_x = int(self._x_for_seconds(self.playhead_seconds))
            painter.setPen(QPen(QColor(255, 230, 120), 2))
            painter.drawLine(playhead_x, 0, playhead_x, height)

    def _pixel_envelope(self, step: int) -> tuple[object, object]:
        """Return per-column ``(low, high)`` sample envelopes for the given step.

        Column ``i`` covers samples ``[i * step : (i + 1) * step]``, matching the
        old per-pixel slice. Cached on ``step`` and rebuilt only when the step or
        samples change, so selection-drag repaints reuse the same arrays. Uses
        numpy when available and falls back to a pure-Python build otherwise.
        """

        step = max(1, step)
        if self._envelope_step == step and self._envelope_low is not None:
            return self._envelope_low, self._envelope_high

        sample_count = len(self.samples)
        column_count = (sample_count + step - 1) // step  # ceil, includes ragged tail
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            low = [0.0] * column_count
            high = [0.0] * column_count
            for column in range(column_count):
                chunk = self.samples[column * step : column * step + step]
                if chunk:
                    low[column] = min(chunk)
                    high[column] = max(chunk)
        else:
            if self._samples_array is None:
                self._samples_array = np.asarray(self.samples, dtype=np.float32)
            data = self._samples_array
            full_columns = sample_count // step
            low = np.zeros(column_count, dtype=np.float32)
            high = np.zeros(column_count, dtype=np.float32)
            if full_columns:
                block = data[: full_columns * step].reshape(full_columns, step)
                low[:full_columns] = block.min(axis=1)
                high[:full_columns] = block.max(axis=1)
            if full_columns < column_count:  # ragged final chunk shorter than step
                tail = data[full_columns * step :]
                low[full_columns] = tail.min()
                high[full_columns] = tail.max()

        self._envelope_step = step
        self._envelope_low = low
        self._envelope_high = high
        return low, high

    def _overview_height(self, total_height: int) -> int:
        if self.pitch_overview is None:
            return 0
        return max(22, min(38, total_height // 3))

    def _draw_pitch_overview(self, painter: QPainter, width: int, height: int, overview_height: int) -> None:
        overview = self.pitch_overview
        if overview is None or overview_height <= 0 or overview.frame_count <= 0 or overview.band_count <= 0:
            return
        top = height - overview_height
        painter.fillRect(0, top, width, overview_height, QColor(9, 10, 15))
        clip = painter.clipBoundingRect()
        clip_left = clip.left() if not clip.isEmpty() else 0.0
        clip_right = clip.right() if not clip.isEmpty() else float(width)
        duration = max(self.duration_seconds(), overview.duration_seconds, 0.001)
        gutter = self.left_gutter
        time_width = self._time_width()
        band_height = max(1.0, overview_height / overview.band_count)
        rows = overview.activations
        band_count = overview.band_count
        for frame_index, time_seconds in enumerate(overview.frame_times):
            x = int(gutter + max(0.0, min(1.0, time_seconds / duration)) * time_width)
            next_time = overview.frame_times[frame_index + 1] if frame_index + 1 < overview.frame_count else duration
            next_x = int(gutter + max(0.0, min(1.0, next_time / duration)) * time_width)
            column_width = max(1, next_x - x)
            if x + column_width < clip_left or x > clip_right:
                continue
            # Index the row once per frame rather than a bounds-checked accessor
            # call per band; clamp inline to match PitchOverview.activation().
            row = rows[frame_index] if frame_index < len(rows) else ()
            for band_index in range(band_count):
                activation = float(row[band_index]) if band_index < len(row) else 0.0
                if activation <= 0.04:
                    continue
                activation = max(0.0, min(1.0, activation))
                y = top + int((band_count - 1 - band_index) * band_height)
                painter.fillRect(QRectF(x, y, column_width, max(1.0, band_height)), self._overview_color(activation))
        painter.setPen(QPen(QColor(255, 176, 64, 110), 1))
        painter.drawLine(0, top, width, top)

    @staticmethod
    def _overview_color(value: float) -> QColor:
        value = max(0.0, min(1.0, value))
        return QColor(int(55 + 200 * value), int(22 + 135 * value), int(8 + 18 * (1.0 - value)), int(38 + 170 * value))

    def _draw_selection(self, painter: QPainter, width: int, height: int) -> None:
        duration = self.duration_seconds()
        if duration <= 0 or self.selection_start_seconds is None or self.selection_duration_seconds is None:
            return
        x = int(self._x_for_seconds(self.selection_start_seconds))
        end_x = int(self._x_for_seconds(self.selection_start_seconds + self.selection_duration_seconds))
        selection_width = max(1, end_x - x)
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
