"""Heatmap + MIDI rectangle piano-roll widget."""

from __future__ import annotations

import math

from PySide6.QtCore import QSize, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from notegrabber.gui.state import GuiHeatmap, GuiMidiNote


class PianoRollWidget(QWidget):
    """Draw a piano-roll heatmap with extracted MIDI note rectangles."""

    seek_requested = Signal(float)
    note_selected = Signal(int, float)
    note_edited = Signal(int, float, float, int, int, bool)
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.heatmap: GuiHeatmap | None = None
        self.notes: list[GuiMidiNote] = []
        self.show_notes = True
        self.selected_note_index: int | None = None
        self.hover_note_index: int | None = None
        self.hover_mode: str | None = None
        self.playhead_seconds = 0.0
        self.seconds_per_pixel = 0.01
        self.fit_seconds_per_pixel = 0.01
        self.horizontal_zoom = 1.0
        self.note_height = 7
        self.keyboard_width = 54
        self.drag_mode: str | None = None
        self.drag_note_index: int | None = None
        self.drag_start_x = 0.0
        self.drag_start_y = 0.0
        self.drag_original_note: GuiMidiNote | None = None
        self.min_note_duration_seconds = 0.001
        self.minimum_canvas_width = 760
        self.minimum_canvas_height = 420
        self.setMouseTracking(True)
        self.setToolTip("Click notes to select. Drag note bodies to move time/pitch; drag edges to resize.")
        self.setMinimumSize(self.minimum_canvas_width, self.minimum_canvas_height)

    def set_data(self, heatmap: GuiHeatmap | None, notes: list[GuiMidiNote]) -> None:
        """Update heatmap and notes."""

        self.heatmap = heatmap
        self.notes = notes
        if self.selected_note_index is not None and self.selected_note_index >= len(notes):
            self.selected_note_index = None
        if self.hover_note_index is not None and self.hover_note_index >= len(notes):
            self.hover_note_index = None
            self.hover_mode = None
        if heatmap is not None:
            duration = max(heatmap.duration_seconds, max((note.end_seconds for note in notes), default=0.0), 1.0)
            available_width = max(300, self.width() - self.keyboard_width)
            self.fit_seconds_per_pixel = max(0.0005, duration / available_width)
            self.seconds_per_pixel = max(0.0005, self.fit_seconds_per_pixel / self.horizontal_zoom)
        self._update_canvas_size()
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self.minimumWidth(), self.minimumHeight())

    def _update_canvas_size(self) -> None:
        if self.heatmap is None:
            self.setMinimumSize(self.minimum_canvas_width, self.minimum_canvas_height)
            return
        full_note_height = max(self.minimum_canvas_height, self.heatmap.note_count * self.note_height)
        duration = max(self.heatmap.duration_seconds, max((note.end_seconds for note in self.notes), default=0.0), 1.0)
        full_time_width = self.keyboard_width + int(duration / self.seconds_per_pixel) + 32
        self.setMinimumSize(max(self.minimum_canvas_width, full_time_width), full_note_height)
        # QScrollArea grows the child eagerly when minimumSize increases, but it
        # may not shrink it immediately when zooming out. Resize explicitly so
        # Ctrl+wheel zoom-out visibly reduces the timeline width right away.
        self.resize(self.minimumSize())

    def set_horizontal_zoom(self, zoom: float) -> None:
        """Set horizontal zoom, where 1.0 fits the whole analysis in the viewport."""

        self.horizontal_zoom = max(1.0, min(32.0, float(zoom)))
        self.seconds_per_pixel = max(0.0005, self.fit_seconds_per_pixel / self.horizontal_zoom)
        self._update_canvas_size()
        self.updateGeometry()
        self.update()

    def set_show_notes(self, enabled: bool) -> None:
        self.show_notes = enabled
        self.update()

    def set_selected_note_index(self, index: int | None) -> None:
        """Select a note by current note-list index."""

        self.selected_note_index = index if index is not None and 0 <= index < len(self.notes) else None
        self.update()

    def set_playhead(self, seconds: float) -> None:
        """Set the drawn playhead position."""

        self.playhead_seconds = max(0.0, seconds)
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.heatmap is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y() or event.pixelDelta().y()
            if delta_y:
                self.zoom_by_wheel_delta(delta_y)
                event.accept()
                return
        super().wheelEvent(event)

    def zoom_by_wheel_delta(self, delta_y: int) -> None:
        """Zoom horizontally from a wheel delta; positive delta zooms in."""

        factor = 1.2 ** (delta_y / 120.0)
        self.set_horizontal_zoom(self.horizontal_zoom * factor)
        self.zoom_changed.emit(self.horizontal_zoom)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.heatmap is not None and event.button() == Qt.MouseButton.LeftButton:
            x = float(event.position().x())
            y = float(event.position().y())
            hit = self._note_hit_at(x, y)
            if hit is not None:
                note_index, mode = hit
                self.drag_mode = mode
                self.drag_note_index = note_index
                self.drag_start_x = x
                self.drag_start_y = y
                self.drag_original_note = self.notes[note_index]
                self.set_selected_note_index(note_index)
                note = self.notes[note_index]
                self.note_selected.emit(note_index, note.start_seconds)
                self.seek_requested.emit(note.start_seconds)
            else:
                self.drag_mode = None
                self.drag_note_index = None
                self.drag_original_note = None
                self.set_selected_note_index(None)
                x_after_keyboard = x - self.keyboard_width
                if x_after_keyboard >= 0:
                    self.seek_requested.emit(max(0.0, x_after_keyboard * self.seconds_per_pixel))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        x = float(event.position().x())
        y = float(event.position().y())
        if self.drag_mode is not None and self.drag_note_index is not None and self.drag_original_note is not None:
            edited = self._edited_drag_note(x, y)
            if edited is not None:
                self.note_edited.emit(
                    self.drag_note_index,
                    edited.start_seconds,
                    edited.duration_seconds,
                    edited.pitch,
                    edited.velocity,
                    False,
                )
        else:
            self._update_hover_state(x, y)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.drag_mode is not None and self.drag_note_index is not None and self.drag_original_note is not None:
            edited = self._edited_drag_note(float(event.position().x()), float(event.position().y()))
            if edited is not None:
                self.note_edited.emit(
                    self.drag_note_index,
                    edited.start_seconds,
                    edited.duration_seconds,
                    edited.pitch,
                    edited.velocity,
                    True,
                )
        self.drag_mode = None
        self.drag_note_index = None
        self.drag_original_note = None
        self._update_hover_state(float(event.position().x()), float(event.position().y()))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.hover_note_index = None
        self.hover_mode = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(9, 10, 18))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        if self.heatmap is None:
            painter.setPen(QColor(170, 180, 195))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Analyze audio to show heatmap and MIDI notes")
            return

        self._draw_keyboard(painter)
        self._draw_heatmap(painter)
        self._draw_grid(painter)
        if self.show_notes:
            self._draw_notes(painter)
        self._draw_playhead(painter)

    def _draw_keyboard(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        painter.fillRect(0, 0, self.keyboard_width, self.height(), QColor(20, 24, 34))
        painter.setPen(QColor(190, 200, 220))
        for index, pitch in enumerate(reversed(self.heatmap.midi_notes)):
            y = index * self.note_height
            if y > self.height():
                break
            is_black = pitch % 12 in {1, 3, 6, 8, 10}
            painter.fillRect(0, y, self.keyboard_width, self.note_height, QColor(32, 36, 48) if is_black else QColor(52, 57, 70))
            if pitch % 12 == 0:
                painter.drawText(4, y + self.note_height - 1, f"C{pitch // 12 - 1}")

    def _draw_heatmap(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        if not self.heatmap.frame_times:
            return
        clip = painter.clipBoundingRect()
        left = max(float(self.keyboard_width), clip.left() if not clip.isEmpty() else 0.0)
        right = min(float(self.width()), clip.right() if not clip.isEmpty() else float(self.width()))
        if right <= left:
            return

        frame_step_seconds = self._frame_step_seconds()
        display_frame_width = frame_step_seconds / self.seconds_per_pixel
        # For zoomed-in/short files, draw real frame rectangles. For long files,
        # aggregate into visible pixel columns so painting is bounded by screen
        # pixels rather than by every model frame in a large MP3/recording.
        if display_frame_width >= 0.75:
            self._draw_heatmap_frames(painter, left, right, max(1.0, display_frame_width))
        else:
            self._draw_heatmap_columns(painter, math.floor(left), math.ceil(right), frame_step_seconds)

    def _draw_heatmap_frames(self, painter: QPainter, left: float, right: float, frame_width: float) -> None:
        assert self.heatmap is not None
        first_frame = max(0, self._frame_index_at_x(left) - 1)
        last_frame = min(self.heatmap.frame_count, self._frame_index_at_x(right) + 2)
        for frame_index in range(first_frame, last_frame):
            time_seconds = self.heatmap.frame_times[frame_index]
            x = self.keyboard_width + time_seconds / self.seconds_per_pixel
            if x > right:
                break
            if x + frame_width < left:
                continue
            for note_index in range(self.heatmap.note_count):
                activation = self.heatmap.activation(frame_index, note_index)
                if activation <= 0.005:
                    continue
                y = (self.heatmap.note_count - 1 - note_index) * self.note_height
                painter.fillRect(QRectF(x, y, frame_width + 0.5, self.note_height), self._heat_color(activation))

    def _draw_heatmap_columns(self, painter: QPainter, left: int, right: int, frame_step_seconds: float) -> None:
        assert self.heatmap is not None
        start_x = max(self.keyboard_width, left)
        end_x = min(self.width(), right)
        for x in range(start_x, end_x + 1):
            start_time = max(0.0, (x - self.keyboard_width) * self.seconds_per_pixel)
            end_time = start_time + self.seconds_per_pixel
            start_frame = max(0, int(start_time / frame_step_seconds))
            end_frame = min(self.heatmap.frame_count, max(start_frame + 1, int(math.ceil(end_time / frame_step_seconds))))
            stride = max(1, (end_frame - start_frame) // 5)
            sampled_frames = range(start_frame, end_frame, stride)
            for note_index in range(self.heatmap.note_count):
                activation = max((self.heatmap.activation(frame_index, note_index) for frame_index in sampled_frames), default=0.0)
                if activation <= 0.005:
                    continue
                y = (self.heatmap.note_count - 1 - note_index) * self.note_height
                painter.fillRect(QRectF(x, y, 1.2, self.note_height), self._heat_color(activation))

    def _frame_step_seconds(self) -> float:
        assert self.heatmap is not None
        if len(self.heatmap.frame_times) < 2:
            return max(self.seconds_per_pixel, 0.001)
        return max(0.001, self.heatmap.frame_times[1] - self.heatmap.frame_times[0])

    def _frame_index_at_x(self, x: float) -> int:
        assert self.heatmap is not None
        frame_step_seconds = self._frame_step_seconds()
        seconds = max(0.0, (x - self.keyboard_width) * self.seconds_per_pixel)
        return max(0, min(self.heatmap.frame_count, int(seconds / frame_step_seconds)))

    def _draw_grid(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        for note_index, pitch in enumerate(reversed(self.heatmap.midi_notes)):
            if pitch % 12 == 0:
                y = note_index * self.note_height
                painter.drawLine(self.keyboard_width, y, self.width(), y)
        painter.setPen(QPen(QColor(255, 255, 255, 36), 1))
        duration = self.heatmap.duration_seconds
        interval = self._grid_interval_seconds()
        second = 0
        while second <= duration + interval:
            x = int(self.keyboard_width + second / self.seconds_per_pixel)
            painter.drawLine(x, 0, x, self.height())
            second += interval

    def _grid_interval_seconds(self) -> int:
        """Return a coarse-enough grid interval for long recordings."""

        target_pixels = 80
        raw_interval = max(1.0, target_pixels * self.seconds_per_pixel)
        for interval in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if interval >= raw_interval:
                return interval
        return int(math.ceil(raw_interval / 600.0) * 600)

    def _draw_notes(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        for list_index, note in enumerate(self.notes):
            rect = self._note_rect(note)
            if rect is None:
                continue
            is_selected = list_index == self.selected_note_index
            is_hovered = list_index == self.hover_note_index
            if is_selected:
                painter.setPen(QPen(QColor(255, 226, 88), 2))
                painter.setBrush(QColor(255, 186, 52, 170))
            elif is_hovered:
                painter.setPen(QPen(QColor(255, 206, 96), 2))
                painter.setBrush(QColor(230, 96, 36, 155))
            else:
                painter.setPen(QPen(QColor(255, 170, 66), 1))
                painter.setBrush(QColor(210, 72, 34, 135))
            painter.drawRect(rect)
            if is_selected or is_hovered:
                self._draw_note_handles(painter, rect, active=is_selected)

    def _draw_note_handles(self, painter: QPainter, rect: QRectF, *, active: bool) -> None:
        handle_width = min(6.0, max(3.0, rect.width() / 3.0))
        handle_color = QColor(255, 235, 132, 235) if active else QColor(255, 198, 90, 210)
        outline_color = QColor(55, 26, 10, 230)
        left = QRectF(rect.left(), rect.top(), handle_width, rect.height())
        right = QRectF(rect.right() - handle_width, rect.top(), handle_width, rect.height())
        painter.setPen(QPen(outline_color, 1))
        painter.setBrush(handle_color)
        painter.drawRect(left)
        painter.drawRect(right)

    def _note_rect(self, note: GuiMidiNote) -> QRectF | None:
        assert self.heatmap is not None
        note_to_index = {pitch: index for index, pitch in enumerate(self.heatmap.midi_notes)}
        if note.pitch not in note_to_index:
            return None
        note_index = note_to_index[note.pitch]
        x = self.keyboard_width + note.start_seconds / self.seconds_per_pixel
        y = (self.heatmap.note_count - 1 - note_index) * self.note_height
        width = max(2.0, note.duration_seconds / self.seconds_per_pixel)
        return QRectF(x, y + 1, width, self.note_height - 2)

    def _note_index_at(self, x: float, y: float) -> int | None:
        hit = self._note_hit_at(x, y)
        return hit[0] if hit is not None else None

    def _note_hit_at(self, x: float, y: float) -> tuple[int, str] | None:
        if self.heatmap is None or not self.show_notes:
            return None
        handle_width = 7.0
        # Reverse so the top-most/latest-drawn overlapping note wins.
        for index in range(len(self.notes) - 1, -1, -1):
            rect = self._note_rect(self.notes[index])
            if rect is None or not rect.contains(x, y):
                continue
            if abs(x - rect.left()) <= handle_width:
                return index, "resize_start"
            if abs(x - rect.right()) <= handle_width:
                return index, "resize_end"
            return index, "move"
        return None

    def _update_hover_state(self, x: float, y: float) -> None:
        hit = self._note_hit_at(x, y)
        note_index = hit[0] if hit is not None else None
        mode = hit[1] if hit is not None else None
        if note_index == self.hover_note_index and mode == self.hover_mode:
            return
        self.hover_note_index = note_index
        self.hover_mode = mode
        if mode in {"resize_start", "resize_end"}:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif mode == "move":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()
        self.update()

    def _edited_drag_note(self, x: float, y: float) -> GuiMidiNote | None:
        if self.drag_mode is None or self.drag_original_note is None:
            return None
        note = self.drag_original_note
        delta_seconds = (x - self.drag_start_x) * self.seconds_per_pixel
        if self.drag_mode == "resize_start":
            original_end = note.end_seconds
            start_seconds = min(max(0.0, note.start_seconds + delta_seconds), original_end - self.min_note_duration_seconds)
            return GuiMidiNote(
                pitch=note.pitch,
                start_seconds=start_seconds,
                duration_seconds=max(self.min_note_duration_seconds, original_end - start_seconds),
                velocity=note.velocity,
                source=note.source,
            )
        if self.drag_mode == "resize_end":
            end_seconds = max(note.start_seconds + self.min_note_duration_seconds, note.end_seconds + delta_seconds)
            return GuiMidiNote(
                pitch=note.pitch,
                start_seconds=note.start_seconds,
                duration_seconds=end_seconds - note.start_seconds,
                velocity=note.velocity,
                source=note.source,
            )
        if self.drag_mode == "move":
            pitch = self._pitch_at_y(y)
            return GuiMidiNote(
                pitch=note.pitch if pitch is None else pitch,
                start_seconds=max(0.0, note.start_seconds + delta_seconds),
                duration_seconds=note.duration_seconds,
                velocity=note.velocity,
                source=note.source,
            )
        return None

    def _pitch_at_y(self, y: float) -> int | None:
        if self.heatmap is None:
            return None
        row = int(y // self.note_height)
        note_index = self.heatmap.note_count - 1 - row
        if 0 <= note_index < self.heatmap.note_count:
            return self.heatmap.midi_notes[note_index]
        return None

    def _draw_playhead(self, painter: QPainter) -> None:
        if self.heatmap is None:
            return
        x = int(self.keyboard_width + self.playhead_seconds / self.seconds_per_pixel)
        if x < self.keyboard_width or x > self.width():
            return
        painter.setPen(QPen(QColor(255, 230, 120), 2))
        painter.drawLine(x, 0, x, self.height())

    @staticmethod
    def _heat_color(value: float) -> QColor:
        value = max(0.0, min(1.0, value))
        return QColor(
            int(45 + 210 * value),
            int(18 + 150 * value),
            int(8 + 22 * (1.0 - value)),
            int(42 + 205 * value),
        )
