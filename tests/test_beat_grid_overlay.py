"""Beat-grid overlay on the piano roll (issue #14 follow-up).

Beat tracking produces a tempo *and* beat positions. Drawing those positions
makes the estimate checkable by eye: you can see whether the detected grid
lines up with the music instead of taking the BPM number on trust.

Beats are irregular timestamps rather than a fixed interval, so this cannot
reuse the time grid's stepping loop; every fourth beat is drawn in the accent
colour as a nominal downbeat.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _window(song_seconds=30.0):
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1500, 900)
    window.show()
    app.processEvents()

    midi_notes = list(range(48, 84))
    frames = [i * 0.1 for i in range(int(song_seconds * 10))]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frames,
        activations=[[0.0] * len(midi_notes) for _ in frames],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.state.heatmap = heatmap
    window.state.extracted_notes = []
    window._set_display_notes([])
    app.processEvents()
    return app, window


def _painted_columns(app, roll):
    """Count canvas columns differing from the plain background at mid-height."""

    from PySide6.QtGui import QPixmap

    app.processEvents()
    pixmap = QPixmap(roll.size())
    roll.render(pixmap)
    image = pixmap.toImage()
    y = image.height() // 2
    background = image.pixelColor(roll.keyboard_width + 3, y).name()
    return sum(
        1
        for x in range(roll.keyboard_width + 2, min(image.width(), 1400))
        if image.pixelColor(x, y).name() != background
    )


# --- widget state -----------------------------------------------------------


def test_beat_times_default_to_empty():
    app, window = _window()
    try:
        assert window.piano_roll.beat_times == ()
        assert window.piano_roll.show_beat_grid is True
    finally:
        window.close()
        app.processEvents()


def test_set_beat_times_accepts_any_sequence():
    app, window = _window()
    try:
        roll = window.piano_roll
        roll.set_beat_times([0.5, 1.0, 1.5])
        assert roll.beat_times == (0.5, 1.0, 1.5)

        roll.set_beat_times(None)
        assert roll.beat_times == ()
    finally:
        window.close()
        app.processEvents()


# --- painting ---------------------------------------------------------------


def test_beats_are_actually_painted():
    app, window = _window()
    try:
        roll = window.piano_roll
        before = _painted_columns(app, roll)

        roll.set_beat_times([0.5 * i for i in range(1, 40)])
        after = _painted_columns(app, roll)

        assert after > before, "beat lines should add painted columns"
    finally:
        window.close()
        app.processEvents()


def test_hiding_the_grid_removes_the_lines():
    app, window = _window()
    try:
        roll = window.piano_roll
        baseline = _painted_columns(app, roll)
        roll.set_beat_times([0.5 * i for i in range(1, 40)])
        with_beats = _painted_columns(app, roll)

        roll.set_show_beat_grid(False)
        assert _painted_columns(app, roll) == baseline

        roll.set_show_beat_grid(True)
        assert _painted_columns(app, roll) == with_beats
    finally:
        window.close()
        app.processEvents()


def test_downbeats_are_drawn_differently():
    """Every fourth beat uses the accent colour, so bars are visible."""

    from PySide6.QtGui import QPixmap

    app, window = _window()
    try:
        roll = window.piano_roll
        # Start away from t=0 so the playhead does not colour the first beat.
        # Indexing is the widget's own: beats[0] is downbeat 0.
        beats = [0.5 * i for i in range(1, 17)]
        roll.set_beat_times(beats)
        app.processEvents()

        pixmap = QPixmap(roll.size())
        roll.render(pixmap)
        image = pixmap.toImage()
        y = image.height() // 2

        downbeat_colors = set()
        offbeat_colors = set()
        for index, seconds in enumerate(beats):
            x = roll.x_for_seconds(seconds)
            if x >= image.width():
                break
            color = image.pixelColor(x, y).name()
            if index % roll.beats_per_bar == 0:
                downbeat_colors.add(color)
            else:
                offbeat_colors.add(color)

        assert downbeat_colors and offbeat_colors
        assert downbeat_colors.isdisjoint(offbeat_colors), "downbeats must stand out"
    finally:
        window.close()
        app.processEvents()


def test_no_beats_paints_nothing_extra():
    app, window = _window()
    try:
        roll = window.piano_roll
        baseline = _painted_columns(app, roll)
        roll.set_beat_times([])
        assert _painted_columns(app, roll) == baseline
    finally:
        window.close()
        app.processEvents()


def test_beats_outside_the_timeline_do_not_crash():
    app, window = _window(song_seconds=5.0)
    try:
        roll = window.piano_roll
        # Far past the end, and a negative time, which x_for_seconds clamps.
        roll.set_beat_times([-2.0, 0.5, 999.0])
        _painted_columns(app, roll)
    finally:
        window.close()
        app.processEvents()


def test_drawing_without_a_heatmap_is_a_no_op():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    roll = PianoRollWidget()
    roll.set_beat_times([0.5, 1.0])
    roll.resize(400, 200)
    roll.render(roll.grab())  # must not raise with heatmap None
    app.processEvents()


# --- wiring -----------------------------------------------------------------


def test_the_checkbox_drives_the_overlay():
    app, window = _window()
    try:
        window.controls.show_beat_grid.setChecked(False)
        app.processEvents()
        assert window.piano_roll.show_beat_grid is False

        window.controls.show_beat_grid.setChecked(True)
        app.processEvents()
        assert window.piano_roll.show_beat_grid is True
    finally:
        window.close()
        app.processEvents()


def test_analysis_results_feed_the_overlay():
    """Beats from the worker reach the roll, in the full-song timeline."""

    from pathlib import Path

    from notegrabber.gui.analysis_worker import AnalysisResult

    app, window = _window()
    try:
        result = AnalysisResult(
            audio_path=Path("/tmp/x.wav"),
            backend="basic-pitch",
            midi_path=Path("/tmp/x.mid"),
            heatmap_path=None,
            rendered_midi_wav=None,
            render_error=None,
            notes=[],
            heatmap=window.state.heatmap,
            audio_tempo_bpm=120.0,
            beat_times=(0.5, 1.0, 1.5, 2.0),
        )
        window._analysis_finished(result)
        app.processEvents()

        assert window.piano_roll.beat_times == (0.5, 1.0, 1.5, 2.0)
        assert window._audio_tempo_bpm == pytest.approx(120.0)
    finally:
        window.close()
        app.processEvents()


# --- beats per bar ----------------------------------------------------------


def _downbeat_positions(app, roll, beats):
    """Indices drawn in the accent (downbeat) colour rather than the plain one.

    Compares against the colour an ordinary beat is actually painted, sampled
    from a run with the emphasis switched off. Inferring "ordinary" as the most
    common colour instead would misreport any beat that happens to coincide
    with a time-grid line or the playhead.
    """

    from PySide6.QtGui import QPixmap

    def sample():
        app.processEvents()
        pixmap = QPixmap(roll.size())
        roll.render(pixmap)
        image = pixmap.toImage()
        y = image.height() // 2
        colors = {}
        for index, seconds in enumerate(beats):
            x = roll.x_for_seconds(seconds)
            if x >= image.width():
                break
            colors[index] = image.pixelColor(x, y).name()
        return colors

    requested = roll.beats_per_bar
    roll.set_beats_per_bar(1)
    plain = sample()
    roll.set_beats_per_bar(requested)
    emphasised = sample()

    return {
        index
        for index, color in emphasised.items()
        if plain.get(index) is not None and color != plain[index]
    }


def test_beats_per_bar_defaults_to_four():
    app, window = _window()
    try:
        assert window.piano_roll.beats_per_bar == 4
        assert window.controls.beats_per_bar.value() == 4
    finally:
        window.close()
        app.processEvents()


def test_three_four_emphasises_every_third_beat():
    """A waltz must not be highlighted as if it were 4/4."""

    app, window = _window()
    try:
        roll = window.piano_roll
        beats = [0.5 * i for i in range(1, 16)]
        roll.set_beat_times(beats)
        roll.set_beats_per_bar(3)

        emphasised = _downbeat_positions(app, roll, beats)
        assert emphasised, "some beat should be emphasised"
        assert all(index % 3 == 0 for index in emphasised)
    finally:
        window.close()
        app.processEvents()


def test_one_beat_per_bar_removes_the_emphasis():
    """No metre known: every detected beat is drawn alike."""

    app, window = _window()
    try:
        roll = window.piano_roll
        beats = [0.5 * i for i in range(1, 16)]
        roll.set_beat_times(beats)
        roll.set_beats_per_bar(1)

        assert _downbeat_positions(app, roll, beats) == set()
    finally:
        window.close()
        app.processEvents()


def test_beats_per_bar_is_clamped_to_at_least_one():
    app, window = _window()
    try:
        roll = window.piano_roll
        roll.set_beats_per_bar(0)
        assert roll.beats_per_bar == 1
        roll.set_beats_per_bar(-5)
        assert roll.beats_per_bar == 1
    finally:
        window.close()
        app.processEvents()


def test_the_spin_box_drives_the_overlay():
    app, window = _window()
    try:
        window.controls.beats_per_bar.setValue(3)
        app.processEvents()
        assert window.piano_roll.beats_per_bar == 3

        window.controls.beats_per_bar.setValue(6)
        app.processEvents()
        assert window.piano_roll.beats_per_bar == 6
    finally:
        window.close()
        app.processEvents()


# --- density at low zoom ----------------------------------------------------


def test_dense_beats_fall_back_to_downbeats_only():
    """At fit-to-song a one-minute track puts beats ~12px apart.

    That reads as a hatch rather than a grid, so below a legible spacing only
    downbeats are drawn.
    """

    app, window = _window(song_seconds=60.0)
    try:
        roll = window.piano_roll
        # ~0.7s apart over a minute: dense at zoom 1, legible zoomed in.
        beats = [0.7 * i for i in range(1, 80)]
        roll.set_beat_times(beats)
        roll.set_beats_per_bar(4)
        roll.zoom_to(1.0)
        app.processEvents()

        spacing = roll._beat_spacing_pixels()
        assert spacing is not None and spacing < roll.MIN_BEAT_SPACING_PIXELS
        assert roll._beat_grid_is_visible() is True, "downbeats should still show"
    finally:
        window.close()
        app.processEvents()


def test_zooming_in_restores_every_beat():
    app, window = _window(song_seconds=60.0)
    try:
        roll = window.piano_roll
        beats = [0.7 * i for i in range(1, 80)]
        roll.set_beat_times(beats)
        roll.zoom_to(8.0)
        app.processEvents()

        spacing = roll._beat_spacing_pixels()
        assert spacing is not None and spacing >= roll.MIN_BEAT_SPACING_PIXELS
        assert roll._beat_grid_is_visible() is True
    finally:
        window.close()
        app.processEvents()


def test_grid_hides_when_even_downbeats_are_too_dense():
    """Nothing legible to draw: leave the timeline to the seconds grid."""

    app, window = _window(song_seconds=600.0)
    try:
        roll = window.piano_roll
        roll.set_beat_times([0.5 * i for i in range(1, 1200)])
        roll.set_beats_per_bar(1)  # no downbeats to fall back to
        roll.zoom_to(1.0)
        app.processEvents()

        assert roll._beat_grid_is_visible() is False
    finally:
        window.close()
        app.processEvents()


def test_beat_spacing_needs_at_least_two_beats():
    app, window = _window()
    try:
        roll = window.piano_roll
        roll.set_beat_times([])
        assert roll._beat_spacing_pixels() is None
        roll.set_beat_times([1.0])
        assert roll._beat_spacing_pixels() is None
    finally:
        window.close()
        app.processEvents()


def test_seconds_grid_only_recedes_while_beats_are_shown():
    """A dashed, dimmed seconds grid with no beats visible would be pointless."""

    app, window = _window()
    try:
        roll = window.piano_roll
        assert roll._beat_grid_is_visible() is False  # no beats yet

        roll.set_beat_times([0.5 * i for i in range(1, 20)])
        roll.zoom_to(8.0)
        app.processEvents()
        assert roll._beat_grid_is_visible() is True

        roll.set_show_beat_grid(False)
        assert roll._beat_grid_is_visible() is False
    finally:
        window.close()
        app.processEvents()
