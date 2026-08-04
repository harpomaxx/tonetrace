"""Audition a note in isolation when it is clicked (issue #67).

Clicking a note plays just that note through a dedicated player, so the
transcription can be judged by ear without starting playback or disturbing the
transport. Uses the built-in synth rather than slicing the rendered preview:
the preview is optional (TiMidity++ or a completed native render) and can be
stale mid-edit, while the synth is always available and is the same voice.
"""

from __future__ import annotations

import os
import wave

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("numpy")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _note(pitch, start, duration=0.4, velocity=90, bends=None):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch,
        start_seconds=start,
        duration_seconds=duration,
        velocity=velocity,
        pitch_bends=bends,
    )


# --- synth helper -----------------------------------------------------------


def test_render_note_writes_a_wav_of_the_expected_length(tmp_path):
    from notegrabber.native_synth import SAMPLE_RATE, render_note_to_wav

    out, error = render_note_to_wav(tmp_path / "n.wav", pitch=60, duration_seconds=0.5)

    assert error is None and out is not None
    with wave.open(str(out)) as handle:
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getnframes() == pytest.approx(0.5 * SAMPLE_RATE, rel=0.02)


def test_render_note_clamps_absurd_durations(tmp_path):
    """A very short note still needs to be audible; a long one must not hang."""

    from notegrabber.native_synth import SAMPLE_RATE, render_note_to_wav

    short, _ = render_note_to_wav(tmp_path / "short.wav", pitch=60, duration_seconds=0.001)
    long_note, _ = render_note_to_wav(tmp_path / "long.wav", pitch=60, duration_seconds=30.0)

    with wave.open(str(short)) as handle:
        assert handle.getnframes() >= 0.05 * SAMPLE_RATE
    with wave.open(str(long_note)) as handle:
        assert handle.getnframes() <= 2.5 * SAMPLE_RATE


def test_render_note_accepts_a_pitch_bend(tmp_path):
    from notegrabber.native_synth import render_note_to_wav

    out, error = render_note_to_wav(
        tmp_path / "bend.wav",
        pitch=60,
        duration_seconds=0.4,
        bend_semitones=((0.0, 0.0), (0.2, 1.0)),
    )
    assert error is None and out is not None and out.exists()


def test_render_note_is_not_silent(tmp_path):
    import numpy as np

    from notegrabber.native_synth import render_note_to_wav

    out, _ = render_note_to_wav(tmp_path / "n.wav", pitch=69, duration_seconds=0.3)
    with wave.open(str(out)) as handle:
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    assert np.max(np.abs(data)) > 1000, "audition tone should be clearly audible"


def test_render_note_clamps_out_of_range_pitch_and_velocity(tmp_path):
    from notegrabber.native_synth import render_note_to_wav

    out, error = render_note_to_wav(
        tmp_path / "n.wav", pitch=200, duration_seconds=0.2, velocity=999
    )
    assert error is None and out is not None and out.exists()


# --- window wiring ----------------------------------------------------------


def _window(notes=None):
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
    if notes is None:
        notes = [_note(60, 2.0), _note(67, 3.0, duration=0.9, velocity=120)]
    window.state.heatmap = heatmap
    window.state.extracted_notes = notes
    window.edit_history.begin(notes)
    window._set_display_notes(notes)
    app.processEvents()
    return app, window


def _audition_files(window):
    if window._audition_dir is None:
        return []
    return sorted(path.name for path in window._audition_dir.glob("note-*.wav"))


def test_audition_is_on_by_default():
    app, window = _window()
    try:
        assert window.audition_enabled is True
        assert window.controls.audition_on_select.isChecked() is True
    finally:
        window.close()
        app.processEvents()


def test_selecting_a_note_renders_and_plays_it():
    app, window = _window()
    try:
        window._audition_selected_index(0)
        app.processEvents()

        assert _audition_files(window), "an audition WAV should have been written"
        assert window.audition_player.source().fileName().startswith("note-")
    finally:
        window.close()
        app.processEvents()


def test_audition_uses_the_selected_note_duration():
    app, window = _window()
    try:
        from notegrabber.native_synth import SAMPLE_RATE

        # Index 1 is the 0.9s note.
        window._audition_selected_index(1)
        app.processEvents()

        path = next(iter(window._audition_dir.glob("note-*.wav")))
        with wave.open(str(path)) as handle:
            assert handle.getnframes() == pytest.approx(0.9 * SAMPLE_RATE, rel=0.05)
    finally:
        window.close()
        app.processEvents()


def test_audition_does_not_disturb_the_transport():
    """The whole point of a separate player: playback state stays put."""

    app, window = _window()
    try:
        before_midi = window.midi_player.playbackState()
        before_original = window.original_player.playbackState()

        window._audition_selected_index(0)
        app.processEvents()

        assert window.midi_player.playbackState() == before_midi
        assert window.original_player.playbackState() == before_original
        assert window.midi_player.position() == 0
        assert window.original_player.position() == 0
    finally:
        window.close()
        app.processEvents()


def test_toggling_audition_off_suppresses_it():
    app, window = _window()
    try:
        window.set_audition_enabled(False)
        window._audition_selected_index(0)
        app.processEvents()

        assert _audition_files(window) == []
    finally:
        window.close()
        app.processEvents()


def test_the_checkbox_drives_the_toggle():
    app, window = _window()
    try:
        window.controls.audition_on_select.setChecked(False)
        app.processEvents()
        assert window.audition_enabled is False

        window.controls.audition_on_select.setChecked(True)
        app.processEvents()
        assert window.audition_enabled is True
    finally:
        window.close()
        app.processEvents()


def test_audition_is_suppressed_once_a_drag_actually_moves():
    """A moving drag emits an edit per tick; auditioning would retrigger endlessly."""

    app, window = _window()
    try:
        window.piano_roll.drag_mode = "move"
        window.piano_roll.drag_has_moved = True
        window._audition_selected_index(0)
        app.processEvents()

        assert _audition_files(window) == []
    finally:
        window.piano_roll.drag_mode = None
        window.piano_roll.drag_has_moved = False
        window.close()
        app.processEvents()


def test_a_plain_click_still_auditions_though_it_arms_a_drag():
    """mousePressEvent sets drag_mode before emitting, so the guard must not
    key off drag_mode -- that would suppress every click."""

    app, window = _window()
    try:
        roll = window.piano_roll
        roll.drag_mode = "move"  # armed by the press, but nothing has moved yet
        roll.drag_has_moved = False
        window._audition_selected_index(0)
        app.processEvents()

        assert _audition_files(window), "an armed-but-unmoved drag must still audition"
    finally:
        window.piano_roll.drag_mode = None
        window.close()
        app.processEvents()


def test_each_audition_uses_a_fresh_file_and_prunes_the_old_one():
    """QMediaPlayer caches by URL, so reusing one path can replay stale audio."""

    app, window = _window()
    try:
        window._audition_selected_index(0)
        app.processEvents()
        first = _audition_files(window)

        window._audition_selected_index(1)
        app.processEvents()
        second = _audition_files(window)

        assert first and second and first != second
        assert len(second) == 1, "previous audition files should be pruned"
    finally:
        window.close()
        app.processEvents()


def test_out_of_range_index_is_ignored():
    app, window = _window()
    try:
        window._audition_selected_index(99)
        app.processEvents()
        assert _audition_files(window) == []
    finally:
        window.close()
        app.processEvents()


def test_audition_without_a_heatmap_is_a_no_op():
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.show()
    app.processEvents()
    try:
        window._audition_note(_note(60, 0.0))
        assert window._audition_dir is None
    finally:
        window.close()
        app.processEvents()


def test_clicking_a_note_in_the_roll_triggers_audition():
    """End to end through the real click path, not just the handler."""

    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    app, window = _window()
    try:
        roll = window.piano_roll
        rect = roll._note_rect(window.state.current_notes[0])
        roll.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(rect.center().x(), rect.center().y()),
                QPointF(rect.center().x(), rect.center().y()),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        app.processEvents()

        assert _audition_files(window), "clicking a note should audition it"
    finally:
        window.close()
        app.processEvents()


def test_closing_the_window_removes_the_audition_directory():
    app, window = _window()
    try:
        window._audition_selected_index(0)
        app.processEvents()
        directory = window._audition_dir
        assert directory is not None and directory.exists()
    finally:
        window.close()
        app.processEvents()

    assert not directory.exists(), "audition temp dir must not outlive the window"
