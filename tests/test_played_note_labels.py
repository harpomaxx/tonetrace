"""The piano keyboard must show the note name on a key while it is being played.

The name is drawn on the sounding key itself (bold, dark, over the warm
highlight) only when the playhead is inside a note and the view is zoomed in
enough for the text to fit. When zoomed out so rows are short, only the highlight
shows -- no name.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The warm highlight painted on a sounding key.
HIGHLIGHT = (255, 196, 84)


def _make_heatmap_and_note(pitch: int = 60):
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    pitches = list(range(58, 66))  # includes pitch 60
    frames = [i * 0.05 for i in range(40)]
    activations = [[0.0 for _ in pitches] for _ in frames]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=pitches,
        frame_times=frames,
        activations=activations,
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )
    note = GuiMidiNote(pitch=pitch, start_seconds=0.5, duration_seconds=1.0, velocity=90)
    return heatmap, [note]


def _render(playhead_seconds: float, *, vertical_zoom: float = 3.0):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    heatmap, notes = _make_heatmap_and_note()

    roll = PianoRollWidget()
    roll.resize(700, 400)
    roll.set_data(heatmap, notes, full_duration_seconds=2.0)
    roll.set_vertical_zoom(vertical_zoom)
    roll.set_playhead(playhead_seconds)

    image = QImage(roll.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0))
    roll.render(image, QPoint(0, 0))
    return roll, image


def _keyboard_highlight_rows(roll, image):
    """Return the set of image y-rows within the keyboard strip that are highlighted."""

    rows = set()
    for x in range(min(roll.keyboard_width, image.width())):
        for y in range(image.height()):
            c = image.pixelColor(x, y)
            if (
                abs(c.red() - HIGHLIGHT[0]) < 30
                and abs(c.green() - HIGHLIGHT[1]) < 30
                and abs(c.blue() - HIGHLIGHT[2]) < 30
            ):
                rows.add(y)
    return rows


def _keyboard_has_dark_text_over_highlight(roll, image) -> bool:
    """True if a highlighted key row contains dark text pixels (the note name)."""

    highlight_rows = _keyboard_highlight_rows(roll, image)
    if not highlight_rows:
        return False
    for x in range(min(roll.keyboard_width, image.width())):
        for y in highlight_rows:
            c = image.pixelColor(x, y)
            # The label is near-black; the highlight fill is bright warm.
            if c.red() < 80 and c.green() < 80 and c.blue() < 80:
                return True
    return False


def test_key_highlighted_and_named_when_zoomed_in():
    roll, image = _render(1.0, vertical_zoom=3.0)  # playhead inside [0.5, 1.5)
    assert _keyboard_highlight_rows(roll, image)  # key is highlighted
    assert _keyboard_has_dark_text_over_highlight(roll, image)  # ...and named


def test_no_highlight_before_note_starts():
    roll, image = _render(0.2, vertical_zoom=3.0)
    assert not _keyboard_highlight_rows(roll, image)


def test_no_highlight_after_note_ends():
    roll, image = _render(1.8, vertical_zoom=3.0)
    assert not _keyboard_highlight_rows(roll, image)


def test_name_hidden_when_zoomed_out_but_key_still_highlighted():
    # Tiny vertical zoom -> rows shorter than the label threshold -> highlight
    # only, no name.
    roll, image = _render(1.0, vertical_zoom=0.5)
    assert roll.note_height < roll._LABEL_MIN_ROW_HEIGHT
    assert _keyboard_highlight_rows(roll, image)  # still highlighted
    assert not _keyboard_has_dark_text_over_highlight(roll, image)  # but not named


def test_pitch_note_name():
    from notegrabber.gui.widgets.piano_roll import _pitch_note_name

    assert _pitch_note_name(60) == "C4"
    assert _pitch_note_name(61) == "C#4"
    assert _pitch_note_name(69) == "A4"
    assert _pitch_note_name(48) == "C3"
