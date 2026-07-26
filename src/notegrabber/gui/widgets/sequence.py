"""Detected sequence table for GUI note/chord inspection."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from notegrabber.gui.state import GuiMidiNote


class SequenceWidget(QWidget):
    """Small onset-grouped note/chord table."""

    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_starts: list[float] = []
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Time", "Notes", "Duration", "Velocity"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        self.table.cellClicked.connect(self._cell_clicked)

    def set_notes(self, notes: list[GuiMidiNote]) -> None:
        """Group notes by approximate onset and display them."""

        rows = group_notes_by_onset(notes)
        self.row_starts = []
        self.table.setRowCount(len(rows))
        for row_index, group in enumerate(rows):
            start = min(note.start_seconds for note in group)
            self.row_starts.append(start)
            duration = max(note.end_seconds for note in group) - start
            pitches = " ".join(note_name(note.pitch) for note in sorted(group, key=lambda note: note.pitch))
            velocities = ", ".join(str(note.velocity) for note in sorted(group, key=lambda note: note.pitch))
            values = [f"{start:.3f}s", pitches, f"{duration:.3f}s", velocities]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def _cell_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self.row_starts):
            self.seek_requested.emit(self.row_starts[row])


def group_notes_by_onset(notes: list[GuiMidiNote], tolerance_seconds: float = 0.035) -> list[list[GuiMidiNote]]:
    """Group notes that start close together into chord rows."""

    rows: list[list[GuiMidiNote]] = []
    for note in sorted(notes, key=lambda item: (item.start_seconds, item.pitch)):
        if rows and abs(note.start_seconds - rows[-1][0].start_seconds) <= tolerance_seconds:
            rows[-1].append(note)
        else:
            rows.append([note])
    return rows


def note_name(pitch: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[pitch % 12]}{pitch // 12 - 1}"
