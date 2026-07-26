"""NeuralNote-inspired left control panel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSize, Qt

from notegrabber.analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    CQT_THRESHOLD,
)
from notegrabber.gui.theme import polish_button

NOTE_SENSITIVITY_HELP = (
    "Basic Pitch note sensitivity. Higher values require stronger frame confidence, "
    "so weak/ambiguous notes are filtered out. Lower values keep more notes, but can add false positives."
)
SPLIT_SENSITIVITY_HELP = (
    "Basic Pitch split/onset sensitivity. Higher values require a clearer new attack before splitting notes. "
    "Lower values can separate repeated notes more easily, but may over-split sustained notes."
)
CQT_THRESHOLD_HELP = (
    "CQT activation threshold for non-ML extraction. Higher values keep only the brightest heatmap regions; "
    "lower values include quieter pitch traces and may add extra notes. Applies instantly to CQT analyses."
)
MIN_DURATION_HELP = (
    "Minimum note length in milliseconds. Raise it to remove tiny blips and glitches; "
    "lower it to keep short ornaments, fast runs, or staccato notes."
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
        self.note_sensitivity.setToolTip(NOTE_SENSITIVITY_HELP)
        self.split_sensitivity.setToolTip(SPLIT_SENSITIVITY_HELP)
        self.cqt_threshold.setToolTip(CQT_THRESHOLD_HELP)
        self.min_duration.setToolTip(MIN_DURATION_HELP)
        self.show_overlay = QCheckBox("Show MIDI overlay")
        self.show_overlay.setChecked(True)

        self.open_button = self._action_button("Open", role="secondary", icon_name="folder")
        self.analyze_button = self._action_button("Analyze", role="primary", icon_name="analyze")
        self.export_button = self._action_button("Export", role="primary", icon_name="export")
        self.delete_button = self._action_button("Delete", role="danger", icon_name="trash")
        self.open_button.setToolTip("Choose an audio file to transcribe")
        self.analyze_button.setToolTip("Run the selected transcription backend")
        self.export_button.setToolTip("Export the current edited note list as MIDI")
        self.delete_button.setToolTip("Remove the selected MIDI note from the edited list")
        self.export_button.setEnabled(False)
        self.delete_button.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        brand = QLabel("TONETRACE")
        brand.setObjectName("brandLabel")
        layout.addWidget(brand)
        layout.addWidget(self._build_transcription_group())
        layout.addWidget(self._build_stub_group("Pitch bend", "No Pitch Bend (reserved)"))
        layout.addWidget(self._build_stub_group("Scale quantize", "Disabled for first milestone"))
        layout.addWidget(self._build_stub_group("Time quantize", "Disabled for first milestone"))
        layout.addStretch(1)
        layout.addWidget(self._build_action_group())

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

    def _build_action_group(self) -> QGroupBox:
        group = QGroupBox("Actions")
        group.setProperty("panel", "muted")
        grid = QGridLayout(group)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(8)
        grid.addWidget(self.open_button, 0, 0)
        grid.addWidget(self.analyze_button, 0, 1)
        grid.addWidget(self.delete_button, 1, 0)
        grid.addWidget(self.export_button, 1, 1)
        return group

    def _build_transcription_group(self) -> QGroupBox:
        group = QGroupBox("Transcription")
        group.setProperty("panel", "accent")
        form = QFormLayout(group)
        form.setVerticalSpacing(10)
        form.addRow("Backend", self.backend_combo)
        form.addRow(self._help_label("Note sensitivity ⓘ", NOTE_SENSITIVITY_HELP), self.note_sensitivity)
        form.addRow(self._help_label("Split sensitivity ⓘ", SPLIT_SENSITIVITY_HELP), self.split_sensitivity)
        form.addRow(self._help_label("CQT threshold ⓘ", CQT_THRESHOLD_HELP), self.cqt_threshold)
        form.addRow(self._help_label("Min note duration ⓘ", MIN_DURATION_HELP), self.min_duration)
        form.addRow(self.show_overlay)
        return group

    @staticmethod
    def _help_label(text: str, tooltip: str) -> QLabel:
        label = QLabel(text)
        label.setToolTip(tooltip)
        return label

    @staticmethod
    def _action_button(text: str, *, role: str, icon_name: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        polish_button(button, role=role, icon_name=icon_name)
        button.setProperty("compact", True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(20, 20))
        return button

    @staticmethod
    def _build_stub_group(title: str, text: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setProperty("panel", "muted")
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
