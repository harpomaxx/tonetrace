"""The piano-roll keyboard must highlight keys that are sounding at the playhead.

Renders the widget offscreen and checks the warm accent color appears on the
keyboard strip only when the playhead sits inside a note on that pitch.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HIGHLIGHT = (255, 196, 84)


def _make_heatmap_and_note():
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
    note = GuiMidiNote(pitch=60, start_seconds=0.5, duration_seconds=1.0, velocity=90)
    return heatmap, [note]


def _keyboard_has_highlight(playhead_seconds: float) -> bool:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    heatmap, notes = _make_heatmap_and_note()

    roll = PianoRollWidget()
    roll.resize(600, 300)
    roll.set_data(heatmap, notes, full_duration_seconds=2.0)
    roll.set_playhead(playhead_seconds)

    image = QImage(roll.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0))
    roll.render(image, QPoint(0, 0))

    # Only inspect the keyboard strip (left edge, keyboard_width wide).
    for x in range(min(roll.keyboard_width, image.width())):
        for y in range(image.height()):
            c = image.pixelColor(x, y)
            if (
                abs(c.red() - HIGHLIGHT[0]) < 30
                and abs(c.green() - HIGHLIGHT[1]) < 30
                and abs(c.blue() - HIGHLIGHT[2]) < 30
            ):
                return True
    return False


def test_key_highlighted_when_playhead_inside_note():
    # Note spans [0.5, 1.5); playhead at 1.0 is inside it.
    assert _keyboard_has_highlight(1.0)


def test_key_not_highlighted_before_note_starts():
    # Playhead at 0.2 is before the note starts at 0.5.
    assert not _keyboard_has_highlight(0.2)


def test_key_not_highlighted_after_note_ends():
    # Playhead at 1.8 is past the note end at 1.5.
    assert not _keyboard_has_highlight(1.8)


def test_active_pitch_set_tracks_playhead():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    heatmap, notes = _make_heatmap_and_note()
    roll = PianoRollWidget()
    roll.set_data(heatmap, notes, full_duration_seconds=2.0)

    roll.set_playhead(1.0)
    assert roll._active_pitches() == {60}
    roll.set_playhead(0.0)
    assert roll._active_pitches() == set()
