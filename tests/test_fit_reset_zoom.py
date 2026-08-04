"""Fit and Reset zoom controls (issue #10).

Fit zooms to whatever the user is most plausibly working on, in a deliberate
order: selected notes first (what editing leaves you holding, and the one span
there was otherwise no way to zoom to), then the dragged analysis range, then
the whole song. Fitting notes zooms *both* axes -- a time-only fit leaves a
selection as a thin horizontal sliver. Reset returns to the whole-song view.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.4, velocity=90):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity
    )


def _window(notes=None, song_seconds=180.0):
    """A window over a long song, so fitting a short span is a real zoom."""

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1600, 950)
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
    if notes is None:
        notes = [_note(60, 100.0), _note(64, 101.0), _note(67, 102.0)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def _visible_x(window, seconds):
    """x of a time within the viewport, accounting for horizontal scroll."""

    roll = window.piano_roll
    return roll.x_for_seconds(seconds) - roll._horizontal_scroll_offset()


def _is_on_screen(window, seconds):
    roll = window.piano_roll
    x = _visible_x(window, seconds)
    return roll.keyboard_width <= x <= roll._viewport_width()


# --- fit to selected notes --------------------------------------------------


def test_fit_brings_far_away_selected_notes_on_screen():
    app, window = _window()
    try:
        # The notes sit at ~100s in a 180s song: off screen at zoom 1.
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._fit_zoom()
        app.processEvents()

        for note in window.state.current_notes:
            assert _is_on_screen(window, note.start_seconds), f"{note.pitch} not visible"
        assert window.piano_roll.horizontal_zoom > 1.0
    finally:
        window.close()
        app.processEvents()


def test_fit_zooms_both_axes_for_a_note_selection():
    """A time-only fit would leave the selection a thin sliver vertically."""

    app, window = _window()
    try:
        before_vertical = window.piano_roll.vertical_zoom
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._fit_zoom()
        app.processEvents()

        assert window.piano_roll.horizontal_zoom > 1.0
        assert window.piano_roll.vertical_zoom > before_vertical
    finally:
        window.close()
        app.processEvents()


def test_fit_fills_a_useful_share_of_the_viewport():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._fit_zoom()
        app.processEvents()

        notes = window.state.current_notes
        left = _visible_x(window, min(note.start_seconds for note in notes))
        right = _visible_x(window, max(note.end_seconds for note in notes))
        share = (right - left) / window.piano_roll._viewport_width()
        # Comfortably more than half the view, and not flush against the edges.
        assert 0.5 < share < 1.0
    finally:
        window.close()
        app.processEvents()


def test_fit_centres_a_single_selected_note():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({1})
        app.processEvents()
        window._fit_zoom()
        app.processEvents()

        note = window.state.current_notes[1]
        centre = _visible_x(window, note.start_seconds + note.duration_seconds / 2)
        viewport_centre = window.piano_roll._viewport_width() / 2
        assert abs(centre - viewport_centre) < window.piano_roll._viewport_width() * 0.15
    finally:
        window.close()
        app.processEvents()


def test_fit_reports_the_selection_in_the_status_line():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1})
        app.processEvents()
        window._fit_zoom()
        app.processEvents()

        assert "2 selected notes" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


# --- fallbacks --------------------------------------------------------------


def test_fit_falls_back_to_the_analysis_range():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices(set())
        window.waveform.selection_start_seconds = 40.0
        window.waveform.selection_duration_seconds = 10.0
        app.processEvents()

        window._fit_zoom()
        app.processEvents()

        assert window.piano_roll.horizontal_zoom > 1.0
        assert _is_on_screen(window, 45.0), "the range midpoint should be visible"
        assert "analysis range" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_fit_falls_back_to_the_whole_song():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices(set())
        window.waveform.selection_start_seconds = None
        window.waveform.selection_duration_seconds = None
        window.piano_roll.zoom_to(8.0)
        app.processEvents()

        window._fit_zoom()
        app.processEvents()

        assert window.piano_roll.horizontal_zoom == pytest.approx(1.0)
        assert "whole song" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_selection_takes_priority_over_the_analysis_range():
    app, window = _window()
    try:
        window.waveform.selection_start_seconds = 5.0
        window.waveform.selection_duration_seconds = 10.0
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()

        window._fit_zoom()
        app.processEvents()

        # Fitted the notes at ~100s, not the range at 5-15s.
        assert _is_on_screen(window, 101.0)
        assert not _is_on_screen(window, 10.0)
    finally:
        window.close()
        app.processEvents()


def test_fit_without_a_heatmap_says_so():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.show()
    app.processEvents()
    try:
        window._fit_zoom()
        assert "analyze audio first" in window.transport.status_label.text().lower()
    finally:
        window.close()
        app.processEvents()


# --- reset ------------------------------------------------------------------


def test_reset_returns_to_the_whole_song_view():
    app, window = _window()
    try:
        window.piano_roll.set_selected_indices({0, 1, 2})
        app.processEvents()
        window._fit_zoom()
        app.processEvents()
        assert window.piano_roll.horizontal_zoom > 1.0

        window._reset_zoom()
        app.processEvents()

        roll = window.piano_roll
        assert roll.horizontal_zoom == pytest.approx(1.0)
        assert roll.vertical_zoom == pytest.approx(1.0)
        assert roll._horizontal_scroll_offset() == 0
        assert roll._vertical_scroll_offset() == 0
    finally:
        window.close()
        app.processEvents()


def test_reset_is_idempotent():
    app, window = _window()
    try:
        window._reset_zoom()
        app.processEvents()
        window._reset_zoom()
        app.processEvents()

        assert window.piano_roll.horizontal_zoom == pytest.approx(1.0)
        assert window.piano_roll._horizontal_scroll_offset() == 0
    finally:
        window.close()
        app.processEvents()


# --- widget-level -----------------------------------------------------------


def test_fit_to_span_leaves_vertical_zoom_alone_without_a_pitch_range():
    app, window = _window()
    try:
        roll = window.piano_roll
        before = roll.vertical_zoom
        roll.fit_to_span(10.0, 20.0)
        app.processEvents()

        assert roll.vertical_zoom == pytest.approx(before)
        assert roll.horizontal_zoom > 1.0
    finally:
        window.close()
        app.processEvents()


def test_fit_to_span_handles_a_zero_length_span():
    """A single instant must not divide by zero or explode the zoom."""

    app, window = _window()
    try:
        roll = window.piano_roll
        roll.fit_to_span(30.0, 30.0)
        app.processEvents()

        assert roll.horizontal_zoom > 1.0
        assert roll._horizontal_scroll_offset() >= 0
    finally:
        window.close()
        app.processEvents()


def test_fit_to_span_without_a_heatmap_is_a_no_op():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    roll = PianoRollWidget()
    roll.fit_to_span(0.0, 10.0, pitch_range=(60, 67))
    assert roll.horizontal_zoom == pytest.approx(1.0)
    app.processEvents()


def test_buttons_exist_in_the_action_bar_and_are_wired():
    app, window = _window()
    try:
        bar = window.controls.build_action_bar()
        labels = {
            child.text()
            for child in bar.findChildren(type(window.controls.fit_button))
        }
        assert {"Fit", "Reset"} <= labels

        fired = []
        window.controls.fit_requested.connect(lambda: fired.append("fit"))
        window.controls.reset_zoom_requested.connect(lambda: fired.append("reset"))
        window.controls.fit_button.click()
        window.controls.reset_zoom_button.click()
        assert fired == ["fit", "reset"]
    finally:
        window.close()
        app.processEvents()
