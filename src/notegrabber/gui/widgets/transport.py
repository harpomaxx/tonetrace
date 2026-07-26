"""Transport controls for the standalone GUI."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QHBoxLayout, QWidget


class TransportWidget(QWidget):
    """Top-bar playback controls for original/MIDI comparison."""

    play_both_requested = Signal()
    play_original_requested = Signal()
    play_midi_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status_label = QLabel("Ready")
        self.play_both = QPushButton("Play both")
        self.play_original = QPushButton("Original")
        self.play_midi = QPushButton("MIDI")
        self.pause = QPushButton("Pause")
        self.stop = QPushButton("Stop")
        self.set_playback_available(original=False, midi=False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status_label, 1)
        layout.addWidget(self.play_both)
        layout.addWidget(self.play_original)
        layout.addWidget(self.play_midi)
        layout.addWidget(self.pause)
        layout.addWidget(self.stop)

        self.play_both.clicked.connect(self.play_both_requested.emit)
        self.play_original.clicked.connect(self.play_original_requested.emit)
        self.play_midi.clicked.connect(self.play_midi_requested.emit)
        self.pause.clicked.connect(self.pause_requested.emit)
        self.stop.clicked.connect(self.stop_requested.emit)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_playback_available(self, *, original: bool, midi: bool) -> None:
        self.play_both.setEnabled(original and midi)
        self.play_original.setEnabled(original)
        self.play_midi.setEnabled(midi)
        self.pause.setEnabled(original or midi)
        self.stop.setEnabled(original or midi)
