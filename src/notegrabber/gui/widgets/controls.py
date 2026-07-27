"""NeuralNote-inspired left control panel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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
from notegrabber.gui.widgets.knob import KnobWidget

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
    heatmap_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["basic-pitch", "cqt", "simple"])

        self.note_sensitivity = self._knob(0, 100, int(BASIC_PITCH_FRAME_THRESHOLD * 100))
        self.split_sensitivity = self._knob(0, 100, int(BASIC_PITCH_ONSET_THRESHOLD * 100))
        self.cqt_threshold = self._knob(0, 100, int(CQT_THRESHOLD * 100))
        self.min_duration = self._knob(0, 500, int(BASIC_PITCH_MIN_DURATION_SECONDS * 1000))
        self.note_sensitivity.setToolTip(NOTE_SENSITIVITY_HELP)
        self.split_sensitivity.setToolTip(SPLIT_SENSITIVITY_HELP)
        self.cqt_threshold.setToolTip(CQT_THRESHOLD_HELP)
        self.min_duration.setToolTip(MIN_DURATION_HELP)
        self.range_enabled = QCheckBox("Analyze range only")
        self.range_start = self._seconds_spin(0.0)
        self.range_duration = self._seconds_spin(30.0)
        self.range_enabled.setToolTip("Analyze only the selected time range instead of the full song. Useful for long MP3s.")
        self.range_start.setToolTip("Range start time in seconds from the beginning of the original audio.")
        self.range_duration.setToolTip("Range length in seconds. Keep this small for faster Basic Pitch analysis.")
        self.show_overlay = QCheckBox("Show MIDI overlay")
        self.show_overlay.setChecked(True)
        self.notes_only = QCheckBox("Notes only (hide heatmap)")
        self.notes_only.setChecked(False)
        self.notes_only.setToolTip("Hide the pitch-salience heatmap and show only the extracted MIDI notes, to focus on the notes.")

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
        layout.addWidget(self._build_range_group())
        # The primary workflow buttons (Open/Analyze/Delete/Export) live in a
        # horizontal action bar above the waveform (see build_action_bar), not in
        # this left column, so the transcription info below stays visible.
        layout.addWidget(self._build_stub_group("Pitch bend", "No Pitch Bend (reserved)"))
        layout.addWidget(self._build_stub_group("Scale quantize", "Disabled for first milestone"))
        layout.addWidget(self._build_stub_group("Time quantize", "Disabled for first milestone"))
        layout.addStretch(1)

        self.open_button.clicked.connect(self.open_requested.emit)
        self.analyze_button.clicked.connect(self.analyze_requested.emit)
        self.export_button.clicked.connect(self.export_requested.emit)
        self.delete_button.clicked.connect(self.delete_requested.emit)
        self.show_overlay.toggled.connect(self.overlay_toggled.emit)
        # Checkbox reads "Notes only", so emit "show heatmap" as its inverse to
        # mirror the set_show_notes wiring on the piano roll.
        self.notes_only.toggled.connect(lambda checked: self.heatmap_toggled.emit(not checked))
        # Retune only when a knob change is committed (drag release / wheel /
        # key), not on every intermediate value, so dragging a knob does not
        # re-extract notes and repaint dozens of times per second. Value labels
        # still update live via valueChanged in _knob_cell.
        for knob in (self.note_sensitivity, self.split_sensitivity, self.cqt_threshold, self.min_duration):
            knob.editingFinished.connect(self.retune_requested.emit)

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

    def analysis_range(self) -> tuple[float, float | None]:
        if not self.range_enabled.isChecked():
            return 0.0, None
        return self.range_start.value(), self.range_duration.value()

    def set_analysis_range(self, start_seconds: float, duration_seconds: float) -> None:
        """Enable range analysis and update numeric range controls."""

        self.range_enabled.setChecked(True)
        self.range_start.setValue(max(0.0, start_seconds))
        self.range_duration.setValue(max(0.01, duration_seconds))

    def set_audio_duration(self, duration_seconds: float) -> None:
        maximum = max(1.0, min(36_000.0, duration_seconds))
        self.range_start.setMaximum(maximum)
        self.range_duration.setMaximum(maximum)
        if self.range_duration.value() > maximum:
            self.range_duration.setValue(maximum)

    def set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy)
        self.backend_combo.setEnabled(not busy)
        self.range_enabled.setEnabled(not busy)
        self.range_start.setEnabled(not busy)
        self.range_duration.setEnabled(not busy)

    def set_can_export(self, enabled: bool) -> None:
        self.export_button.setEnabled(enabled)

    def set_can_delete(self, enabled: bool) -> None:
        self.delete_button.setEnabled(enabled)

    def build_action_bar(self) -> QWidget:
        """Return a horizontal bar with the primary workflow buttons.

        Reuses the same button objects created in ``__init__`` so all existing
        signal wiring and enable/disable logic keeps working; only their layout
        home changes. Placed above the waveform by the main window.
        """

        bar = QWidget()
        bar.setObjectName("actionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.open_button)
        row.addWidget(self.analyze_button)
        row.addWidget(self.delete_button)
        row.addWidget(self.export_button)
        row.addStretch(1)
        return bar

    def _build_range_group(self) -> QGroupBox:
        group = QGroupBox("Analysis range")
        group.setProperty("panel", "muted")
        form = QFormLayout(group)
        form.setVerticalSpacing(8)
        form.addRow(self.range_enabled)
        form.addRow("Start", self.range_start)
        form.addRow("Duration", self.range_duration)
        return group

    def _build_transcription_group(self) -> QGroupBox:
        group = QGroupBox("Transcription")
        group.setProperty("panel", "accent")
        form = QFormLayout(group)
        form.setVerticalSpacing(10)
        form.addRow("Backend", self.backend_combo)
        form.addRow(
            self._help_label("Note sensitivity ⓘ", NOTE_SENSITIVITY_HELP),
            self._knob_cell(self.note_sensitivity, self._format_percent),
        )
        form.addRow(
            self._help_label("Split sensitivity ⓘ", SPLIT_SENSITIVITY_HELP),
            self._knob_cell(self.split_sensitivity, self._format_percent),
        )
        form.addRow(
            self._help_label("CQT threshold ⓘ", CQT_THRESHOLD_HELP),
            self._knob_cell(self.cqt_threshold, self._format_percent),
        )
        form.addRow(
            self._help_label("Min note duration ⓘ", MIN_DURATION_HELP),
            self._knob_cell(self.min_duration, self._format_millis),
        )
        form.addRow(self.show_overlay)
        form.addRow(self.notes_only)
        return group

    @staticmethod
    def _format_percent(value: int) -> str:
        return f"{value} %"

    @staticmethod
    def _format_millis(value: int) -> str:
        return f"{value} ms"

    def _knob_cell(self, knob: KnobWidget, formatter) -> QWidget:
        """Lay a knob beside a live value label that mirrors its value."""

        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        value_label = QLabel(formatter(knob.value()))
        value_label.setObjectName("knobValueLabel")
        value_label.setMinimumWidth(46)
        value_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        knob.valueChanged.connect(lambda value, label=value_label, fmt=formatter: label.setText(fmt(value)))
        layout.addWidget(knob)
        layout.addWidget(value_label)
        layout.addStretch(1)
        return cell

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
    def _knob(minimum: int, maximum: int, value: int) -> KnobWidget:
        return KnobWidget(minimum, maximum, value, default=value)

    @staticmethod
    def _seconds_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 36_000.0)
        spin.setDecimals(2)
        spin.setSingleStep(1.0)
        spin.setSuffix(" s")
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin
