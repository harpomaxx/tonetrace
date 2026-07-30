"""The piano roll must draw a pitch-bend contour for notes that carry one.

Renders the widget to an offscreen image and checks for the distinctive bend-curve
color, and that the toggle suppresses it.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_heatmap_and_notes():
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    pitches = list(range(58, 66))
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
    # A note bending up +2 semitones over its life.
    bends = tuple((i * 0.1, i * 0.5) for i in range(5))  # up to +2 semitones
    note = GuiMidiNote(pitch=60, start_seconds=0.2, duration_seconds=1.2, velocity=90, pitch_bends=bends)
    return heatmap, [note]


def _render_has_bend_color(show_bends: bool) -> bool:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    heatmap, notes = _make_heatmap_and_notes()

    roll = PianoRollWidget()
    roll.resize(600, 300)
    roll.set_data(heatmap, notes, full_duration_seconds=2.0)
    roll.set_show_pitch_bends(show_bends)

    image = QImage(roll.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0))
    # Render the widget straight onto the image (a paint device), not via a
    # painter we own -- QWidget.render manages its own painter internally.
    roll.render(image, QPoint(0, 0))

    # The bend pen is a distinctive cyan (~120, 220, 255). Look for a pixel with
    # strong blue+green and low red anywhere in the note region.
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.green() > 170 and c.blue() > 200 and c.red() < 150:
                return True
    return False


def test_bend_contour_is_drawn_when_enabled():
    assert _render_has_bend_color(show_bends=True)


def test_bend_contour_is_hidden_when_toggled_off():
    assert not _render_has_bend_color(show_bends=False)
