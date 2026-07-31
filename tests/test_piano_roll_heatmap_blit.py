"""The zoomed-out heatmap must render via the cached-image blit and, crucially,
still show a transient one-frame activation (the max-reduction the blit replaces
must be preserved).
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("numpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_heatmap(spike_frame: int, frame_count: int = 2000):
    """A heatmap that is silent everywhere except one bright frame on one pitch.

    Many frames + a modest full duration forces the aggregated (zoomed-out)
    column regime where several frames collapse into each screen pixel.
    """

    from notegrabber.gui.state import GuiHeatmap

    pitches = list(range(58, 66))
    frame_step = 0.01
    frames = [i * frame_step for i in range(frame_count)]
    activations = [[0.0 for _ in pitches] for _ in frames]
    activations[spike_frame][3] = 1.0  # one bright cell, one frame wide
    return GuiHeatmap(
        backend="basic-pitch",
        midi_notes=pitches,
        frame_times=frames,
        activations=activations,
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )


def _render_zoomed_out(heatmap):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    roll = PianoRollWidget()
    roll.resize(600, 300)
    # Fit the whole (long) heatmap into the viewport -> zoomed out -> column regime.
    roll.set_data(heatmap, [], full_duration_seconds=heatmap.duration_seconds)

    image = QImage(roll.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0))
    roll.render(image, QPoint(0, 0))
    return roll, image


def _has_heat_pixel(image) -> bool:
    # The heat ramp at high activation is a warm red/orange: strong red, low blue,
    # green well below red (ramp maxes at r=255, g=168, b=8 at activation 1.0).
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.red() > 200 and c.blue() < 60 and c.green() < c.red() - 40 and c.alpha() > 120:
                return True
    return False


def test_zoomed_out_uses_blit_path():
    heatmap = _make_heatmap(spike_frame=1000)
    roll, _ = _render_zoomed_out(heatmap)
    # The cache should have been populated by the paint -> blit path.
    assert roll._heatmap_image is not None


def test_transient_activation_survives_low_zoom():
    heatmap = _make_heatmap(spike_frame=1000)
    _, image = _render_zoomed_out(heatmap)
    assert _has_heat_pixel(image), "one-frame activation vanished when zoomed out"


def test_no_heat_when_all_silent():
    from notegrabber.gui.state import GuiHeatmap

    pitches = list(range(58, 66))
    frames = [i * 0.01 for i in range(2000)]
    silent = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=pitches,
        frame_times=frames,
        activations=[[0.0 for _ in pitches] for _ in frames],
        sample_rate=100,
        hop_size=1,
        window_size=1,
    )
    _, image = _render_zoomed_out(silent)
    assert not _has_heat_pixel(image)


def test_blit_image_is_bounded_to_viewport_not_canvas():
    """Zooming in must not build a full-canvas-width image.

    Regression guard for the 'unusable when zoomed with heatmap' bug: the cached
    image width must track the viewport (plus margin), not the (possibly huge)
    zoomed-in canvas width. Otherwise the per-column build loop grows without
    bound as you zoom in.
    """

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QScrollArea

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    heatmap = _make_heatmap(spike_frame=1000, frame_count=15000)

    area = QScrollArea()
    area.resize(800, 400)
    roll = PianoRollWidget()
    area.setWidget(roll)
    area.setWidgetResizable(False)
    roll.set_data(heatmap, [], full_duration_seconds=heatmap.duration_seconds)
    area.show()

    def paint():
        img = QImage(area.viewport().size(), QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        roll.render(img, QPoint(-roll._horizontal_scroll_offset(), 0))

    viewport_w = area.viewport().width()
    for zoom in (1, 8, 16):
        roll.set_horizontal_zoom(zoom)
        QApplication.processEvents()
        roll._invalidate_heatmap_image()
        paint()
        if roll._heatmap_image is None:
            continue  # numpy unavailable; blit path skipped
        # Image covers the viewport plus at most a margin on each side, never the
        # whole canvas (which is viewport * zoom).
        max_expected = viewport_w + 2 * roll._HEATMAP_SPAN_MARGIN + 64
        assert roll._heatmap_image.width() <= max_expected, (
            f"zoom {zoom}: image width {roll._heatmap_image.width()} exceeds "
            f"viewport-bounded {max_expected} (canvas width {roll.width()})"
        )
