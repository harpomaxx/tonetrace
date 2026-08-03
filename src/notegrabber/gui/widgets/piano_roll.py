"""Heatmap + MIDI rectangle piano-roll widget."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QRect, QSize, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
from notegrabber.gui.theme import active_theme, qcolor

# Chromatic note names, index 0 == C. Combined with the octave (MIDI pitch // 12
# - 1) this yields labels like "C4", "F#3" -- matching the "C4" octave marks the
# keyboard already draws.
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _pitch_note_name(pitch: int) -> str:
    """Return the note name with octave for a MIDI pitch, e.g. 60 -> 'C4'."""

    return f"{_NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


class PianoRollWidget(QWidget):
    """Draw a piano-roll heatmap with extracted MIDI note rectangles."""

    seek_requested = Signal(float)
    note_selected = Signal(int, float)
    note_edited = Signal(int, float, float, int, int, bool)
    # (start_seconds, duration_seconds, pitch, velocity) for a note created by
    # double-clicking empty space.
    note_created = Signal(float, float, int, int)
    # Emitted whenever the selected *set* changes (issue #35). note_selected
    # still fires for the single-note case so the inspector keeps working.
    selection_changed = Signal(object)
    zoom_changed = Signal(float)
    vertical_zoom_changed = Signal(float)

    # A sounding key's name is only drawn on the keyboard when the row is tall
    # enough for legible text; when zoomed out so rows are short it would be
    # unreadable clutter, so it is hidden (highlight only).
    _LABEL_MIN_ROW_HEIGHT = 11

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.heatmap: GuiHeatmap | None = None
        self.notes: list[GuiMidiNote] = []
        self.full_duration_seconds = 0.0
        self._pitch_to_row: dict[int, int] = {}
        # note indices bucketed by their pitch row, so hover hit-testing scans
        # only the handful of notes on the row under the cursor instead of every
        # note. Rebuilt in set_data; patched in preview_note_edit during a drag.
        self._notes_by_row: dict[int, list[int]] = {}
        # Cached QImage of the zoomed-out heatmap (one canvas pixel per column,
        # one image row per note) plus the params it was built under, so a repaint
        # can blit it instead of re-drawing every cell. See _heatmap_image().
        self._heatmap_image: Any = None
        self._heatmap_image_key: tuple | None = None
        self._heatmap_image_span: tuple[int, int] = (0, 0)
        self.show_notes = True
        self.show_heatmap = True
        self.show_pitch_bends = True
        self.selected_indices: set[int] = set()
        # Rubber-band drag over empty space (issue #35). Both are canvas points;
        # None means no band is in progress.
        self.rubber_start: tuple[float, float] | None = None
        self.rubber_current: tuple[float, float] | None = None
        self.rubber_base_selection: set[int] = set()
        self.hover_note_index: int | None = None
        self.hover_mode: str | None = None
        self.playhead_seconds = 0.0
        self._last_active_pitches: set[int] = set()
        self.seconds_per_pixel = 0.01
        self.fit_seconds_per_pixel = 0.01
        self.horizontal_zoom = 1.0
        self.vertical_zoom = 1.0
        self.base_note_height = 7
        self.note_height = self.base_note_height
        self.keyboard_width = 54
        self.drag_mode: str | None = None
        self.drag_note_index: int | None = None
        self.drag_start_x = 0.0
        self.drag_start_y = 0.0
        self.drag_original_note: GuiMidiNote | None = None
        self.drag_has_moved = False
        self.drag_threshold_pixels = 3.0
        self.min_note_duration_seconds = 0.001
        # Defaults for a note created by double-clicking empty space (issue #37).
        # A fixed duration rather than the grid interval: _grid_interval_seconds
        # is whole seconds (>= 1s), which is far too long for a single note.
        self.new_note_duration_seconds = 0.25
        self.new_note_velocity = 90
        self.minimum_canvas_width = 760
        self.minimum_canvas_height = 420
        self.setMouseTracking(True)
        self.setToolTip("Click notes to select. Drag note bodies to move time/pitch; drag edges to resize. Double-click empty space to add a note. Ctrl+wheel zooms time; Shift+wheel changes note height.")
        self.setMinimumSize(self.minimum_canvas_width, self.minimum_canvas_height)

    def set_data(
        self,
        heatmap: GuiHeatmap | None,
        notes: list[GuiMidiNote],
        full_duration_seconds: float | None = None,
    ) -> None:
        """Update heatmap and notes.

        ``full_duration_seconds`` is the full-song duration the timeline should
        span, so a range analysis inside a long file shares one time->x scale
        with the waveform overview.  It defaults to the heatmap duration, which
        keeps whole-file analysis unchanged.  Crucially, fitting to the full song
        (not the analysed range) keeps the canvas width bounded to roughly the
        viewport at zoom 1.0 for any file length, so huge MP3s do not create a
        giant widget.
        """

        heatmap_changed = heatmap is not self.heatmap
        if heatmap_changed:
            self._invalidate_heatmap_image()
        self.heatmap = heatmap
        self.notes = notes
        self.full_duration_seconds = max(0.0, float(full_duration_seconds or 0.0))
        if heatmap_changed:
            # Cache the pitch -> row lookup so per-note draw and per-mousemove
            # hit-testing do not rebuild this dict on every call.
            self._pitch_to_row = (
                {pitch: index for index, pitch in enumerate(heatmap.midi_notes)}
                if heatmap is not None
                else {}
            )
        self._rebuild_note_index()
        stale = {index for index in self.selected_indices if index >= len(notes)}
        if stale:
            self.selected_indices -= stale
        if self.hover_note_index is not None and self.hover_note_index >= len(notes):
            self.hover_note_index = None
            self.hover_mode = None
        if heatmap is not None and heatmap_changed:
            duration = self._timeline_duration_seconds()
            available_width = self._fit_available_width()
            self.fit_seconds_per_pixel = max(0.0005, duration / available_width)
            self.seconds_per_pixel = max(0.0005, self.fit_seconds_per_pixel / self.horizontal_zoom)
        self._update_canvas_size()
        self.updateGeometry()
        self.update()

    def _invalidate_heatmap_image(self) -> None:
        """Drop the cached heatmap image so the next repaint rebuilds it.

        Called whenever anything the image depends on changes: the heatmap data,
        the horizontal scale (``seconds_per_pixel``), or the canvas width. It does
        *not* need calling on vertical zoom -- the image stores one row per note
        and is scaled to ``note_height`` at blit time -- nor on horizontal scroll,
        which only changes which slice is blitted.
        """

        self._heatmap_image = None
        self._heatmap_image_key = None
        self._heatmap_image_span = (0, 0)

    def _timeline_duration_seconds(self) -> float:
        """Total time span the canvas represents (full song when known)."""

        if self.heatmap is None:
            return max(self.full_duration_seconds, 1.0)
        return max(
            self.full_duration_seconds,
            self.heatmap.duration_seconds,
            max((note.end_seconds for note in self.notes), default=0.0),
            1.0,
        )

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self.minimumWidth(), self.minimumHeight())

    def _fit_available_width(self) -> int:
        """Return viewport width for fit calculations, not the zoomed canvas width."""

        parent = self.parentWidget()
        if parent is not None and parent.width() > self.keyboard_width + 100:
            return max(300, parent.width() - self.keyboard_width)
        return max(300, min(self.width(), self.minimum_canvas_width) - self.keyboard_width)

    def _update_canvas_size(self) -> None:
        if self.heatmap is None:
            self.setMinimumSize(self.minimum_canvas_width, self.minimum_canvas_height)
            return
        full_note_height = max(self.minimum_canvas_height, self.heatmap.note_count * self.note_height)
        duration = self._timeline_duration_seconds()
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

    def set_vertical_zoom(self, zoom: float) -> None:
        """Set vertical pitch zoom by changing the rendered note-row height."""

        self.vertical_zoom = max(0.75, min(6.0, float(zoom)))
        self.note_height = max(4, round(self.base_note_height * self.vertical_zoom))
        self._update_canvas_size()
        self.updateGeometry()
        self.update()

    def set_show_notes(self, enabled: bool) -> None:
        self.show_notes = enabled
        self.update()

    def set_show_heatmap(self, enabled: bool) -> None:
        self.show_heatmap = enabled
        self.update()

    def set_show_pitch_bends(self, enabled: bool) -> None:
        self.show_pitch_bends = enabled
        self.update()

    def preview_note_edit(self, index: int, note: GuiMidiNote) -> None:
        """Update a single note in place with a partial repaint.

        Used during an uncommitted drag so intermediate moves do not run the full
        ``set_data`` path (canvas resize + full-canvas repaint + sequence-table
        rebuild). Only the old and new note rectangles are repainted.
        """

        if index < 0 or index >= len(self.notes) or self.heatmap is None:
            return
        old_note = self.notes[index]
        old_rect = self._note_rect(old_note)
        self.notes[index] = note
        new_rect = self._note_rect(note)
        # Keep the row buckets consistent if the drag moved this note's pitch.
        old_row = self._pitch_to_row.get(old_note.pitch)
        new_row = self._pitch_to_row.get(note.pitch)
        if old_row != new_row:
            if old_row is not None:
                bucket = self._notes_by_row.get(old_row)
                if bucket is not None and index in bucket:
                    bucket.remove(index)
            if new_row is not None:
                self._notes_by_row.setdefault(new_row, []).append(index)
        dirty = self._union_note_dirty_rect(old_rect, new_rect)
        if dirty is None:
            self.update()
        else:
            self.update(dirty)

    def _union_note_dirty_rect(self, *rects: QRectF | None) -> QRect | None:
        """Union note rects into an inflated integer repaint region."""

        region: QRect | None = None
        for rect in rects:
            if rect is None:
                continue
            # Inflate for the 2px selection outline and resize handles.
            inflated = rect.toRect().adjusted(-4, -4, 4, 4)
            region = inflated if region is None else region.united(inflated)
        return region

    @property
    def selected_note_index(self) -> int | None:
        """The single selected note, or None when zero or several are selected.

        Kept so single-selection callers (the note inspector, edit-apply) read
        naturally; the set in ``selected_indices`` is the source of truth.
        """

        if len(self.selected_indices) != 1:
            return None
        return next(iter(self.selected_indices))

    def set_selected_note_index(self, index: int | None) -> None:
        """Replace the selection with a single note, or clear it."""

        if index is None or not 0 <= index < len(self.notes):
            self.set_selected_indices(set())
            return
        self.set_selected_indices({index})

    def set_selected_indices(self, indices: set[int]) -> None:
        """Replace the whole selection, dropping any out-of-range index."""

        valid = {index for index in indices if 0 <= index < len(self.notes)}
        if valid == self.selected_indices:
            return
        self.selected_indices = valid
        self.selection_changed.emit(set(valid))
        self.update()

    def toggle_selected_index(self, index: int) -> None:
        """Add or remove one note from the selection (shift-click)."""

        if not 0 <= index < len(self.notes):
            return
        updated = set(self.selected_indices)
        updated.discard(index) if index in updated else updated.add(index)
        self.set_selected_indices(updated)

    def set_playhead(self, seconds: float) -> None:
        """Set the drawn playhead position without repainting the full heatmap."""

        old_seconds = self.playhead_seconds
        self.playhead_seconds = max(0.0, seconds)
        if self.heatmap is None:
            self.update()
            return
        dirty = self._playhead_update_rect(old_seconds).united(self._playhead_update_rect(self.playhead_seconds))
        # The sounding keys on the keyboard change only when the playhead crosses a
        # note boundary. Repaint the (left-pinned) keyboard strip only then, so a
        # normal playback tick stays as cheap as a thin playhead-strip update.
        active = self._active_pitches()
        if active != self._last_active_pitches:
            self._last_active_pitches = active
            dirty = dirty.united(self._keyboard_update_rect())
        self.update(dirty)

    def _playhead_update_rect(self, seconds: float) -> QRect:
        x = int(self.keyboard_width + max(0.0, seconds) / self.seconds_per_pixel)
        return QRect(max(0, x - 3), 0, 7, self.height())

    def _keyboard_update_rect(self) -> QRect:
        """Repaint region for the left-pinned keyboard strip at its current offset."""

        left = self._horizontal_scroll_offset()
        return QRect(left, 0, self.keyboard_width, self.height())

    def x_for_seconds(self, seconds: float) -> int:
        """Return the canvas x pixel for a time in seconds (keyboard offset included)."""

        return int(self.keyboard_width + max(0.0, seconds) / self.seconds_per_pixel)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.heatmap is not None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            delta_y = event.angleDelta().y() or event.pixelDelta().y()
            if delta_y:
                # Anchor on the cursor so the pointed-at pitch stays put.
                self.vertical_zoom_by_wheel_delta(delta_y, anchor_y=float(event.position().y()))
                event.accept()
                return
        if self.heatmap is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y() or event.pixelDelta().y()
            if delta_y:
                # Anchor on the cursor so the pointed-at moment stays put.
                self.zoom_by_wheel_delta(delta_y, anchor_x=float(event.position().x()))
                event.accept()
                return
        super().wheelEvent(event)

    def zoom_by_wheel_delta(self, delta_y: int, anchor_x: float | None = None) -> None:
        """Zoom horizontally from a wheel delta; positive delta zooms in.

        ``anchor_x`` is a canvas x to hold still (issue #10): the time under the
        cursor stays under the cursor instead of the view zooming around the left
        edge, which used to push the point of interest sideways off screen. When
        omitted, the viewport centre is held instead, so keyboard/button zoom
        does not jump the view either.
        """

        factor = 1.2 ** (delta_y / 120.0)
        self.zoom_to(self.horizontal_zoom * factor, anchor_x=anchor_x)

    def zoom_to(self, zoom: float, *, anchor_x: float | None = None) -> None:
        """Set horizontal zoom while keeping ``anchor_x``'s time under itself."""

        bar = self._horizontal_scroll_bar()
        if anchor_x is None:
            # No cursor to anchor: hold the middle of what is currently visible.
            anchor_x = self._horizontal_scroll_offset() + self._viewport_width() / 2.0
        # Time under the anchor, and where the anchor sits inside the viewport;
        # both must be measured before the scale changes.
        anchor_seconds = self.seconds_at_x(anchor_x)
        offset_in_viewport = anchor_x - self._horizontal_scroll_offset()

        self.set_horizontal_zoom(zoom)

        if bar is not None:
            # Solve for the scroll offset that puts anchor_seconds back at the
            # same spot in the viewport under the new seconds_per_pixel.
            target = self.x_for_seconds(anchor_seconds) - offset_in_viewport
            bar.setValue(max(0, round(target)))
        self.zoom_changed.emit(self.horizontal_zoom)

    def _viewport_width(self) -> int:
        """Width of the visible slice of the canvas, not the zoomed canvas."""

        viewport = self.parentWidget()
        if viewport is not None and viewport.width() > 0:
            return viewport.width()
        return max(1, self.width())

    def seconds_at_x(self, x: float) -> float:
        """Inverse of x_for_seconds: the time a canvas x represents."""

        return max(0.0, (float(x) - self.keyboard_width) * self.seconds_per_pixel)

    def vertical_zoom_by_wheel_delta(self, delta_y: int, anchor_y: float | None = None) -> None:
        """Zoom note height from a wheel delta; positive delta makes rows taller.

        ``anchor_y`` is a canvas y to hold still (issue #10). Rows grow downward
        from the top of the canvas, so without an anchor the pitch under the
        cursor slides away fast -- nearly three octaves over five wheel clicks.
        """

        factor = 1.2 ** (delta_y / 120.0)
        self.vertical_zoom_to(self.vertical_zoom * factor, anchor_y=anchor_y)

    def vertical_zoom_to(self, zoom: float, *, anchor_y: float | None = None) -> None:
        """Set vertical zoom while keeping ``anchor_y``'s pitch row under itself."""

        bar = self._vertical_scroll_bar()
        if anchor_y is None:
            # No cursor to anchor: hold the middle of what is currently visible.
            anchor_y = self._vertical_scroll_offset() + self._viewport_height() / 2.0
        # The fractional row under the anchor, and where the anchor sits inside
        # the viewport; both must be measured before note_height changes.
        anchor_row = self._row_at_y_exact(anchor_y)
        offset_in_viewport = anchor_y - self._vertical_scroll_offset()

        self.set_vertical_zoom(zoom)

        if bar is not None:
            # Solve for the scroll offset that puts anchor_row back at the same
            # spot in the viewport under the new note_height.
            target = anchor_row * self.note_height - offset_in_viewport
            bar.setValue(max(0, round(target)))
        self.vertical_zoom_changed.emit(self.vertical_zoom)

    def _row_at_y_exact(self, y: float) -> float:
        """Fractional row-from-top at a canvas y (unclamped, unlike _row_for_y)."""

        if self.note_height <= 0:
            return 0.0
        return max(0.0, float(y)) / self.note_height

    def _vertical_scroll_offset(self) -> int:
        """Return how far the canvas is scrolled down inside its scroll area."""

        bar = self._vertical_scroll_bar()
        if bar is None:
            return 0
        return max(0, bar.value())

    def _viewport_height(self) -> int:
        """Height of the visible slice of the canvas, not the zoomed canvas."""

        viewport = self.parentWidget()
        if viewport is not None and viewport.height() > 0:
            return viewport.height()
        return max(1, self.height())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.heatmap is not None and event.button() == Qt.MouseButton.LeftButton:
            x = float(event.position().x())
            y = float(event.position().y())
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            hit = self._note_hit_at(x, y)
            if hit is not None:
                note_index, mode = hit
                if shift:
                    # Shift-click toggles membership and starts no drag, so a
                    # stray movement cannot edit a note the user is deselecting.
                    self.drag_mode = None
                    self.drag_note_index = None
                    self.drag_original_note = None
                    self.toggle_selected_index(note_index)
                else:
                    self.drag_mode = mode
                    self.drag_note_index = note_index
                    self.drag_start_x = x
                    self.drag_start_y = y
                    self.drag_original_note = self.notes[note_index]
                    self.drag_has_moved = False
                    # Clicking a note already in a multi-selection keeps the set
                    # (so a drag can act on it); otherwise the click replaces it.
                    if note_index not in self.selected_indices:
                        self.set_selected_note_index(note_index)
                    note = self.notes[note_index]
                    self.note_selected.emit(note_index, note.start_seconds)
                    self.seek_requested.emit(note.start_seconds)
            else:
                # Empty space arms a rubber-band. It only becomes a selection
                # once the pointer moves past the drag threshold; releasing
                # without moving seeks, exactly as a plain click always has.
                self.drag_mode = None
                self.drag_note_index = None
                self.drag_original_note = None
                self.drag_start_x = x
                self.drag_start_y = y
                self.drag_has_moved = False
                self.rubber_start = (x, y)
                self.rubber_current = None
                self.rubber_base_selection = set(self.selected_indices) if shift else set()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Double-clicking empty space inserts a note there (issue #37).

        Double-clicking *on* a note does nothing new -- the press that precedes
        this event already selected it and armed a move/resize drag.  Empty space
        is shared with single-click seek, which has already fired for the first
        click of the double; creation is additive rather than replacing it.
        """

        if self.heatmap is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        x = float(event.position().x())
        y = float(event.position().y())
        if self._note_hit_at(x, y) is not None:
            super().mouseDoubleClickEvent(event)
            return
        pitch = self._pitch_at_y(y)
        x_after_keyboard = x - self.keyboard_width
        if pitch is None or x_after_keyboard < 0:
            super().mouseDoubleClickEvent(event)
            return
        # A creation double-click must not leave a half-armed drag behind: the
        # preceding press on empty space cleared it, but be explicit.
        self.drag_mode = None
        self.drag_note_index = None
        self.drag_original_note = None
        self.drag_has_moved = False
        start_seconds = max(0.0, x_after_keyboard * self.seconds_per_pixel)
        self.note_created.emit(
            start_seconds,
            self.new_note_duration_seconds,
            int(pitch),
            int(self.new_note_velocity),
        )
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        x = float(event.position().x())
        y = float(event.position().y())
        if self.drag_mode is not None and self.drag_note_index is not None and self.drag_original_note is not None:
            if not self.drag_has_moved:
                distance = math.hypot(x - self.drag_start_x, y - self.drag_start_y)
                if distance < self.drag_threshold_pixels:
                    super().mouseMoveEvent(event)
                    return
                self.drag_has_moved = True
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
        elif self.rubber_start is not None:
            if not self.drag_has_moved:
                distance = math.hypot(x - self.drag_start_x, y - self.drag_start_y)
                if distance < self.drag_threshold_pixels:
                    super().mouseMoveEvent(event)
                    return
                self.drag_has_moved = True
            self.rubber_current = (x, y)
            self.set_selected_indices(
                self.rubber_base_selection | self._notes_in_rect(self._rubber_rect())
            )
        else:
            self._update_hover_state(x, y)
        super().mouseMoveEvent(event)

    def _rubber_rect(self) -> QRectF | None:
        """The current rubber-band rectangle, normalized, or None."""

        if self.rubber_start is None or self.rubber_current is None:
            return None
        (x0, y0), (x1, y1) = self.rubber_start, self.rubber_current
        return QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _notes_in_rect(self, rect: QRectF | None) -> set[int]:
        """Indices of every note whose rectangle intersects ``rect``."""

        if rect is None or not self.show_notes:
            return set()
        selected = set()
        for index, note in enumerate(self.notes):
            note_rect = self._note_rect(note)
            if note_rect is not None and rect.intersects(note_rect):
                selected.add(index)
        return selected

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.drag_mode is not None and self.drag_note_index is not None and self.drag_original_note is not None and self.drag_has_moved:
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
        elif self.rubber_start is not None:
            if self.drag_has_moved:
                # The band already applied the selection on every move; just
                # settle it and let go of the band.
                self.set_selected_indices(
                    self.rubber_base_selection | self._notes_in_rect(self._rubber_rect())
                )
            else:
                # Pressed and released without moving: that is a plain click on
                # empty space, which clears the selection and seeks as before.
                self.set_selected_indices(set())
                x_after_keyboard = self.rubber_start[0] - self.keyboard_width
                if x_after_keyboard >= 0:
                    self.seek_requested.emit(max(0.0, x_after_keyboard * self.seconds_per_pixel))
        self._clear_rubber_band()
        self.drag_mode = None
        self.drag_note_index = None
        self.drag_original_note = None
        self.drag_has_moved = False
        self._update_hover_state(float(event.position().x()), float(event.position().y()))
        super().mouseReleaseEvent(event)

    def _clear_rubber_band(self) -> None:
        """Drop any in-progress band and repaint away its rectangle."""

        if self.rubber_start is None and self.rubber_current is None:
            return
        self.rubber_start = None
        self.rubber_current = None
        self.rubber_base_selection = set()
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.hover_note_index = None
        self.hover_mode = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        theme = active_theme()
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor(theme.canvas_bg))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        if self.heatmap is None:
            painter.setPen(qcolor(theme.text_dim))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Analyze audio to show heatmap and MIDI notes")
            return

        if self.show_heatmap:
            self._draw_heatmap(painter)
        self._draw_grid(painter)
        if self.show_notes:
            self._draw_notes(painter)
            if self.show_pitch_bends:
                self._draw_bend_curves(painter)
        self._draw_playhead(painter)
        self._draw_rubber_band(painter)
        # Keyboard is drawn last and pinned to the visible left edge so it stays
        # in view (opaque, over the content) while scrolling horizontally.
        self._draw_keyboard(painter)

    def _draw_rubber_band(self, painter: QPainter) -> None:
        """Draw the in-progress selection rectangle (issue #35)."""

        rect = self._rubber_rect()
        if rect is None:
            return
        color = qcolor(active_theme().note_selected)
        painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 48))
        painter.drawRect(rect)

    def _horizontal_scroll_bar(self):
        """Return the enclosing scroll area's horizontal bar, or None."""

        return self._scroll_bar("horizontalScrollBar")

    def _vertical_scroll_bar(self):
        """Return the enclosing scroll area's vertical bar, or None."""

        return self._scroll_bar("verticalScrollBar")

    def _scroll_bar(self, accessor: str):
        """Return one of the enclosing scroll area's bars, or None."""

        viewport = self.parentWidget()
        if viewport is None:
            return None
        scroll_area = viewport.parentWidget()
        bar = getattr(scroll_area, accessor, None)
        return None if bar is None else bar()

    def _horizontal_scroll_offset(self) -> int:
        """Return how far the canvas is scrolled left inside its scroll area.

        The piano roll lives in a QScrollArea; when zoomed in, the canvas is
        wider than the viewport and can scroll horizontally. This offset lets the
        keyboard pin itself to the visible left edge instead of scrolling away.
        """

        bar = self._horizontal_scroll_bar()
        if bar is None:
            return 0
        return max(0, bar.value())

    def _visible_x_range(self) -> tuple[float, float]:
        """Return the (left, right) canvas x range currently visible in the viewport.

        When the widget is inside a QScrollArea this is the scroll offset plus the
        viewport width; otherwise it is the whole widget. Used to bound painting to
        what is on screen rather than the full zoomed-in canvas.
        """

        offset = self._horizontal_scroll_offset()
        viewport = self.parentWidget()
        viewport_width = viewport.width() if viewport is not None else self.width()
        left = float(offset)
        right = float(min(self.width(), offset + viewport_width))
        return left, right

    def _active_pitches(self) -> set[int]:
        """Return the set of note pitches sounding at the current playhead time.

        A note is sounding when the playhead is within [start, end). Used to
        highlight the corresponding keys on the keyboard during playback.
        """

        t = self.playhead_seconds
        return {note.pitch for note in self.notes if note.start_seconds <= t < note.end_seconds}

    def _draw_keyboard(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        # Pin to the visible left edge so the keyboard stays put while the
        # heatmap scrolls horizontally underneath it.
        theme = active_theme()
        left = self._horizontal_scroll_offset()
        active = self._active_pitches()
        painter.fillRect(left, 0, self.keyboard_width, self.height(), qcolor(theme.keyboard_bg))
        base_font = painter.font()
        bold_font = QFont(base_font)
        bold_font.setBold(True)
        # Only label a sounding key if the row is tall enough for legible text;
        # when zoomed out so rows are short, keep the strip clean (highlight only).
        label_active = self.note_height >= self._LABEL_MIN_ROW_HEIGHT
        for index, pitch in enumerate(reversed(self.heatmap.midi_notes)):
            y = index * self.note_height
            if y > self.height():
                break
            is_black = pitch % 12 in {1, 3, 6, 8, 10}
            if pitch in active:
                # A key sounding at the playhead: warm accent, matching the playhead.
                key_color = qcolor(theme.key_active)
            else:
                key_color = qcolor(theme.key_black) if is_black else qcolor(theme.key_white)
            painter.fillRect(left, y, self.keyboard_width, self.note_height, key_color)
            if pitch in active and label_active:
                # Name the sounding key on the key itself: bold, dark for contrast
                # against the warm highlight.
                painter.setFont(bold_font)
                painter.setPen(QColor(24, 16, 6))
                painter.drawText(
                    QRect(left, y, self.keyboard_width - 3, self.note_height),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    _pitch_note_name(pitch),
                )
                painter.setFont(base_font)
            elif pitch % 12 == 0:
                painter.setPen(qcolor(theme.text_dim))
                painter.drawText(left + 4, y + self.note_height - 1, _pitch_note_name(pitch))

    def _draw_heatmap(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        if not self.heatmap.frame_times:
            return
        # Bound painting to the visible viewport, not the full (possibly huge)
        # canvas. A scroll repaint clips to the exposed rect; a full update() (e.g.
        # from zoom) has an empty clip, so fall back to the scroll-area viewport
        # range -- otherwise the whole zoomed-in timeline would be rebuilt/painted.
        vis_left, vis_right = self._visible_x_range()
        clip = painter.clipBoundingRect()
        left = max(float(self.keyboard_width), clip.left() if not clip.isEmpty() else vis_left)
        right = min(clip.right() if not clip.isEmpty() else vis_right, vis_right)
        if right <= left:
            return

        frame_step_seconds = self._frame_step_seconds()
        display_frame_width = frame_step_seconds / self.seconds_per_pixel
        # Use the blit for anything up to a few pixels per frame: there the heatmap
        # is still essentially one column per pixel, so the cached image renders it
        # identically and cheaply. Only when frames are genuinely wide (several px
        # each, so few of them fit the viewport) do per-frame fillRects become both
        # necessary for crispness and cheap enough. This keeps deep zoom on a dense
        # heatmap interactive instead of doing ~100k fillRects per repaint.
        if display_frame_width < self._BLIT_MAX_FRAME_WIDTH and self._blit_heatmap_columns(
            painter, math.floor(left), math.ceil(right)
        ):
            return
        if display_frame_width >= 0.75:
            self._draw_heatmap_frames(painter, left, right, max(1.0, display_frame_width))
        else:
            # Blit unavailable (no numpy) and frames too narrow: per-cell fallback.
            self._draw_heatmap_columns(painter, math.floor(left), math.ceil(right), frame_step_seconds)

    def _blit_heatmap_columns(self, painter: QPainter, left: int, right: int) -> bool:
        """Blit the cached heatmap image for the visible column span.

        Returns ``False`` if the image could not be built (e.g. numpy missing), so
        the caller can fall back to the per-cell path. The cached image is one
        canvas pixel wide per column and one pixel tall per note row; ``drawImage``
        scales it vertically to ``note_height`` with smoothing off, so each note
        row expands by exact pixel replication (no color bleed between rows).
        """

        assert self.heatmap is not None
        note_count = self.heatmap.note_count
        start_x = max(self.keyboard_width, left)
        end_x = min(self.width(), right)
        if end_x <= start_x:
            return True
        # Column offsets are canvas-x minus the keyboard; the cached image only
        # covers the visible span, so blit failure means "no numpy" -> fall back.
        col_start = start_x - self.keyboard_width
        col_end = end_x - self.keyboard_width
        image, span_start = self._heatmap_image_for_span(col_start, col_end)
        if image is None:
            return False
        # Sub-slice the cached span image to the requested visible columns.
        src_left = col_start - span_start
        src_width = min(col_end - col_start, image.width() - src_left)
        if src_width <= 0:
            return True
        source = QRect(src_left, 0, src_width, note_count)
        target = QRect(self.keyboard_width + col_start, 0, src_width, note_count * self.note_height)
        was_smooth = painter.testRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(target, image, source)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, was_smooth)
        return True

    # Extra columns rendered on each side of the visible span so that small
    # scrolls reuse the cached image instead of forcing a rebuild every step.
    _HEATMAP_SPAN_MARGIN = 512

    # Blit the cached image while each model frame is narrower than this many
    # pixels; above it, frames are wide and few, so per-frame fillRects are crisp
    # and cheap. 4px keeps the per-frame path's on-screen frame count bounded
    # (<~viewport_width/4) while giving the blit the whole dense-zoom range.
    _BLIT_MAX_FRAME_WIDTH = 4.0

    def _heatmap_image_for_span(self, col_start: int, col_end: int) -> tuple[Any, int]:
        """Return (image, span_start_column) covering the visible column range.

        The image is built only for the visible span (plus a margin), not the full
        timeline, so its cost is bounded by the viewport width regardless of zoom
        or song length. Reused while the requested range stays inside the cached
        span and the scale is unchanged; horizontal scroll past the margin, zoom,
        or a new heatmap rebuilds it. Vertical zoom does not (rows scale at blit).
        """

        assert self.heatmap is not None
        scale_key = (
            id(self.heatmap),
            active_theme().id,  # a theme change recolors the ramp -> rebuild the image
            round(self.seconds_per_pixel, 9),
            round(self._first_frame_seconds(), 6),
            round(self._frame_step_seconds(), 9),
        )
        cache = self._heatmap_image
        if (
            cache is not None
            and self._heatmap_image_key == scale_key
            and self._heatmap_image_span[0] <= col_start
            and col_end <= self._heatmap_image_span[1]
        ):
            return cache, self._heatmap_image_span[0]

        # Build a margin-padded span, clamped to the non-negative column range.
        span_start = max(0, col_start - self._HEATMAP_SPAN_MARGIN)
        span_end = col_end + self._HEATMAP_SPAN_MARGIN
        image = self._build_heatmap_image(span_start, span_end - span_start)
        if image is None:
            self._heatmap_image = None
            self._heatmap_image_key = None
            return None, 0
        self._heatmap_image = image
        self._heatmap_image_key = scale_key
        self._heatmap_image_span = (span_start, span_start + image.width())
        return image, span_start

    def _build_heatmap_image(self, col_start: int, width: int) -> Any:
        """Rasterize a column span of the aggregated heatmap into an ARGB32 QImage.

        Columns ``[col_start, col_start + width)`` are canvas pixels (keyboard
        offset already removed). One image row per note. Each column takes the same
        stride-sampled ``max`` over its frame window that the per-cell path uses, so
        transient one-frame activations survive at low zoom. Returns ``None`` when
        numpy is unavailable so the caller falls back to per-cell.
        """

        assert self.heatmap is not None
        try:
            import numpy as np  # type: ignore[import-not-found]
        except Exception:
            return None
        from PySide6.QtGui import QImage

        matrix = self.heatmap.activation_matrix()
        note_count = self.heatmap.note_count
        if matrix is None or width <= 0 or note_count == 0:
            return None
        frame_count = self.heatmap.frame_count
        frame_step = self._frame_step_seconds()
        first_frame_seconds = self._first_frame_seconds()

        # (width, note_count) activation, note rows top-to-bottom (high pitch first)
        # to match the fillRect path's y = (note_count - 1 - note_index) layout.
        column_max = np.zeros((width, note_count), dtype=np.float32)
        for i in range(width):
            px = col_start + i
            start_time = px * self.seconds_per_pixel - first_frame_seconds
            end_time = start_time + self.seconds_per_pixel
            if end_time <= 0.0:
                continue
            start_frame = int(start_time / frame_step) if start_time > 0 else 0
            if start_frame >= frame_count:
                break
            end_frame = min(frame_count, max(start_frame + 1, int(math.ceil(end_time / frame_step))))
            stride = max(1, (end_frame - start_frame) // 5)
            column_max[i] = matrix[start_frame:end_frame:stride].max(axis=0)

        # Flip note axis so row 0 is the top (highest pitch), then LUT-map to RGBA.
        rows_top_down = column_max[:, ::-1].T  # (note_count, width)
        indices = np.clip(rows_top_down * 255.0, 0, 255).astype(np.uint8)
        rgba = self._heat_rgba_lut()[indices]  # (note_count, width, 4)
        rgba = np.ascontiguousarray(rgba)
        image = QImage(rgba.data, width, note_count, width * 4, QImage.Format.Format_ARGB32)
        # QImage does not own the numpy buffer; copy so it stays valid after return.
        image = image.copy()
        return image

    def _draw_heatmap_frames(self, painter: QPainter, left: float, right: float, frame_width: float) -> None:
        assert self.heatmap is not None
        first_frame = max(0, self._frame_index_at_x(left) - 1)
        last_frame = min(self.heatmap.frame_count, self._frame_index_at_x(right) + 2)
        matrix = self.heatmap.activation_matrix()
        note_count = self.heatmap.note_count
        for frame_index in range(first_frame, last_frame):
            time_seconds = self.heatmap.frame_times[frame_index]
            x = self.keyboard_width + time_seconds / self.seconds_per_pixel
            if x > right:
                break
            if x + frame_width < left:
                continue
            if matrix is not None:
                row = matrix[frame_index]
                for note_index in self._active_rows(row):
                    y = (note_count - 1 - note_index) * self.note_height
                    painter.fillRect(QRectF(x, y, frame_width + 0.5, self.note_height), self._heat_color(float(row[note_index])))
            else:
                for note_index in range(note_count):
                    activation = self.heatmap.activation(frame_index, note_index)
                    if activation <= 0.005:
                        continue
                    y = (note_count - 1 - note_index) * self.note_height
                    painter.fillRect(QRectF(x, y, frame_width + 0.5, self.note_height), self._heat_color(activation))

    def _draw_heatmap_columns(self, painter: QPainter, left: int, right: int, frame_step_seconds: float) -> None:
        assert self.heatmap is not None
        start_x = max(self.keyboard_width, left)
        end_x = min(self.width(), right)
        matrix = self.heatmap.activation_matrix()
        frame_count = self.heatmap.frame_count
        note_count = self.heatmap.note_count
        first_frame_seconds = self._first_frame_seconds()
        for x in range(start_x, end_x + 1):
            # Time relative to the first frame: range analyses offset frames onto
            # the full-song timeline, so frame 0 is not at t=0.
            start_time = (x - self.keyboard_width) * self.seconds_per_pixel - first_frame_seconds
            end_time = start_time + self.seconds_per_pixel
            if end_time <= 0.0:
                # Pixel column before the analysed range starts; nothing to draw.
                continue
            start_frame = max(0, int(start_time / frame_step_seconds))
            if start_frame >= frame_count:
                # Pixel column past the last analysed frame (e.g. a short range on
                # a full-song timeline). Nothing to draw; also avoids an empty
                # numpy slice reduction.
                break
            end_frame = min(frame_count, max(start_frame + 1, int(math.ceil(end_time / frame_step_seconds))))
            stride = max(1, (end_frame - start_frame) // 5)
            if matrix is not None:
                # One C-level column reduction gives all note rows for this pixel.
                col_max = matrix[start_frame:end_frame:stride].max(axis=0)
                for note_index in self._active_rows(col_max):
                    y = (note_count - 1 - note_index) * self.note_height
                    painter.fillRect(QRectF(x, y, 1.2, self.note_height), self._heat_color(float(col_max[note_index])))
            else:
                sampled_frames = range(start_frame, end_frame, stride)
                for note_index in range(note_count):
                    activation = max((self.heatmap.activation(frame_index, note_index) for frame_index in sampled_frames), default=0.0)
                    if activation <= 0.005:
                        continue
                    y = (note_count - 1 - note_index) * self.note_height
                    painter.fillRect(QRectF(x, y, 1.2, self.note_height), self._heat_color(activation))

    @staticmethod
    def _active_rows(values: Any) -> list[int]:
        """Return note-row indices whose activation exceeds the draw threshold.

        Returned as plain Python ints; numpy integer scalars must not reach the
        Qt geometry calls (they can crash PySide6 when used as coordinates).
        """

        import numpy as np  # type: ignore[import-not-found]

        return np.nonzero(values > 0.005)[0].tolist()

    def _frame_step_seconds(self) -> float:
        assert self.heatmap is not None
        if len(self.heatmap.frame_times) < 2:
            return max(self.seconds_per_pixel, 0.001)
        return max(0.001, self.heatmap.frame_times[1] - self.heatmap.frame_times[0])

    def _first_frame_seconds(self) -> float:
        """Time of the first heatmap frame.

        For a range analysis the frame times are offset onto the full-song
        timeline (e.g. starting at 40s), so frame index 0 is not at t=0.  Index
        math must subtract this or the heatmap draws shifted left relative to the
        MIDI notes and playhead.
        """

        assert self.heatmap is not None
        return self.heatmap.frame_times[0] if self.heatmap.frame_times else 0.0

    def _frame_index_at_x(self, x: float) -> int:
        assert self.heatmap is not None
        frame_step_seconds = self._frame_step_seconds()
        seconds = (x - self.keyboard_width) * self.seconds_per_pixel - self._first_frame_seconds()
        return max(0, min(self.heatmap.frame_count, int(seconds / frame_step_seconds)))

    def _draw_grid(self, painter: QPainter) -> None:
        assert self.heatmap is not None
        grid = qcolor(active_theme().grid)
        # Octave lines slightly dimmer than the time grid (as before: 28 vs 36).
        octave = QColor(grid)
        octave.setAlpha(max(1, int(grid.alpha() * 0.78)))
        painter.setPen(QPen(octave, 1))
        for note_index, pitch in enumerate(reversed(self.heatmap.midi_notes)):
            if pitch % 12 == 0:
                y = note_index * self.note_height
                painter.drawLine(self.keyboard_width, y, self.width(), y)
        painter.setPen(QPen(grid, 1))
        duration = self._timeline_duration_seconds()
        interval = self._grid_interval_seconds()
        # Only draw grid lines within the visible clip so a full-song timeline
        # does not paint thousands of off-screen verticals for a long file.
        clip = painter.clipBoundingRect()
        visible_left = clip.left() if not clip.isEmpty() else float(self.keyboard_width)
        visible_right = clip.right() if not clip.isEmpty() else float(self.width())
        first_index = max(0, int(((visible_left - self.keyboard_width) * self.seconds_per_pixel) / interval))
        second = first_index * interval
        while second <= duration + interval:
            x = int(self.keyboard_width + second / self.seconds_per_pixel)
            if x > visible_right:
                break
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
        theme = active_theme()
        fill = qcolor(theme.note_fill)
        border = qcolor(theme.note_border)
        selected = qcolor(theme.note_selected)
        # Hover: same fill hue, a touch brighter/more opaque than the resting note.
        hover_fill = QColor(fill)
        hover_fill.setAlpha(min(255, fill.alpha() + 20))
        clip = painter.clipBoundingRect()
        for list_index, note in enumerate(self.notes):
            rect = self._note_rect(note)
            if rect is None:
                continue
            if not clip.isEmpty() and (rect.right() < clip.left() or rect.left() > clip.right() or rect.bottom() < clip.top() or rect.top() > clip.bottom()):
                continue
            is_selected = list_index in self.selected_indices
            is_hovered = list_index == self.hover_note_index
            if is_selected:
                painter.setPen(QPen(selected, 2))
                painter.setBrush(QColor(selected.red(), selected.green(), selected.blue(), 170))
            elif is_hovered:
                painter.setPen(QPen(border, 2))
                painter.setBrush(hover_fill)
            else:
                painter.setPen(QPen(border, 1))
                painter.setBrush(fill)
            painter.drawRect(rect)
            if is_selected or is_hovered:
                self._draw_note_handles(painter, rect, active=is_selected)

    def _draw_bend_curves(self, painter: QPainter) -> None:
        """Draw each note's pitch-bend contour as a polyline within/around its row.

        The curve's vertical position tracks the bend in semitones (up = sharp),
        scaled so one semitone equals one note-row height; large bends may extend
        beyond the note's own row, which honestly reflects the pitch movement.
        """

        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF

        assert self.heatmap is not None
        clip = painter.clipBoundingRect()
        pen = QPen(qcolor(active_theme().bend_curve), 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for note in self.notes:
            bends = note.pitch_bends
            if not bends:
                continue
            rect = self._note_rect(note)
            if rect is None:
                continue
            if not clip.isEmpty() and (rect.right() < clip.left() or rect.left() > clip.right()):
                continue
            row_center_y = rect.top() + rect.height() / 2.0
            points = QPolygonF()
            for time_in_note, semitones in bends:
                x = self.keyboard_width + (note.start_seconds + time_in_note) / self.seconds_per_pixel
                y = row_center_y - semitones * self.note_height
                points.append(QPointF(x, y))
            painter.drawPolyline(points)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _draw_note_handles(self, painter: QPainter, rect: QRectF, *, active: bool) -> None:
        theme = active_theme()
        handle_width = min(6.0, max(3.0, rect.width() / 3.0))
        bright = qcolor(theme.note_selected)
        accent = qcolor(theme.accent)
        handle_color = QColor(bright.red(), bright.green(), bright.blue(), 235) if active else QColor(accent.red(), accent.green(), accent.blue(), 210)
        outline_color = QColor(55, 26, 10, 230)
        left = QRectF(rect.left(), rect.top(), handle_width, rect.height())
        right = QRectF(rect.right() - handle_width, rect.top(), handle_width, rect.height())
        painter.setPen(QPen(outline_color, 1))
        painter.setBrush(handle_color)
        painter.drawRect(left)
        painter.drawRect(right)

    def _note_rect(self, note: GuiMidiNote) -> QRectF | None:
        assert self.heatmap is not None
        note_index = self._pitch_to_row.get(note.pitch)
        if note_index is None:
            return None
        x = self.keyboard_width + note.start_seconds / self.seconds_per_pixel
        y = (self.heatmap.note_count - 1 - note_index) * self.note_height
        width = max(2.0, note.duration_seconds / self.seconds_per_pixel)
        return QRectF(x, y + 1, width, self.note_height - 2)

    def _note_index_at(self, x: float, y: float) -> int | None:
        hit = self._note_hit_at(x, y)
        return hit[0] if hit is not None else None

    def _rebuild_note_index(self) -> None:
        """Bucket note indices by pitch row for O(row) hover hit-testing."""

        buckets: dict[int, list[int]] = {}
        for index, note in enumerate(self.notes):
            row = self._pitch_to_row.get(note.pitch)
            if row is not None:
                buckets.setdefault(row, []).append(index)
        self._notes_by_row = buckets

    def _row_for_y(self, y: float) -> int | None:
        """Map a canvas y to the pitch row drawn there, or None if off the grid."""

        if self.heatmap is None or self.note_height <= 0:
            return None
        row_from_top = int(y // self.note_height)
        row = self.heatmap.note_count - 1 - row_from_top
        return row if 0 <= row < self.heatmap.note_count else None

    def _note_hit_at(self, x: float, y: float) -> tuple[int, str] | None:
        if self.heatmap is None or not self.show_notes:
            return None
        row = self._row_for_y(y)
        if row is None:
            return None
        handle_width = 7.0
        # Only notes on the row under the cursor can be hit; scan that bucket in
        # reverse so the top-most/latest-drawn overlapping note wins.
        for index in reversed(self._notes_by_row.get(row, ())):
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
            # replace() keeps pitch_bends so the bend curve survives the edit.
            return replace(
                note,
                start_seconds=start_seconds,
                duration_seconds=max(self.min_note_duration_seconds, original_end - start_seconds),
            )
        if self.drag_mode == "resize_end":
            end_seconds = max(note.start_seconds + self.min_note_duration_seconds, note.end_seconds + delta_seconds)
            return replace(note, duration_seconds=end_seconds - note.start_seconds)
        if self.drag_mode == "move":
            pitch = self._pitch_at_y(y)
            return replace(
                note,
                pitch=note.pitch if pitch is None else pitch,
                start_seconds=max(0.0, note.start_seconds + delta_seconds),
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
        painter.setPen(QPen(qcolor(active_theme().playhead), 2))
        painter.drawLine(x, 0, x, self.height())

    # 256-entry color lookup table, built lazily and shared by every instance.
    # Rebuilding a QColor per heatmap cell dominated repaint cost; quantising the
    # activation to a byte and indexing this list removes that per-cell work. The
    # cache is keyed on the active theme id so a theme change rebuilds the ramp.
    _HEAT_LUT: list[QColor] | None = None
    _HEAT_LUT_THEME: str | None = None

    @classmethod
    def _heat_lut(cls) -> list[QColor]:
        theme = active_theme()
        if cls._HEAT_LUT is None or cls._HEAT_LUT_THEME != theme.id:
            channels = theme.heat.channels()
            cls._HEAT_LUT = [cls._heat_color_exact(i / 255.0, channels) for i in range(256)]
            cls._HEAT_LUT_THEME = theme.id
        return cls._HEAT_LUT

    @staticmethod
    def _heat_color_exact(value: float, channels: tuple[tuple[int, int], ...]) -> QColor:
        """Map a [0, 1] activation to an RGBA QColor via the ramp's (offset, slope) per channel."""

        value = max(0.0, min(1.0, value))
        (r0, rs), (g0, gs), (b0, bs), (a0, as_) = channels
        return QColor(
            int(r0 + rs * value),
            int(g0 + gs * value),
            int(b0 + bs * value),
            int(a0 + as_ * value),
        )

    @classmethod
    def _heat_color(cls, value: float) -> QColor:
        index = int(max(0.0, min(1.0, value)) * 255.0)
        return cls._heat_lut()[index]

    # numpy RGBA lookup table (256, 4) in the byte order QImage.Format_ARGB32
    # expects on a little-endian host: B, G, R, A. Built from the same color
    # ramp as the QColor LUT so the blitted heatmap matches the fillRect path.
    # Also keyed on the active theme id so it rebuilds on a theme change.
    _HEAT_RGBA_LUT: Any = None
    _HEAT_RGBA_LUT_THEME: str | None = None

    @classmethod
    def _heat_rgba_lut(cls) -> Any:
        theme = active_theme()
        if cls._HEAT_RGBA_LUT is None or cls._HEAT_RGBA_LUT_THEME != theme.id:
            import numpy as np  # type: ignore[import-not-found]

            (r0, rs), (g0, gs), (b0, bs), (a0, as_) = theme.heat.channels()
            values = np.arange(256, dtype=np.float32) / 255.0
            r = (r0 + rs * values).astype(np.uint8)
            g = (g0 + gs * values).astype(np.uint8)
            b = (b0 + bs * values).astype(np.uint8)
            a = (a0 + as_ * values).astype(np.uint8)
            # ARGB32 stored little-endian is B, G, R, A byte order in memory.
            cls._HEAT_RGBA_LUT = np.stack([b, g, r, a], axis=1)
            cls._HEAT_RGBA_LUT_THEME = theme.id
        return cls._HEAT_RGBA_LUT
