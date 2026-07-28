"""Transport controls for the standalone GUI."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QWidget

from notegrabber.gui.theme import icon


class TransportWidget(QWidget):
    """Top-bar playback controls for original/MIDI comparison.

    The app-wide status label stays on the left; the playback controls live in a
    "Playback" group on the right as round, icon-only buttons (media-player
    style), with the primary Both/play button emphasized in the centre.
    """

    play_both_requested = Signal()
    play_original_requested = Signal()
    play_midi_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("transportStatus")
        # Keep the status to a single line so a long message does not grow a tall
        # box; it stays on one row and the layout gives the space elsewhere.
        self.status_label.setWordWrap(False)

        # Icon-only round transport buttons. Tooltips carry the old text labels.
        self.play_original = self._transport_button("audio", "Play original audio")
        self.play_midi = self._transport_button("midi", "Play rendered MIDI")
        self.play_both = self._transport_button("both", "Play both (original + MIDI)", primary=True)
        self.pause = self._transport_button("pause", "Pause playback")
        self.stop = self._transport_button("stop", "Stop playback")
        self.set_playback_available(original=False, midi=False)

        self.playback_group = QGroupBox("Playback")
        self.playback_group.setProperty("panel", "muted")
        group_row = QHBoxLayout(self.playback_group)
        group_row.setContentsMargins(12, 4, 12, 8)
        group_row.setSpacing(9)
        # Order flanks the emphasized central play button, like a media player:
        # Audio  MIDI  [ Both ]  Pause  Stop
        group_row.addWidget(self.play_original)
        group_row.addWidget(self.play_midi)
        group_row.addWidget(self.play_both)
        group_row.addWidget(self.pause)
        group_row.addWidget(self.stop)

        # The status label and the playback group are placed separately by the
        # main window (status on its own one-line strip; playback next to the
        # Open/Analyze action bar), so this widget itself stays empty.

        self.play_both.clicked.connect(self.play_both_requested.emit)
        self.play_original.clicked.connect(self.play_original_requested.emit)
        self.play_midi.clicked.connect(self.play_midi_requested.emit)
        self.pause.clicked.connect(self.pause_requested.emit)
        self.stop.clicked.connect(self.stop_requested.emit)

    @staticmethod
    def _transport_button(icon_name: str, tooltip: str, *, primary: bool = False) -> QPushButton:
        button = QPushButton()
        button.setIcon(icon(icon_name))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # The central play button is larger and accent-coloured; the rest are
        # equal-size secondary round buttons flanking it.
        button.setProperty("transportIcon", "primary" if primary else "secondary")
        button.setIconSize(QSize(22, 22) if primary else QSize(18, 18))
        return button

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_playback_available(self, *, original: bool, midi: bool) -> None:
        self.play_both.setEnabled(original and midi)
        self.play_original.setEnabled(original)
        self.play_midi.setEnabled(midi)
        self.pause.setEnabled(original or midi)
        self.stop.setEnabled(original or midi)
