"""Wheel zoom keeps the point under the cursor fixed (issue #10).

Both axes used to rescale without touching the scroll offset, so the view
zoomed around the canvas's top-left corner:

- Ctrl+wheel (time): the moment under the cursor slid sideways ~17s after three
  wheel clicks on a 60s file.
- Shift+wheel (pitch): rows grow downward from the canvas top, so the pitch
  under the cursor walked nearly three octaves (MIDI 38 -> 72) in five clicks.

Either way the point of interest had to be chased with the scrollbars. These
tests pin that the anchored time and pitch stay under the anchor across zoom
in, zoom out, and round trips.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# The scroll bar is integer-valued, so the anchored time can only be held to
# within a pixel's worth of seconds. Half a pixel at the *fitted* scale is a
# generous bound that still fails loudly on any real drift.
_PIXEL_TOLERANCE_SECONDS = 0.05


def _windowed_roll(duration_seconds=60.0, pitch_range=range(48, 84), window_height=1000):
    """A piano roll inside its real QScrollArea, with a long-enough heatmap.

    The scroll area matters: cursor anchoring adjusts the horizontal scroll
    offset, so a bare widget with no scroll parent cannot exercise it.
    """

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1600, window_height)
    window.show()
    app.processEvents()

    midi_notes = list(pitch_range)
    frame_count = int(duration_seconds * 20)
    frames = [i * 0.05 for i in range(frame_count)]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frames,
        activations=[[0.0] * len(midi_notes) for _ in frames],
        sample_rate=20,
        hop_size=1,
        window_size=1,
    )
    window.state.heatmap = heatmap
    window.state.extracted_notes = []
    window._set_display_notes([])
    app.processEvents()
    return app, window


def _time_under(roll, viewport_x):
    """The time currently drawn at ``viewport_x`` pixels into the viewport."""

    return roll.seconds_at_x(roll._horizontal_scroll_offset() + viewport_x)


def test_seconds_at_x_inverts_x_for_seconds():
    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        # x_for_seconds truncates to an integer pixel, so the round trip is only
        # exact to within one pixel's worth of time.
        one_pixel = roll.seconds_per_pixel
        for seconds in (0.0, 1.5, 12.25, 47.0):
            assert roll.seconds_at_x(roll.x_for_seconds(seconds)) == pytest.approx(seconds, abs=one_pixel)
        # The keyboard gutter is before t=0 and must not produce negative times.
        assert roll.seconds_at_x(0.0) == 0.0
    finally:
        window.close()
        app.processEvents()


def test_wheel_zoom_in_keeps_time_under_the_cursor():
    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        bar = roll._horizontal_scroll_bar()
        bar.setValue(0)
        roll.zoom_to(1.0)
        app.processEvents()

        cursor_x = 900.0
        before = _time_under(roll, cursor_x)

        for _ in range(3):
            roll.zoom_by_wheel_delta(120, anchor_x=roll._horizontal_scroll_offset() + cursor_x)
            app.processEvents()

        assert roll.horizontal_zoom > 1.0
        assert _time_under(roll, cursor_x) == pytest.approx(before, abs=_PIXEL_TOLERANCE_SECONDS)
    finally:
        window.close()
        app.processEvents()


def test_wheel_zoom_out_keeps_time_under_the_cursor():
    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        bar = roll._horizontal_scroll_bar()
        bar.setValue(0)
        roll.zoom_to(6.0)
        app.processEvents()

        cursor_x = 700.0
        before = _time_under(roll, cursor_x)

        for _ in range(3):
            roll.zoom_by_wheel_delta(-120, anchor_x=roll._horizontal_scroll_offset() + cursor_x)
            app.processEvents()

        assert roll.horizontal_zoom < 6.0
        assert _time_under(roll, cursor_x) == pytest.approx(before, abs=_PIXEL_TOLERANCE_SECONDS)
    finally:
        window.close()
        app.processEvents()


def test_zoom_round_trip_returns_to_the_same_view():
    """In then out by the same amount lands back where it started."""

    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        bar = roll._horizontal_scroll_bar()
        bar.setValue(0)
        roll.zoom_to(2.0)
        app.processEvents()

        cursor_x = 800.0
        before = _time_under(roll, cursor_x)
        start_zoom = roll.horizontal_zoom

        for delta in (120, 120, -120, -120):
            roll.zoom_by_wheel_delta(delta, anchor_x=roll._horizontal_scroll_offset() + cursor_x)
            app.processEvents()

        assert roll.horizontal_zoom == pytest.approx(start_zoom, rel=1e-6)
        assert _time_under(roll, cursor_x) == pytest.approx(before, abs=_PIXEL_TOLERANCE_SECONDS)
    finally:
        window.close()
        app.processEvents()


def test_zoom_without_anchor_holds_the_viewport_centre():
    """Button/keyboard zoom has no cursor, so it must not jump the view."""

    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        bar = roll._horizontal_scroll_bar()
        roll.zoom_to(4.0)
        app.processEvents()
        bar.setValue(bar.maximum() // 2)
        app.processEvents()

        centre_x = roll._viewport_width() / 2.0
        before = _time_under(roll, centre_x)

        roll.zoom_to(6.0)
        app.processEvents()

        assert _time_under(roll, centre_x) == pytest.approx(before, abs=_PIXEL_TOLERANCE_SECONDS)
    finally:
        window.close()
        app.processEvents()


def test_zoom_clamps_still_apply_and_scroll_stays_valid():
    """Anchoring must not push the scroll offset negative at the extremes."""

    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        bar = roll._horizontal_scroll_bar()
        bar.setValue(0)
        roll.zoom_to(1.0)
        app.processEvents()

        # Zooming out at the very start is already at the clamp.
        for _ in range(5):
            roll.zoom_by_wheel_delta(-120, anchor_x=roll.keyboard_width + 10.0)
            app.processEvents()

        assert roll.horizontal_zoom == pytest.approx(1.0)
        assert bar.value() >= 0

        # Enough wheel clicks to reach the upper clamp, whatever it is set to
        # (raised to 256 for Fit in issue #10; 1.2**x, so ~31 clicks to 256).
        for _ in range(80):
            roll.zoom_by_wheel_delta(120, anchor_x=roll._horizontal_scroll_offset() + 500.0)
            app.processEvents()

        ceiling = roll.horizontal_zoom
        roll.zoom_by_wheel_delta(120, anchor_x=roll._horizontal_scroll_offset() + 500.0)
        app.processEvents()
        assert roll.horizontal_zoom == pytest.approx(ceiling), "zoom must stop at the clamp"
        assert bar.value() >= 0
    finally:
        window.close()
        app.processEvents()


def test_zoom_without_a_scroll_area_does_not_crash():
    """A bare widget (no scroll parent) still zooms; there is just nothing to pan."""

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    roll = PianoRollWidget()
    assert roll._horizontal_scroll_bar() is None

    roll.zoom_by_wheel_delta(120, anchor_x=400.0)
    assert roll.horizontal_zoom > 1.0
    app.processEvents()


# --- vertical (pitch) zoom --------------------------------------------------


def _tall_roll():
    """A roll whose pitch rows overflow the viewport, so it can scroll vertically."""

    return _windowed_roll(pitch_range=range(36, 96), window_height=700)


def _pitch_under(roll, viewport_y):
    """The MIDI pitch currently drawn at ``viewport_y`` into the viewport."""

    return roll._pitch_at_y(roll._vertical_scroll_offset() + viewport_y)


def test_vertical_wheel_zoom_in_keeps_pitch_under_the_cursor():
    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        bar = window.piano_scroll.verticalScrollBar()
        # Only meaningful if there is somewhere to scroll.
        assert bar.maximum() > 0

        cursor_y = 300.0
        before = _pitch_under(roll, cursor_y)
        assert before is not None

        for _ in range(5):
            roll.vertical_zoom_by_wheel_delta(120, anchor_y=roll._vertical_scroll_offset() + cursor_y)
            app.processEvents()

        assert roll.vertical_zoom > 1.0
        assert _pitch_under(roll, cursor_y) == before
    finally:
        window.close()
        app.processEvents()


def test_vertical_wheel_zoom_out_keeps_pitch_under_the_cursor():
    """Zoom out holds the pitch while the anchor can still be honoured.

    Once the view has been pulled to the very top (scroll offset 0), holding a
    point still would need a negative offset, which a scroll bar cannot express;
    the pitch then necessarily drifts. So this stops while there is still room
    to scroll and asserts the anchor exactly, rather than asserting something
    the geometry makes impossible.
    """

    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        bar = window.piano_scroll.verticalScrollBar()
        roll.vertical_zoom_to(3.0)
        app.processEvents()

        cursor_y = 250.0
        before = _pitch_under(roll, cursor_y)
        assert before is not None

        steps = 0
        for _ in range(4):
            roll.vertical_zoom_by_wheel_delta(-120, anchor_y=roll._vertical_scroll_offset() + cursor_y)
            app.processEvents()
            steps += 1
            if bar.value() == 0:
                # Pinned to the top: no headroom left to keep anchoring.
                break

        assert steps > 0
        assert roll.vertical_zoom < 3.0
        assert _pitch_under(roll, cursor_y) == before
    finally:
        window.close()
        app.processEvents()


def test_vertical_zoom_out_at_the_top_edge_stays_pinned_and_valid():
    """At scroll 0 the anchor cannot be honoured; the view must still stay sane."""

    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        bar = window.piano_scroll.verticalScrollBar()
        bar.setValue(0)
        app.processEvents()

        for _ in range(6):
            roll.vertical_zoom_by_wheel_delta(-120, anchor_y=roll._vertical_scroll_offset() + 50.0)
            app.processEvents()
            # Never negative, never past the end of a shrinking canvas.
            assert 0 <= bar.value() <= bar.maximum()
    finally:
        window.close()
        app.processEvents()


def test_vertical_zoom_round_trip_returns_to_the_same_view():
    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        roll.vertical_zoom_to(2.0)
        app.processEvents()

        cursor_y = 280.0
        before = _pitch_under(roll, cursor_y)
        start_zoom = roll.vertical_zoom

        for delta in (120, 120, -120, -120):
            roll.vertical_zoom_by_wheel_delta(delta, anchor_y=roll._vertical_scroll_offset() + cursor_y)
            app.processEvents()

        assert roll.vertical_zoom == pytest.approx(start_zoom, rel=1e-6)
        assert _pitch_under(roll, cursor_y) == before
    finally:
        window.close()
        app.processEvents()


def test_vertical_zoom_without_anchor_holds_the_viewport_centre():
    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        bar = window.piano_scroll.verticalScrollBar()
        roll.vertical_zoom_to(2.0)
        app.processEvents()
        bar.setValue(bar.maximum() // 2)
        app.processEvents()

        centre_y = roll._viewport_height() / 2.0
        before = _pitch_under(roll, centre_y)

        roll.vertical_zoom_to(3.0)
        app.processEvents()

        assert _pitch_under(roll, centre_y) == before
    finally:
        window.close()
        app.processEvents()


def test_vertical_zoom_clamps_and_keeps_scroll_valid():
    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        bar = window.piano_scroll.verticalScrollBar()

        for _ in range(20):
            roll.vertical_zoom_by_wheel_delta(-120, anchor_y=roll._vertical_scroll_offset() + 10.0)
            app.processEvents()
        assert roll.vertical_zoom == pytest.approx(0.75)
        assert bar.value() >= 0

        for _ in range(30):
            roll.vertical_zoom_by_wheel_delta(120, anchor_y=roll._vertical_scroll_offset() + 200.0)
            app.processEvents()
        assert roll.vertical_zoom == pytest.approx(6.0)
        assert bar.value() >= 0
    finally:
        window.close()
        app.processEvents()


def test_vertical_zoom_without_a_scroll_area_does_not_crash():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    roll = PianoRollWidget()
    assert roll._vertical_scroll_bar() is None

    roll.vertical_zoom_by_wheel_delta(120, anchor_y=200.0)
    assert roll.vertical_zoom > 1.0
    app.processEvents()


def test_shift_wheel_event_anchors_on_the_cursor_position():
    """The Shift+wheel handler passes the cursor y through as the anchor."""

    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    app, window = _tall_roll()
    try:
        roll = window.piano_roll
        canvas_cursor_y = 300.0
        viewport_cursor_y = canvas_cursor_y - roll._vertical_scroll_offset()
        before = _pitch_under(roll, viewport_cursor_y)

        event = QWheelEvent(
            QPointF(400.0, canvas_cursor_y),
            QPointF(400.0, canvas_cursor_y),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ShiftModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        roll.wheelEvent(event)
        app.processEvents()

        assert roll.vertical_zoom > 1.0
        assert _pitch_under(roll, viewport_cursor_y) == before
    finally:
        window.close()
        app.processEvents()


def test_wheel_event_anchors_on_the_cursor_position():
    """The Ctrl+wheel handler passes the cursor x through as the anchor."""

    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    app, window = _windowed_roll()
    try:
        roll = window.piano_roll
        bar = roll._horizontal_scroll_bar()
        bar.setValue(0)
        roll.zoom_to(2.0)
        app.processEvents()

        # Mouse events arrive in the widget's own (canvas-absolute) coordinates:
        # scrolling moves the canvas to a negative x rather than offsetting the
        # events. So the anchor here is a canvas x, and the viewport x it
        # corresponds to is that minus the scroll offset.
        canvas_cursor_x = 850.0
        viewport_cursor_x = canvas_cursor_x - roll._horizontal_scroll_offset()
        before = _time_under(roll, viewport_cursor_x)

        event = QWheelEvent(
            QPointF(canvas_cursor_x, 100.0),
            QPointF(canvas_cursor_x, 100.0),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        roll.wheelEvent(event)
        app.processEvents()

        assert roll.horizontal_zoom > 2.0
        assert _time_under(roll, viewport_cursor_x) == pytest.approx(before, abs=_PIXEL_TOLERANCE_SECONDS)
    finally:
        window.close()
        app.processEvents()
