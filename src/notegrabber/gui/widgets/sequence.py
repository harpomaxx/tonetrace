"""Detected-notes table for GUI note/chord inspection.

Shows the transcribed MIDI notes grouped into chords by onset time. Carries its
own caption, unit-bearing column headers, and an empty state so the view is
self-explanatory before and after an analysis.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from notegrabber.gui.state import GuiMidiNote


class SequenceWidget(QWidget):
    """Onset-grouped note/chord table with a caption and empty state."""

    seek_requested = Signal(float)
    # Emitted with the number of chord rows whenever the table is repopulated, so
    # a container (e.g. a collapsible header) can show a live count.
    count_changed = Signal(int)

    # Column headers carry units so the table needs no separate helper text.
    COLUMNS = ("Start (s)", "Notes (chord)", "Length (s)", "Velocity (0-127)")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_starts: list[float] = []
        # The cell text currently shown per row, so set_notes can update only the
        # rows that actually changed instead of destroying and recreating every
        # QTableWidgetItem on each committed edit.
        self._row_values: list[tuple[str, str, str, str]] = []

        caption = QLabel(
            "Transcribed notes, grouped into chords by start time · click a row to jump the playhead"
        )
        caption.setObjectName("inlineFieldLabel")
        caption.setWordWrap(True)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._cell_clicked)

        # Empty state shown in place of the table until there are notes to list.
        self.empty_state = QLabel(
            "No notes yet.\nLoad audio and run Analyze to list the detected notes here."
        )
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)

        # Swap between the empty state and the populated table in one slot.
        self._stack = QStackedWidget()
        self._stack.addWidget(self.empty_state)  # index 0
        self._stack.addWidget(self.table)        # index 1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(caption)
        layout.addWidget(self._stack, 1)

    def set_notes(self, notes: list[GuiMidiNote]) -> None:
        """Group notes by approximate onset and display them (or the empty state).

        Updates incrementally: rows are diffed against what is already shown and
        only changed cells are rewritten (reusing existing items), so a single
        committed edit no longer rebuilds every QTableWidgetItem.
        """

        rows = group_notes_by_onset(notes)
        new_starts: list[float] = []
        new_values: list[tuple[str, str, str, str]] = []
        for group in rows:
            start = min(note.start_seconds for note in group)
            new_starts.append(start)
            duration = max(note.end_seconds for note in group) - start
            ordered = sorted(group, key=lambda note: note.pitch)
            pitches = " ".join(note_name(note.pitch) for note in ordered)
            velocities = ", ".join(str(note.velocity) for note in ordered)
            new_values.append((f"{start:.3f}", pitches, f"{duration:.3f}", velocities))

        row_count_changed = len(new_values) != len(self._row_values)
        if row_count_changed:
            self.table.setRowCount(len(new_values))

        any_cell_changed = row_count_changed
        for row_index, values in enumerate(new_values):
            old = self._row_values[row_index] if row_index < len(self._row_values) else None
            for column, value in enumerate(values):
                if old is not None and old[column] == value:
                    continue  # unchanged cell: leave its existing item untouched
                any_cell_changed = True
                item = self.table.item(row_index, column)
                if item is None:
                    self.table.setItem(row_index, column, QTableWidgetItem(value))
                else:
                    item.setText(value)

        self.row_starts = new_starts
        self._row_values = new_values
        # Column widths only need recomputing when content actually changed;
        # re-setting the identical note list (a common no-op) now costs nothing.
        if any_cell_changed:
            self.table.resizeColumnsToContents()
        self._stack.setCurrentIndex(1 if rows else 0)
        self.count_changed.emit(len(rows))

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
