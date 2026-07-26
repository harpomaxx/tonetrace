"""NeuralNote-inspired left control panel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from notegrabber.analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    CQT_THRESHOLD,
)


class AnalysisControls(QWidget):
    """Left-side backend and transcription controls."""

    analyze_requested = Signal()
    export_requested = Signal()
    delete_requested = Signal()
    open_requested = Signal()
    retune_requested = Signal()
    overlay_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["basic-pitch", "cqt", "simple"])

        self.note_sensitivity = self._slider(0, 100, int(BASIC_PITCH_FRAME_THRESHOLD * 100))
        self.split_sensitivity = self._slider(0, 100, int(BASIC_PITCH_ONSET_THRESHOLD * 100))
        self.cqt_threshold = self._slider(0, 100, int(CQT_THRESHOLD * 100))
        self.min_duration = self._slider(0, 500, int(BASIC_PITCH_MIN_DURATION_SECONDS * 1000))
        self.show_overlay = QCheckBox("Show MIDI overlay")
        self.show_overlay.setChecked(True)

        self.open_button = QPushButton("Open audio")
        self.analyze_button = QPushButton("Analyze")
        self.export_button = QPushButton("Export MIDI")
        self.delete_button = QPushButton("Delete selected note")
        self.export_button.setEnabled(False)
        self.delete_button.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_transcription_group())
        layout.addWidget(self._build_stub_group("Pitch bend", "No Pitch Bend (reserved)"))
        layout.addWidget(self._build_stub_group("Scale quantize", "Disabled for first milestone"))
        layout.addWidget(self._build_stub_group("Time quantize", "Disabled for first milestone"))
        layout.addStretch(1)
        layout.addWidget(self.open_button)
        layout.addWidget(self.analyze_button)
        layout.addWidget(self.delete_button)
        layout.addWidget(self.export_button)

        self.open_button.clicked.connect(self.open_requested.emit)
        self.analyze_button.clicked.connect(self.analyze_requested.emit)
        self.export_button.clicked.connect(self.export_requested.emit)
        self.delete_button.clicked.connect(self.delete_requested.emit)
        self.show_overlay.toggled.connect(self.overlay_toggled.emit)
        for slider in (self.note_sensitivity, self.split_sensitivity, self.cqt_threshold, self.min_duration):
            slider.valueChanged.connect(self.retune_requested.emit)

    def backend(self) -> str:
        return self.backend_combo.currentText()

    def frame_threshold(self) -> float:
        return self.note_sensitivity.value() / 100.0

    def onset_threshold(self) -> float:
        return self.split_sensitivity.value() / 100.0

    def threshold(self) -> float:
        return self.cqt_threshold.value() / 100.0

    def min_duration_seconds(self) -> float:
        return self.min_duration.value() / 1000.0

    def set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy)
        self.backend_combo.setEnabled(not busy)

    def set_can_export(self, enabled: bool) -> None:
        self.export_button.setEnabled(enabled)

    def set_can_delete(self, enabled: bool) -> None:
        self.delete_button.setEnabled(enabled)

    def _build_transcription_group(self) -> QGroupBox:
        group = QGroupBox("Transcription")
        form = QFormLayout(group)
        form.addRow("Backend", self.backend_combo)
        form.addRow("Note sensitivity", self.note_sensitivity)
        form.addRow("Split sensitivity", self.split_sensitivity)
        form.addRow("CQT threshold", self.cqt_threshold)
        form.addRow("Min note duration", self.min_duration)
        form.addRow(self.show_overlay)
        return group

    @staticmethod
    def _build_stub_group(title: str, text: str) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setEnabled(False)
        layout.addWidget(label)
        return group

    @staticmethod
    def _slider(minimum: int, maximum: int, value: int) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        return slider
