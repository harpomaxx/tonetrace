"""The detected-notes table and the collapsible dock that holds it.

Covers the layout-reorg contract: the table is self-explaining (unit-bearing
headers, empty state), emits a count, and the collapsible section starts
collapsed and toggles.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _notes():
    from notegrabber.gui.state import GuiMidiNote

    return [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.30, velocity=90),
        GuiMidiNote(pitch=64, start_seconds=0.0, duration_seconds=0.30, velocity=88),  # chord with C4
        GuiMidiNote(pitch=67, start_seconds=0.5, duration_seconds=0.30, velocity=80),
    ]


def test_headers_carry_units():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.sequence import SequenceWidget

    QApplication.instance() or QApplication([])
    seq = SequenceWidget()
    headers = [seq.table.horizontalHeaderItem(i).text() for i in range(seq.table.columnCount())]
    assert headers == ["Start (s)", "Notes (chord)", "Length (s)", "Velocity (0-127)"]


def test_empty_state_shown_until_notes_then_table():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.sequence import SequenceWidget

    QApplication.instance() or QApplication([])
    seq = SequenceWidget()
    # Starts on the empty-state page (index 0).
    assert seq._stack.currentIndex() == 0
    seq.set_notes(_notes())
    assert seq._stack.currentIndex() == 1  # table
    seq.set_notes([])
    assert seq._stack.currentIndex() == 0  # back to empty state


def test_unchanged_rows_keep_their_items_on_edit():
    """Editing one note must not recreate the QTableWidgetItems of other rows
    (issue #28: update incrementally instead of a full rebuild)."""

    from dataclasses import replace

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.sequence import SequenceWidget

    QApplication.instance() or QApplication([])
    seq = SequenceWidget()
    notes = _notes()  # rows: [C4+E4 @0.0], [G4 @0.5]
    seq.set_notes(notes)

    # Capture the item objects of the untouched first (chord) row.
    row0_items_before = [seq.table.item(0, c) for c in range(seq.table.columnCount())]

    # Edit only the second row's note (raise G4's velocity).
    edited = [notes[0], notes[1], replace(notes[2], velocity=120)]
    seq.set_notes(edited)

    # First row's cells are the exact same item objects (not recreated)...
    row0_items_after = [seq.table.item(0, c) for c in range(seq.table.columnCount())]
    assert row0_items_after == row0_items_before
    # ...while the changed row reflects the new velocity.
    assert seq.table.item(1, 3).text() == "120"


def test_identical_reset_changes_no_cells():
    """Re-setting the identical note list rewrites no cell text but still reports
    the count (a common no-op path on the edit-commit flow)."""

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.sequence import SequenceWidget

    QApplication.instance() or QApplication([])
    seq = SequenceWidget()
    notes = _notes()
    seq.set_notes(notes)

    items_before = [seq.table.item(r, c) for r in range(seq.table.rowCount()) for c in range(seq.table.columnCount())]
    texts_before = [item.text() for item in items_before]

    counts: list[int] = []
    seq.count_changed.connect(counts.append)
    seq.set_notes(list(notes))  # same content, fresh list

    items_after = [seq.table.item(r, c) for r in range(seq.table.rowCount()) for c in range(seq.table.columnCount())]
    assert items_after == items_before  # no item was recreated
    assert [item.text() for item in items_after] == texts_before
    assert counts == [2]  # count still emitted


def test_count_changed_signal_reports_chord_rows():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.sequence import SequenceWidget

    QApplication.instance() or QApplication([])
    seq = SequenceWidget()
    counts: list[int] = []
    seq.count_changed.connect(counts.append)
    seq.set_notes(_notes())  # 3 notes -> 2 onset rows (C4+E4 chord, then G4)
    assert counts[-1] == 2
    assert seq.table.rowCount() == 2
    # The chord row lists both note names.
    assert seq.table.item(0, 1).text() == "C4 E4"


def test_row_click_emits_seek_at_row_start():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.sequence import SequenceWidget

    QApplication.instance() or QApplication([])
    seq = SequenceWidget()
    seq.set_notes(_notes())
    seeks: list[float] = []
    seq.seek_requested.connect(seeks.append)
    seq._cell_clicked(1, 0)  # second row starts at 0.5s
    assert seeks == [0.5]


def test_collapsible_starts_collapsed_and_toggles():
    from PySide6.QtWidgets import QApplication, QLabel

    from notegrabber.gui.widgets.collapsible import CollapsibleSection

    QApplication.instance() or QApplication([])
    body = QLabel("body")
    section = CollapsibleSection("Detected notes", body, expanded=False)
    assert not section.is_expanded()

    states: list[bool] = []
    section.toggled.connect(states.append)
    section.header.click()
    assert section.is_expanded()
    assert states == [True]


def test_collapsible_suffix_appends_to_title():
    from PySide6.QtWidgets import QApplication, QLabel

    from notegrabber.gui.widgets.collapsible import CollapsibleSection

    QApplication.instance() or QApplication([])
    section = CollapsibleSection("Detected notes", QLabel("b"))
    section.set_suffix("  ·  7")
    assert section.header.text() == "Detected notes  ·  7"
