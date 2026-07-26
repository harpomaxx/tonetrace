"""Waveform overview widget for the standalone GUI."""

from __future__ import annotations

from pathlib import Path

from notegrabber.analyzer import read_wav

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class WaveformWidget(QWidget):
    """Draw a lightweight mono waveform overview from an audio file."""

    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.samples: list[float] = []
        self.sample_rate = 0
        self.playhead_seconds = 0.0
        self.setMinimumHeight(90)

    def load_audio(self, path: Path) -> None:
        """Load audio samples for drawing."""

        audio = read_wav(path)
        self.samples = audio.samples
        self.sample_rate = audio.sample_rate
        self.update()

    def duration_seconds(self) -> float:
        if not self.samples or self.sample_rate <= 0:
            return 0.0
        return len(self.samples) / self.sample_rate

    def set_playhead(self, seconds: float) -> None:
        """Set the drawn playhead position."""

        self.playhead_seconds = max(0.0, seconds)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        duration = self.duration_seconds()
        if duration > 0:
            x = max(0.0, min(float(self.width()), float(event.position().x())))
            self.seek_requested.emit(duration * x / max(1.0, float(self.width())))
        super().mousePressEvent(event)

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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open an audio file to show waveform")
            return

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
