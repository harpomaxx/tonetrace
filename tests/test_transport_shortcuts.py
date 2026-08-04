"""Keyboard transport shortcuts (issue #64).

Space toggles pause/resume of whatever was last playing, 1/2/3 switch between
Original / MIDI / Both, and 0 or Esc stops. All of them are guarded so they
never fire while a spin box or combo has focus, where Space and digits are
ordinary input.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.4, velocity=90):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity
    )


def _window(*, with_audio=True, with_midi=True):
    """A window with playable sources faked in.

    The players never actually decode these paths in the offscreen tests; what
    matters is the availability checks the transport makes before playing.
    """

    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1400, 900)
    window.show()
    app.processEvents()

    midi_notes = list(range(48, 84))
    frames = [i * 0.1 for i in range(200)]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frames,
        activations=[[0.0] * len(midi_notes) for _ in frames],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    notes = [_note(60, 2.0)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    if with_audio:
        window.state.audio_path = Path("/tmp/notegrabber-test-audio.wav")
    if with_midi:
        window.state.rendered_midi_wav = Path("/tmp/notegrabber-test-midi.wav")
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


# --- mode switching ---------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    ["original", "midi", "both"],
)
def test_play_mode_starts_each_mode(mode):
    app, window = _window()
    try:
        assert window._play_mode(mode) is True
        app.processEvents()
        assert window.playback_mode == mode
        assert window.last_playback_mode == mode
    finally:
        window.close()
        app.processEvents()


def test_switching_modes_while_playing_changes_source():
    app, window = _window()
    try:
        window._play_mode("original")
        app.processEvents()
        assert window.playback_mode == "original"

        window._play_mode("both")
        app.processEvents()
        assert window.playback_mode == "both"
    finally:
        window.close()
        app.processEvents()


def test_midi_mode_falls_back_to_original_without_a_preview():
    app, window = _window(with_midi=False)
    try:
        assert window._play_mode("midi") is True
        app.processEvents()

        assert window.playback_mode == "original"
        assert "playing the original instead" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_both_mode_falls_back_to_original_without_a_preview():
    app, window = _window(with_midi=False)
    try:
        assert window._play_mode("both") is True
        app.processEvents()
        assert window.playback_mode == "original"
    finally:
        window.close()
        app.processEvents()


def test_play_without_audio_reports_and_does_nothing():
    app, window = _window(with_audio=False, with_midi=False)
    try:
        assert window._play_mode("original") is False
        app.processEvents()

        assert window.playback_mode == "stopped"
        assert "Load an audio file" in window.transport.status_label.text()
    finally:
        window.close()
        app.processEvents()


# --- space toggle -----------------------------------------------------------


def test_space_pauses_while_playing():
    app, window = _window()
    try:
        window._play_mode("both")
        app.processEvents()

        window._toggle_playback()
        app.processEvents()
        assert window.playback_mode == "paused"
    finally:
        window.close()
        app.processEvents()


def test_space_resumes_the_mode_that_was_playing():
    app, window = _window()
    try:
        window._play_mode("midi")
        app.processEvents()
        window._toggle_playback()  # pause
        app.processEvents()

        window._toggle_playback()  # resume
        app.processEvents()
        assert window.playback_mode == "midi", "resume must not guess a different mode"
    finally:
        window.close()
        app.processEvents()


def test_space_after_stop_starts_the_last_mode():
    app, window = _window()
    try:
        window._play_mode("original")
        app.processEvents()
        window._stop_playback()
        app.processEvents()
        assert window.playback_mode == "stopped"

        window._toggle_playback()
        app.processEvents()
        assert window.playback_mode == "original"
    finally:
        window.close()
        app.processEvents()


def test_space_from_a_fresh_window_uses_the_default_mode():
    app, window = _window()
    try:
        assert window.playback_mode == "stopped"
        window._toggle_playback()
        app.processEvents()
        assert window.playback_mode == "both"
    finally:
        window.close()
        app.processEvents()


def test_stop_resets_to_stopped():
    app, window = _window()
    try:
        window._play_mode("both")
        app.processEvents()
        window._stop_playback()
        app.processEvents()
        assert window.playback_mode == "stopped"
    finally:
        window.close()
        app.processEvents()


# --- focus guard ------------------------------------------------------------


def _select_note(app, window):
    """Select a note, which is what enables the inspector spin boxes."""

    window.piano_roll.set_selected_indices({0})
    app.processEvents()


def test_guard_detects_a_focused_spin_box():
    app, window = _window()
    try:
        _select_note(app, window)
        assert window.note_velocity_spin.isEnabled()

        window.note_velocity_spin.setFocus()
        app.processEvents()
        assert window._text_entry_has_focus() is True
    finally:
        window.close()
        app.processEvents()


def test_guard_detects_a_focused_combo():
    app, window = _window()
    try:
        window.controls.backend_combo.setFocus()
        app.processEvents()
        assert window._text_entry_has_focus() is True
    finally:
        window.close()
        app.processEvents()


def test_guard_allows_the_piano_roll():
    app, window = _window()
    try:
        window.piano_roll.setFocus()
        app.processEvents()
        assert window._text_entry_has_focus() is False
    finally:
        window.close()
        app.processEvents()


def test_space_does_not_start_playback_from_a_spin_box():
    """The issue's key acceptance case."""

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, window = _window()
    try:
        _select_note(app, window)
        window.note_velocity_spin.setFocus()
        app.processEvents()

        QTest.keyClick(window.note_velocity_spin, Qt.Key.Key_Space)
        app.processEvents()
        assert window.playback_mode == "stopped"
    finally:
        window.close()
        app.processEvents()


def test_digits_do_not_switch_mode_from_a_spin_box():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, window = _window()
    try:
        _select_note(app, window)
        window.note_velocity_spin.setFocus()
        app.processEvents()

        QTest.keyClick(window.note_velocity_spin, Qt.Key.Key_3)
        app.processEvents()
        assert window.playback_mode == "stopped"
    finally:
        window.close()
        app.processEvents()


def test_digits_still_type_into_a_spin_box():
    """Guarding the shortcut must not swallow the keystroke itself."""

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, window = _window()
    try:
        _select_note(app, window)
        window.note_velocity_spin.setFocus()
        window.note_velocity_spin.selectAll()
        app.processEvents()

        QTest.keyClick(window.note_velocity_spin, Qt.Key.Key_3)
        app.processEvents()
        assert window.note_velocity_spin.value() == 3
    finally:
        window.close()
        app.processEvents()


def test_shortcut_fires_when_the_roll_has_focus():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, window = _window()
    try:
        window.piano_roll.setFocus()
        app.processEvents()

        QTest.keyClick(window.piano_roll, Qt.Key.Key_3)
        app.processEvents()
        assert window.playback_mode == "both"
    finally:
        window.close()
        app.processEvents()


def test_all_transport_shortcuts_are_registered():
    from PySide6.QtGui import QKeySequence, QShortcut

    app, window = _window()
    try:
        bound = {shortcut.key().toString() for shortcut in window.findChildren(QShortcut)}
        for sequence in ("Space", "1", "2", "3", "0", "Esc"):
            assert QKeySequence(sequence).toString() in bound, f"{sequence} not bound"
    finally:
        window.close()
        app.processEvents()
