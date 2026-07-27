"""Custom-painted rotary knob for the ToneTrace transcription controls.

Qt has no pro-DAW knob widget (``QDial`` is a flat OS control that QSS cannot
restyle), so this is a self-contained ``QWidget`` painted with ``QPainter``.  It
mirrors the small integer ``QSlider`` API used by the controls panel
(``value``/``setValue``/``setRange``/``valueChanged``) so it drops in behind the
existing slider factory without changing consumers.

Interaction follows the DAW convention: vertical drag changes the value (up
increases), Shift enables a fine mode, the wheel nudges, and double-click resets
to the default.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

# Sweep geometry: the value arc runs from the lower-left to the lower-right,
# leaving a gap at the bottom like a physical knob's end stops.
_START_ANGLE_DEG = 225.0  # 7-8 o'clock
_SWEEP_DEG = 270.0        # up and over to 4-5 o'clock

# Warm ToneTrace accent palette.
_ARC_BG = QColor(60, 66, 84)
_ARC_LOW = QColor(216, 109, 34)   # #d86d22
_ARC_HIGH = QColor(255, 214, 79)  # #ffd64f
_BODY_LIGHT = QColor(70, 76, 94)
_BODY_DARK = QColor(26, 29, 40)
_RIM = QColor(255, 179, 63)       # #ffb33f
_POINTER = QColor(255, 226, 120)
_TICK = QColor(150, 160, 180, 120)


class KnobWidget(QWidget):
    """A rotary knob exposing the integer slider API used by AnalysisControls."""

    valueChanged = Signal(int)  # noqa: N815 - matches QSlider signal name
    # Emitted when a value change is "committed": mouse-drag release, or
    # immediately after a wheel/keyboard step (which have no release). Consumers
    # that trigger expensive work (e.g. CQT retune) should use this, not
    # valueChanged, so a drag does not recompute on every intermediate value.
    editingFinished = Signal()  # noqa: N815 - matches Qt naming convention

    def __init__(self, minimum: int, maximum: int, value: int, *, default: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._minimum = int(minimum)
        self._maximum = max(int(maximum), int(minimum) + 1)
        self._default = int(default) if default is not None else int(value)
        self._value = self._clamp(int(value))
        self._drag_last_y: float | None = None
        self._drag_moved = False
        self.setMinimumSize(56, 56)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    # -- slider-compatible API -------------------------------------------------

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 - matches QSlider
        clamped = self._clamp(int(round(value)))
        if clamped == self._value:
            return
        self._value = clamped
        self.update()
        self.valueChanged.emit(self._value)

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802 - matches QSlider
        self._minimum = int(minimum)
        self._maximum = max(int(maximum), int(minimum) + 1)
        self.setValue(self._value)

    def setDefault(self, default: int) -> None:  # noqa: N802 - knob-specific
        self._default = self._clamp(int(default))

    # -- helpers ---------------------------------------------------------------

    def _clamp(self, value: int) -> int:
        return max(self._minimum, min(self._maximum, value))

    def _fraction(self) -> float:
        span = self._maximum - self._minimum
        return (self._value - self._minimum) / span if span else 0.0

    # -- interaction -----------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_last_y = float(event.position().y())
            self._drag_moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_last_y is None:
            super().mouseMoveEvent(event)
            return
        y = float(event.position().y())
        delta_pixels = self._drag_last_y - y  # up (smaller y) increases
        self._drag_last_y = y
        span = self._maximum - self._minimum
        # A full sweep spans ~200px of vertical travel; Shift = fine (5x slower).
        pixels_per_span = 200.0
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            pixels_per_span *= 5.0
        before = self._value
        self.setValue(self._value + delta_pixels * span / pixels_per_span)
        if self._value != before:
            self._drag_moved = True
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_last_y = None
            # Commit the drag once, on release, so a sweep triggers one retune.
            if self._drag_moved:
                self._drag_moved = False
                self.editingFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            before = self._value
            self.setValue(self._default)
            if self._value != before:
                self.editingFinished.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        steps = event.angleDelta().y() / 120.0
        if not steps:
            steps = event.pixelDelta().y() / 40.0
        if steps:
            before = self._value
            self.setValue(self._value + int(round(steps)))
            # Wheel has no release; commit immediately when the value changed.
            if self._value != before:
                self.editingFinished.emit()
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Right):
            before = self._value
            self.setValue(self._value + 1)
            if self._value != before:
                self.editingFinished.emit()
            event.accept()
        elif event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Left):
            before = self._value
            self.setValue(self._value - 1)
            if self._value != before:
                self.editingFinished.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    # -- painting --------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        side = min(self.width(), self.height())
        margin = 6.0
        diameter = side - 2 * margin
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        arc_rect = QRectF(cx - diameter / 2.0, cy - diameter / 2.0, diameter, diameter)

        # Qt angles are in 1/16 degree, counter-clockwise from 3 o'clock.
        start_qt = int(-_START_ANGLE_DEG * 16)
        full_span_qt = int(-_SWEEP_DEG * 16)
        value_span_qt = int(-_SWEEP_DEG * self._fraction() * 16)

        # Background track.
        track_pen = QPen(_ARC_BG, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(arc_rect, start_qt, full_span_qt)

        # Value arc with a warm low->high gradient.
        gradient = QConicalGradient(cx, cy, _START_ANGLE_DEG)
        gradient.setColorAt(0.0, _ARC_LOW)
        gradient.setColorAt(0.4, _ARC_HIGH)
        value_pen = QPen(gradient, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(value_pen)
        painter.drawArc(arc_rect, start_qt, value_span_qt)

        # Knob body: radial gradient cap with a rim, inset from the arc.
        body_d = diameter - 16.0
        body_rect = QRectF(cx - body_d / 2.0, cy - body_d / 2.0, body_d, body_d)
        body = QRadialGradient(cx, cy - body_d * 0.18, body_d * 0.75)
        body.setColorAt(0.0, _BODY_LIGHT)
        body.setColorAt(1.0, _BODY_DARK)
        painter.setBrush(body)
        painter.setPen(QPen(_RIM, 1.4))
        painter.drawEllipse(body_rect)

        # Pointer from the body edge toward the center at the current angle.
        angle_deg = _START_ANGLE_DEG - _SWEEP_DEG * self._fraction()
        angle_rad = math.radians(angle_deg)
        # Screen y grows downward, so negate the sin term.
        dx = math.cos(angle_rad)
        dy = -math.sin(angle_rad)
        outer = QPointF(cx + dx * body_d * 0.42, cy + dy * body_d * 0.42)
        inner = QPointF(cx + dx * body_d * 0.14, cy + dy * body_d * 0.14)
        painter.setPen(QPen(_POINTER, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(inner, outer)
