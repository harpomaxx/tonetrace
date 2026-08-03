"""Smoke tests for the optional PySide6 standalone GUI."""

from __future__ import annotations

import os

import pytest

from notegrabber.gui.app import build_parser


def test_gui_launcher_parser_documents_backend_without_qt() -> None:
    parser = build_parser()
    help_text = parser.format_help().lower()

    assert "notegrabber-gui" in help_text
    assert "--backend" in help_text
    assert "basic-pitch" in help_text
    assert "--no-render-midi" in help_text


@pytest.mark.gui
def test_play_start_position_rewinds_when_reference_player_is_at_end() -> None:
    pytest.importorskip("PySide6")

    from notegrabber.gui.main_window import MainWindow

    assert MainWindow._play_start_position(9_950, 10_000) == 0
    assert MainWindow._play_start_position(5_000, 10_000) == 5_000
    assert MainWindow._play_start_position(-10, 10_000) == 0


@pytest.mark.gui
def test_pending_range_stops_playback_before_analysis_offscreen() -> None:
    """Playback must honor the selected range end even before Analyze is run."""

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.waveform.set_preview([0.0, 0.1], sample_rate=1, duration_seconds=120.0)

    # No analysis yet.
    assert window.state.analysis_duration_seconds is None
    assert window._active_analysis_range() is None

    # Select a range in the controls (as dragging the waveform would).
    window.controls.set_analysis_range(30.0, 20.0)  # 30s..50s
    assert window._active_analysis_range() == pytest.approx((30.0, 50.0))

    # Simulate original-audio playback reaching the range end.
    window.playback_mode = "original"
    window._start_playback_clock(30.0)
    window.playback_timer.start()
    assert window._stop_if_past_analysis_end(49.0) is False  # still inside range
    assert window._stop_if_past_analysis_end(50.5) is True  # past the end -> paused
    assert window.playback_mode == "paused"
    assert window.playback_timer.isActive() is False

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_play_both_ducks_original_below_midi_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    # Both mode: original is ducked and stays below the MIDI level.
    window._apply_playback_mix("both")
    assert window.original_audio.volume() == pytest.approx(MainWindow.BOTH_ORIGINAL_VOLUME)
    assert window.midi_audio.volume() == pytest.approx(MainWindow.BOTH_MIDI_VOLUME)
    assert window.original_audio.volume() < window.midi_audio.volume()

    # Solo modes restore the normal level on both players.
    window._apply_playback_mix("original")
    assert window.original_audio.volume() == pytest.approx(MainWindow.SOLO_VOLUME)
    assert window.midi_audio.volume() == pytest.approx(MainWindow.SOLO_VOLUME)

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_playback_sync_helpers_keep_waveform_and_heatmap_playheads_together() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.waveform.set_preview([0.0, 0.1], sample_rate=1, duration_seconds=10.0)

    assert window.playback_timer.interval() == 16
    assert MainWindow._interpolated_display_seconds(10.0, 250) == pytest.approx(10.25)
    assert MainWindow._position_needs_sync(1_000, 1_050) is False
    assert MainWindow._position_needs_sync(1_000, 1_250) is True

    window._set_playhead(3.25)
    assert window.waveform.playhead_seconds == pytest.approx(3.25)
    assert window.piano_roll.playhead_seconds == pytest.approx(3.25)
    assert window.waveform._playhead_update_rect(3.25).width() == 7
    assert window.piano_roll._playhead_update_rect(3.25).width() == 7

    window._set_playhead(99.0)
    assert window.waveform.playhead_seconds == pytest.approx(10.0)
    assert window.piano_roll.playhead_seconds == pytest.approx(10.0)
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_resync_ignores_backend_lag_but_chases_forward_jumps() -> None:
    pytest.importorskip("PySide6")

    from notegrabber.gui.main_window import MainWindow

    # Player position lagging behind the interpolated estimate (drift < 0) is
    # ordinary buffered-backend lag and must never pull the playhead backward.
    small_lag = -(MainWindow.PLAYBACK_RESYNC_MAX_LAG_SECONDS / 2)
    assert MainWindow._resync_drift_needs_correction(small_lag) is False
    assert MainWindow._resync_drift_needs_correction(-0.0) is False

    # A player that has jumped ahead of us (drift > 0) past the ahead tolerance,
    # or lag so large it cannot be ordinary buffering, is a real desync to chase.
    ahead = MainWindow.PLAYBACK_RESYNC_AHEAD_TOLERANCE_SECONDS + 0.01
    assert MainWindow._resync_drift_needs_correction(ahead) is True
    huge_lag = -(MainWindow.PLAYBACK_RESYNC_MAX_LAG_SECONDS + 0.01)
    assert MainWindow._resync_drift_needs_correction(huge_lag) is True

    # A tiny forward drift within the ahead tolerance stays smooth (no snap).
    tiny_ahead = MainWindow.PLAYBACK_RESYNC_AHEAD_TOLERANCE_SECONDS / 2
    assert MainWindow._resync_drift_needs_correction(tiny_ahead) is False


@pytest.mark.gui
def test_maybe_resync_does_not_snap_playhead_backward_on_position_lag() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    window.playback_mode = "original"
    window._start_playback_clock(5.0)

    # Interpolated estimate has advanced to 5.30s; the backend still reports a
    # lagging 5.10s. The estimate must be kept, not snapped back to 5.10s.
    window._reference_player_display_seconds = lambda: 5.10  # type: ignore[method-assign]
    resynced = window._maybe_resync_playback_clock(5.30)
    assert resynced == pytest.approx(5.30)
    assert window.playback_clock_anchor_seconds == pytest.approx(5.0)

    # A real forward jump (seek) to 8.0s is chased.
    window._reference_player_display_seconds = lambda: 8.0  # type: ignore[method-assign]
    resynced = window._maybe_resync_playback_clock(5.30)
    assert resynced == pytest.approx(8.0)
    assert window.playback_clock_anchor_seconds == pytest.approx(8.0)

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_sync_playback_tick_resyncs_after_configured_tick_count() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    window.playback_mode = "original"
    window._start_playback_clock(0.0)
    window.playback_clock_anchor_seconds = 999.0
    window._reference_player_display_seconds = lambda: 0.0  # type: ignore[method-assign]

    for _ in range(window.PLAYBACK_RESYNC_TICKS - 1):
        window._sync_playback_tick()
    assert window.playback_sync_ticks == window.PLAYBACK_RESYNC_TICKS - 1
    assert window.playback_clock_anchor_seconds == pytest.approx(999.0)

    window._sync_playback_tick()
    assert window.playback_sync_ticks == 0
    assert window.playback_clock_anchor_seconds == pytest.approx(0.0, abs=1e-6)

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_media_status_changed_freezes_and_resumes_the_interpolated_clock() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from PySide6.QtMultimedia import QMediaPlayer
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    window.playback_mode = "original"
    window.playback_timer.start()
    window._start_playback_clock(2.0)
    assert window.playback_stalled is False

    window.playback_stalled = True
    anchor_before = window.playback_clock_anchor_seconds
    window._sync_playback_tick()
    assert window.playback_clock_anchor_seconds == pytest.approx(anchor_before)

    window._media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
    assert window.playback_stalled is False

    window.playback_timer.stop()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_range_midi_preview_maps_local_time_to_full_song_timeline() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.waveform.set_preview([0.0, 0.1], sample_rate=1, duration_seconds=60.0)
    window.state.analysis_start_seconds = 12.0
    window.state.analysis_duration_seconds = 9.47
    window.state.midi_preview_offset_seconds = 12.0

    assert window._display_seconds_from_midi_position(3_000) == pytest.approx(15.0)
    assert window._display_seconds_from_midi_position(11_000) == pytest.approx(21.47)
    assert window._midi_position_from_display_seconds(15.0) == 3_000
    assert window._midi_position_from_display_seconds(99.0) == 9_470

    preview_notes = window._notes_for_midi_preview(
        [
            GuiMidiNote(pitch=60, start_seconds=10.0, duration_seconds=3.0, velocity=80),
            GuiMidiNote(pitch=64, start_seconds=14.0, duration_seconds=1.5, velocity=90),
            GuiMidiNote(pitch=67, start_seconds=21.0, duration_seconds=3.0, velocity=100),
            GuiMidiNote(pitch=72, start_seconds=30.0, duration_seconds=1.0, velocity=70),
        ]
    )

    assert [note.pitch for note in preview_notes] == [60, 64, 67]
    assert [note.start_seconds for note in preview_notes] == pytest.approx([0.0, 2.0, 9.0])
    assert [note.duration_seconds for note in preview_notes] == pytest.approx([1.0, 1.5, 0.47])
    assert [note.velocity for note in preview_notes] == [80, 90, 100]
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_selected_note_summary_highlights_note_name() -> None:
    pytest.importorskip("PySide6")

    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiMidiNote

    summary = MainWindow._note_summary(GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90))

    assert "C4" in summary
    assert "font-weight:900" in summary
    assert "MIDI 60" in summary


@pytest.mark.gui
def test_main_window_constructs_offscreen_when_pyside6_is_installed() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    assert window.windowTitle() == "ToneTrace"
    assert window.controls.backend() == "basic-pitch"
    assert window.original_player.audioOutput() is window.original_audio
    assert window.midi_player.audioOutput() is window.midi_audio
    assert window.piano_scroll.maximumHeight() <= round(window.height() * 0.72) + 1
    # The detected-notes table now lives in a collapsible section under a vertical
    # splitter, collapsed by default so the piano roll owns the height.
    assert not window.sequence_section.is_expanded()
    assert not window.transport.play_both.isEnabled()
    window.close()
    app.processEvents()


def test_window_fits_laptop_height_and_status_is_visible() -> None:
    # Regression: the left control column used to force the window minimum height
    # past a laptop screen (~768 px), pushing the status line off-screen. The
    # window must fit within a laptop height, and the single status line must live
    # in the always-visible top block (not the hidden QMainWindow status bar).
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)

    assert window.minimumSizeHint().height() <= 768
    window.resize(1366, 768)
    window.show()
    app.processEvents()
    assert window.height() <= 768  # not forced taller than the screen

    # The single status home is the top-block strip; the status bar is hidden.
    assert not window.statusBar().isVisible()
    window._set_status("hello status")
    assert window.transport.status_label.text() == "hello status"
    strip = window.transport.status_label
    y = strip.mapTo(window, strip.rect().topLeft()).y()
    assert 0 <= y < window.height()  # on screen

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_transcription_controls_explain_sliders_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.widgets.controls import AnalysisControls

    app = QApplication.instance() or QApplication([])
    controls = AnalysisControls()

    assert "frame confidence" in controls.note_sensitivity.toolTip()
    assert "attack" in controls.split_sensitivity.toolTip()
    assert "CQT activation threshold" in controls.cqt_threshold.toolTip()
    assert "Minimum note length" in controls.min_duration.toolTip()
    assert not hasattr(controls, "heatmap_zoom")
    assert not hasattr(controls, "heatmap_vertical_zoom")
    assert controls.analysis_range() == (0.0, None)
    controls.range_enabled.setChecked(True)
    controls.range_start.setValue(12.0)
    controls.range_duration.setValue(30.0)
    assert controls.analysis_range() == (12.0, 30.0)
    controls.resize(314, 760)
    controls.show()
    app.processEvents()
    assert controls.open_button.geometry().bottom() < controls.height()
    assert controls.export_button.geometry().bottom() < controls.height()


@pytest.mark.gui
def test_transcription_knobs_expose_slider_api_and_feed_accessors_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.widgets.controls import AnalysisControls
    from notegrabber.gui.widgets.knob import KnobWidget

    app = QApplication.instance() or QApplication([])
    controls = AnalysisControls()

    # The four transcription controls are now knobs with the slider-compatible API.
    for knob in (controls.note_sensitivity, controls.split_sensitivity, controls.cqt_threshold, controls.min_duration):
        assert isinstance(knob, KnobWidget)

    # setValue/value round-trips and feeds the analyzer accessors.
    controls.note_sensitivity.setValue(80)
    controls.split_sensitivity.setValue(40)
    controls.cqt_threshold.setValue(25)
    controls.min_duration.setValue(120)
    assert controls.frame_threshold() == pytest.approx(0.80)
    assert controls.onset_threshold() == pytest.approx(0.40)
    assert controls.threshold() == pytest.approx(0.25)
    assert controls.min_duration_seconds() == pytest.approx(0.120)

    # retune fires on committed edits (editingFinished), not on every value
    # change, so dragging a knob does not retune per intermediate value.
    fired = []
    controls.retune_requested.connect(lambda: fired.append(True))
    controls.note_sensitivity.setValue(81)
    assert not fired  # setValue alone must not retune
    controls.note_sensitivity.editingFinished.emit()
    assert fired

    # Clamping and the knob-specific default/reset behaviour.
    knob = KnobWidget(0, 100, 30, default=30)
    knob.setValue(999)
    assert knob.value() == 100
    knob.setValue(-5)
    assert knob.value() == 0
    # Double-click resets to the configured default.
    knob.setValue(70)
    knob.setValue(knob._default)
    assert knob.value() == 30


@pytest.mark.gui
def test_knob_editing_finished_fires_on_commit_only_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.widgets.knob import KnobWidget

    app = QApplication.instance() or QApplication([])
    knob = KnobWidget(0, 100, 30, default=30)

    committed = []
    changed = []
    knob.editingFinished.connect(lambda: committed.append(True))
    knob.valueChanged.connect(lambda v: changed.append(v))

    # Programmatic setValue emits valueChanged (live label) but never commits.
    knob.setValue(50)
    assert changed == [50]
    assert committed == []

    # A wheel step changes the value and commits immediately (no release event).
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    wheel = QWheelEvent(
        QPointF(20.0, 20.0),
        QPointF(20.0, 20.0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    knob.wheelEvent(wheel)
    assert committed == [True]  # exactly one commit from the wheel step

    app.processEvents()


@pytest.mark.gui
def test_waveform_widget_selection_handles_refine_range_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.widgets.waveform import WaveformWidget

    app = QApplication.instance() or QApplication([])
    widget = WaveformWidget()
    widget.left_gutter = 0  # isolate the seconds<->x math from the timeline gutter
    widget.resize(100, 90)
    widget.set_preview([0.0, 0.5, -0.5], sample_rate=3, duration_seconds=20.0)
    widget.set_selection(5.0, 10.0)

    assert widget._selection_pixel_bounds() == (25.0, 75.0)
    assert widget._selection_hit_at(25.0) == "resize_start"
    assert widget._selection_hit_at(75.0) == "resize_end"
    assert widget._selection_hit_at(50.0) == "move"

    widget._selection_drag_mode = "resize_start"
    widget._drag_start_x = 25.0
    widget._drag_original_start_seconds = 5.0
    widget._drag_original_duration_seconds = 10.0
    widget._drag_existing_selection(35.0)
    assert widget.selection_start_seconds == pytest.approx(7.0)
    assert widget.selection_duration_seconds == pytest.approx(8.0)

    widget._update_hover_cursor(35.0)
    assert widget.cursor().shape() == Qt.CursorShape.SizeHorCursor
    app.processEvents()


@pytest.mark.gui
def test_ranged_analysis_playheads_and_heatmap_share_timeline_offscreen() -> None:
    """With a non-zero range start, waveform + heatmap map the same second to the
    same screen fraction, and the heatmap frames align with the MIDI notes."""

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1400, 820)
    window.show()
    app.processEvents()
    window.waveform.set_preview([0.0] * 100, sample_rate=1, duration_seconds=210.0)
    app.processEvents()

    # Waveform gutter must mirror the piano roll keyboard for a shared mapping.
    assert window.waveform.left_gutter == window.piano_roll.keyboard_width

    start = 40.87
    duration = 30.0
    # Frame times offset onto the full-song timeline, as the analysis worker does.
    frame_times = [start + i * 0.05 for i in range(int(duration / 0.05))]
    midi_notes = list(range(21, 109))
    activations = [[0.0] * len(midi_notes) for _ in frame_times]
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frame_times,
        activations=activations,
        sample_rate=22_050,
        hop_size=512,
        window_size=2_048,
    )
    window.state.heatmap = heatmap
    window.state.analysis_start_seconds = start
    window.state.analysis_duration_seconds = duration
    window.state.midi_preview_offset_seconds = start
    window.state.extracted_notes = [GuiMidiNote(pitch=60, start_seconds=start, duration_seconds=0.5, velocity=90)]
    window._set_display_notes(window.state.extracted_notes)
    app.processEvents()

    pr = window.piano_roll
    wf = window.waveform

    def pr_fraction(seconds: float) -> float:
        return (pr.x_for_seconds(seconds) - pr.keyboard_width) / max(1, pr.width() - pr.keyboard_width)

    def wf_fraction(seconds: float) -> float:
        return (wf._x_for_seconds(seconds) - wf.left_gutter) / wf._time_width()

    # Same second -> same fraction of the shared time area (both widgets at fit).
    # A tiny residual (~1%) remains from the piano roll's trailing canvas pad;
    # what matters is that the gross keyboard-gutter offset no longer shifts the
    # two timelines apart (that was ~13% / 137px before the fix).
    for seconds in (start, start + duration / 2, start + duration):
        assert pr_fraction(seconds) == pytest.approx(wf_fraction(seconds), abs=0.02)

    # The heatmap's first frame draws at the same x as a note at the range start,
    # i.e. the heatmap is not shifted left relative to the notes.
    assert pr.x_for_seconds(heatmap.frame_times[0]) == pr.x_for_seconds(start)

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_waveform_widget_range_selection_math_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.widgets.waveform import WaveformWidget

    app = QApplication.instance() or QApplication([])
    widget = WaveformWidget()
    widget.left_gutter = 0  # isolate the seconds<->x math from the timeline gutter
    widget.resize(100, 90)
    widget.set_preview([0.0, 0.5, -0.5], sample_rate=3, duration_seconds=20.0)

    start, duration = widget._selection_from_pixels(25, 75)
    widget.set_selection(start, duration)

    assert start == pytest.approx(5.0)
    assert duration == pytest.approx(10.0)
    assert widget.selection_start_seconds == pytest.approx(5.0)
    assert widget.selection_duration_seconds == pytest.approx(10.0)
    app.processEvents()


@pytest.mark.gui
def test_main_window_waveform_range_selection_updates_controls_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.waveform.set_preview([0.0, 0.5, -0.5], sample_rate=3, duration_seconds=20.0)

    window._set_analysis_range_from_waveform(4.0, 8.0)

    assert window.controls.analysis_range() == (4.0, 8.0)
    assert window.waveform.selection_start_seconds == pytest.approx(4.0)
    assert window.waveform.selection_duration_seconds == pytest.approx(8.0)
    assert "Selected range" in window.statusBar().currentMessage()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_waveform_widget_falls_back_for_non_wav_audio_offscreen(monkeypatch, tmp_path) -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    import notegrabber.gui.widgets.waveform as waveform_module
    from notegrabber.gui.widgets.waveform import WaveformWidget, downsample_waveform_preview

    def fake_read_wav(_path):
        raise wave_error

    def fake_load_with_librosa(_path):
        return [0.0, 0.5, -0.5, 0.25], 4000, 12.5

    wave_error = ValueError("not a wav")
    monkeypatch.setattr(waveform_module, "read_wav", fake_read_wav)
    monkeypatch.setattr(WaveformWidget, "_load_with_librosa", staticmethod(fake_load_with_librosa))

    app = QApplication.instance() or QApplication([])
    widget = WaveformWidget()
    widget.load_audio(tmp_path / "song.mp3")

    assert widget.samples == [0.0, 0.5, -0.5, 0.25]
    assert widget.duration_seconds() == pytest.approx(12.5)
    assert len(downsample_waveform_preview(range(100), max_samples=10)) == 10
    app.processEvents()


@pytest.mark.gui
def test_waveform_widget_paints_pitch_overview_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.overview import PitchOverview
    from notegrabber.gui.widgets.waveform import WaveformWidget

    app = QApplication.instance() or QApplication([])
    widget = WaveformWidget()
    widget.resize(320, 120)
    widget.set_preview([0.0, 0.5, -0.5, 0.25], sample_rate=4, duration_seconds=4.0)
    widget.set_pitch_overview(
        PitchOverview(
            frame_times=[0.0, 1.0, 2.0],
            midi_notes=[60, 61, 62],
            activations=[[0.0, 0.5, 0.9], [0.2, 0.4, 0.1], [0.8, 0.0, 0.3]],
            duration_seconds=4.0,
        )
    )

    pixmap = QPixmap(widget.size())
    painter = QPainter(pixmap)
    try:
        widget._draw_pitch_overview(painter, widget.width(), widget.height(), widget._overview_height(widget.height()))
    finally:
        painter.end()
    app.processEvents()


@pytest.mark.gui
def test_main_window_long_audio_defaults_to_range_analysis_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.waveform.set_preview([0.0, 0.1], sample_rate=1, duration_seconds=240.0)

    assert window._maybe_enable_default_range_for_long_audio(240.0)
    assert window.controls.analysis_range() == (0.0, 30.0)
    assert window.waveform.selection_start_seconds == pytest.approx(0.0)
    assert window.waveform.selection_duration_seconds == pytest.approx(30.0)
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_widget_paints_gui_heatmap_model_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    widget.resize(320, 180)
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[68, 69, 70],
        frame_times=[0.0, 0.1, 0.2],
        activations=[[0.1, 0.9, 0.2], [0.0, 0.8, 0.1], [0.0, 0.1, 0.0]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    widget.set_data(heatmap, [GuiMidiNote(pitch=69, start_seconds=0.0, duration_seconds=0.2, velocity=100)])
    widget.set_selected_note_index(0)
    widget.set_playhead(0.1)

    pixmap = QPixmap(widget.size())
    painter = QPainter(pixmap)
    try:
        widget._draw_keyboard(painter)
        widget._draw_heatmap(painter)
        widget._draw_grid(painter)
        widget._draw_notes(painter)
        widget._draw_playhead(painter)
    finally:
        painter.end()
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_horizontal_zoom_expands_timeline_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    widget.resize(320, 180)
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61],
        frame_times=[0.0, 10.0],
        activations=[[0.0, 0.0], [0.0, 0.0]],
        sample_rate=1,
        hop_size=1,
        window_size=1,
    )
    widget.set_data(heatmap, [])
    fit_width = widget.minimumWidth()
    fit_seconds_per_pixel = widget.seconds_per_pixel

    widget.set_horizontal_zoom(4.0)
    zoomed_width = widget.width()

    assert widget.horizontal_zoom == pytest.approx(4.0)
    assert widget.seconds_per_pixel == pytest.approx(fit_seconds_per_pixel / 4.0)
    assert widget.minimumWidth() > fit_width
    widget.zoom_by_wheel_delta(120)
    assert widget.horizontal_zoom == pytest.approx(4.8)
    assert widget.width() > zoomed_width
    widget.zoom_by_wheel_delta(-120)
    assert widget.horizontal_zoom == pytest.approx(4.0)
    assert widget.width() == zoomed_width
    widget.set_horizontal_zoom(1.0)
    assert widget.width() == fit_width
    app.processEvents()


@pytest.mark.gui
def test_playhead_follow_scroll_keeps_playhead_visible_when_zoomed_offscreen() -> None:
    """When zoomed in past the viewport, the scroll area follows the playhead."""

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.resize(1200, 800)
    window.show()
    app.processEvents()
    window.waveform.set_preview([0.0, 0.1], sample_rate=1, duration_seconds=60.0)

    frame_times = [i * 0.05 for i in range(1200)]
    midi_notes = list(range(21, 109))
    heatmap = GuiHeatmap(
        backend="cqt",
        midi_notes=midi_notes,
        frame_times=frame_times,
        activations=[[0.0] * len(midi_notes) for _ in frame_times],
        sample_rate=22_050,
        hop_size=512,
        window_size=2_048,
    )
    window.state.heatmap = heatmap
    window._set_display_notes([])
    window.piano_roll.set_horizontal_zoom(16.0)
    app.processEvents()

    scrollbar = window.piano_scroll.horizontalScrollBar()
    assert scrollbar.maximum() > 0  # canvas is wider than the viewport

    # Not playing: following must be a no-op so manual scrolling is respected.
    scrollbar.setValue(0)
    window._set_playhead(30.0)
    assert scrollbar.value() == 0

    # Playing: advancing the playhead keeps it inside the viewport, and the
    # scrollbar updates in jumps rather than on every single tick.
    window.playback_mode = "original"
    window.playback_timer.start()
    scroll_updates = 0
    previous = scrollbar.value()
    visible_ticks = 0
    ticks = [i * 0.2 for i in range(300)]
    for seconds in ticks:
        window._set_playhead(seconds)
        x = window.piano_roll.x_for_seconds(seconds)
        viewport_width = window.piano_scroll.viewport().width()
        if scrollbar.value() + window.piano_roll.keyboard_width <= x <= scrollbar.value() + viewport_width:
            visible_ticks += 1
        if scrollbar.value() != previous:
            scroll_updates += 1
            previous = scrollbar.value()
    window.playback_timer.stop()

    assert visible_ticks == len(ticks)
    assert scroll_updates < len(ticks) // 4  # re-anchors in jumps, not per frame

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_full_song_timeline_keeps_canvas_bounded_for_huge_files_offscreen() -> None:
    """A short range analysed inside a very long file must not create a giant canvas.

    Fitting to the full-song duration (not the analysed range) keeps the canvas
    roughly viewport-sized at zoom 1.0 regardless of file length, while still
    placing the range on the same time->x scale as the waveform overview.
    """

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication, QScrollArea
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    scroll = QScrollArea()
    widget = PianoRollWidget()
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    scroll.resize(1000, 500)
    scroll.show()
    app.processEvents()

    # 10 s of heatmap frames from a 1-hour recording.
    frame_times = [i * 0.05 for i in range(200)]
    midi_notes = list(range(21, 109))
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=midi_notes,
        frame_times=frame_times,
        activations=[[0.0] * len(midi_notes) for _ in frame_times],
        sample_rate=22_050,
        hop_size=512,
        window_size=2_048,
    )
    notes = [GuiMidiNote(pitch=60, start_seconds=2.0, duration_seconds=0.5, velocity=90)]

    full_song_seconds = 3_600.0
    widget.set_data(heatmap, notes, full_duration_seconds=full_song_seconds)

    assert widget._timeline_duration_seconds() == pytest.approx(full_song_seconds)
    # Canvas at fit stays near the viewport width, not hundreds of viewports wide.
    fit_available = widget._fit_available_width()
    assert widget.width() <= fit_available + widget.keyboard_width + 200
    # Even fully zoomed in the width is bounded by the 32x zoom cap, not duration.
    widget.set_horizontal_zoom(32.0)
    assert widget.width() <= 33 * (fit_available + widget.keyboard_width + 200)

    # Whole-file analysis (full duration == heatmap duration) is unchanged.
    widget.set_horizontal_zoom(1.0)
    widget.set_data(heatmap, notes, full_duration_seconds=heatmap.duration_seconds)
    assert widget._timeline_duration_seconds() == pytest.approx(heatmap.duration_seconds)
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_note_updates_do_not_compound_zoom_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    widget.resize(320, 180)
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61],
        frame_times=[0.0, 10.0],
        activations=[[0.0, 0.0], [0.0, 0.0]],
        sample_rate=1,
        hop_size=1,
        window_size=1,
    )
    widget.set_data(heatmap, [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=1.0, velocity=80)])
    widget.set_horizontal_zoom(4.0)
    fit_seconds_per_pixel = widget.fit_seconds_per_pixel
    seconds_per_pixel = widget.seconds_per_pixel
    width = widget.width()

    widget.set_data(heatmap, [GuiMidiNote(pitch=60, start_seconds=1.0, duration_seconds=1.0, velocity=80)])

    assert widget.fit_seconds_per_pixel == pytest.approx(fit_seconds_per_pixel)
    assert widget.seconds_per_pixel == pytest.approx(seconds_per_pixel)
    assert widget.horizontal_zoom == pytest.approx(4.0)
    assert widget.width() == width
    widget.set_horizontal_zoom(1.0)
    assert widget.seconds_per_pixel == pytest.approx(fit_seconds_per_pixel)
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_vertical_zoom_expands_pitch_rows_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    widget.resize(320, 180)
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=list(range(36, 84)),
        frame_times=[0.0, 10.0],
        activations=[[0.0] * 48, [0.0] * 48],
        sample_rate=1,
        hop_size=1,
        window_size=1,
    )
    widget.set_data(heatmap, [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=1.0, velocity=80)])
    original_height = widget.minimumHeight()
    original_seconds_per_pixel = widget.seconds_per_pixel

    widget.set_vertical_zoom(3.0)

    assert widget.vertical_zoom == pytest.approx(3.0)
    assert widget.note_height == 21
    assert widget.minimumHeight() > original_height
    assert widget.seconds_per_pixel == pytest.approx(original_seconds_per_pixel)
    widget.vertical_zoom_by_wheel_delta(-120)
    assert widget.vertical_zoom == pytest.approx(2.5)
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_mouse_wheel_zoom_works_without_left_panel_sliders_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61],
        frame_times=[0.0, 10.0],
        activations=[[0.0, 0.0], [0.0, 0.0]],
        sample_rate=1,
        hop_size=1,
        window_size=1,
    )
    window._set_display_notes([])

    window.piano_roll.zoom_by_wheel_delta(120)
    window.piano_roll.vertical_zoom_by_wheel_delta(120)

    assert window.piano_roll.horizontal_zoom == pytest.approx(1.2)
    assert window.piano_roll.vertical_zoom == pytest.approx(1.2)
    assert not hasattr(window.controls, "heatmap_zoom")
    assert not hasattr(window.controls, "heatmap_vertical_zoom")
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_large_heatmap_paint_aggregates_visible_columns_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    widget.resize(320, 180)
    frame_count = 10_000
    note_count = 12
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=list(range(60, 60 + note_count)),
        frame_times=[index / 86.0 for index in range(frame_count)],
        activations=[[0.5] * note_count for _index in range(frame_count)],
        sample_rate=86,
        hop_size=1,
        window_size=1,
    )
    calls = 0

    def counted_activation(frame_index, note_index):
        nonlocal calls
        calls += 1
        return heatmap.activations[frame_index][note_index]

    object.__setattr__(heatmap, "activation", counted_activation)
    widget.set_data(heatmap, [])

    pixmap = QPixmap(widget.size())
    painter = QPainter(pixmap)
    try:
        widget._draw_heatmap(painter)
    finally:
        painter.end()

    # Bounded by visible pixels, never O(frames x notes). With numpy present the
    # vectorized path avoids the per-cell accessor entirely (calls == 0); the
    # pure-Python fallback still stays well under half the full cell count.
    assert calls < frame_count * note_count // 2
    if heatmap.activation_matrix() is not None:
        assert calls == 0
    app.processEvents()


@pytest.mark.gui
def test_heatmap_numpy_and_fallback_paths_draw_equivalently_offscreen() -> None:
    """The vectorized numpy paint matches the pure-Python fallback (±1 color LSB)."""

    pytest.importorskip("PySide6")
    np = pytest.importorskip("numpy")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import random

    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    random.seed(7)
    frame_times = [i * 0.05 for i in range(150)]
    midi_notes = list(range(48, 72))
    activations = [
        [round(random.random(), 3) if random.random() < 0.3 else 0.0 for _ in midi_notes]
        for _ in frame_times
    ]

    def render(force_fallback: bool, zoom: float) -> QImage:
        heatmap = GuiHeatmap(
            backend="cqt",
            midi_notes=midi_notes,
            frame_times=frame_times,
            activations=activations,
            sample_rate=10,
            hop_size=1,
            window_size=1,
        )
        widget = PianoRollWidget()
        widget.resize(1200, 400)
        widget.set_data(heatmap, [], full_duration_seconds=heatmap.duration_seconds)
        widget.set_horizontal_zoom(zoom)
        if force_fallback:
            object.__setattr__(heatmap, "activation_matrix", lambda: None)
        image = QImage(widget.width(), widget.height(), QImage.Format.Format_ARGB32)
        image.fill(0)
        widget.render(image)
        return image

    for zoom in (1.0, 8.0):  # exercises both the columns and frames paths
        numpy_image = render(False, zoom)
        fallback_image = render(True, zoom)
        a = np.frombuffer(bytes(numpy_image.constBits()), dtype=np.uint8).astype(np.int16)
        b = np.frombuffer(bytes(fallback_image.constBits()), dtype=np.uint8).astype(np.int16)
        assert a.shape == b.shape
        # Only sub-LSB float32-vs-float64 color rounding may differ, never a
        # missing/extra cell: every channel within 1, and almost all identical.
        max_channel_diff = int(np.abs(a - b).max())
        differing_fraction = float((a != b).mean())
        assert max_channel_diff <= 1
        assert differing_fraction < 0.01
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_hover_updates_cursor_for_move_and_resize_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    widget.resize(320, 180)
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[68, 69, 70],
        frame_times=[0.0, 0.1, 0.2],
        activations=[[0.1, 0.9, 0.2], [0.0, 0.8, 0.1], [0.0, 0.1, 0.0]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    note = GuiMidiNote(pitch=69, start_seconds=0.0, duration_seconds=0.2, velocity=100)
    widget.set_data(heatmap, [note])
    rect = widget._note_rect(note)
    assert rect is not None

    widget._update_hover_state(rect.center().x(), rect.center().y())
    assert widget.hover_note_index == 0
    assert widget.hover_mode == "move"
    assert widget.cursor().shape() == Qt.CursorShape.SizeAllCursor

    widget._update_hover_state(rect.left() + 1, rect.center().y())
    assert widget.hover_mode == "resize_start"
    assert widget.cursor().shape() == Qt.CursorShape.SizeHorCursor
    app.processEvents()


@pytest.mark.gui
def test_main_window_edit_selected_note_rerenders_midi_preview_offscreen(monkeypatch, tmp_path) -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    import notegrabber.gui.midi_preview_worker as preview_module
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    def fake_render_midi_to_wav(_midi_path, wav_path):
        wav_path.write_bytes(b"edited midi preview")
        return wav_path, None

    monkeypatch.setattr(preview_module, "render_midi_to_wav", fake_render_midi_to_wav)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=True)
    window.state.audio_path = tmp_path / "original.wav"
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.state.extracted_notes = [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90)]
    window._set_display_notes(window.state.extracted_notes)
    window._select_note(0)
    window.note_pitch_spin.setValue(64)

    window._apply_selected_note_edit()

    # Rendering is debounced and off-thread; flush it synchronously for the test.
    assert "updating" in window.statusBar().currentMessage()
    window._flush_preview_render()

    assert window.state.rendered_midi_wav is not None
    assert window.state.rendered_midi_wav.read_bytes() == b"edited midi preview"
    assert "preview re-rendered" in window.statusBar().currentMessage()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_midi_preview_render_is_debounced_and_supersedes_offscreen(tmp_path) -> None:
    """A burst of edits collapses to one process render; stale results drop."""

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import time

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.midi_preview_worker import MidiPreviewResult
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=True)
    window.state.audio_path = tmp_path / "original.wav"
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61],
        frame_times=[0.0, 0.1],
        activations=[[0.5, 0.5], [0.5, 0.5]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )

    # Five committed edits within the debounce window.
    for i in range(5):
        notes = [GuiMidiNote(pitch=60 + i % 2, start_seconds=0.0, duration_seconds=0.3, velocity=90)]
        window._refresh_midi_preview(notes)

    assert window._preview_request_id == 5
    assert window.preview_jobs == []  # nothing rendered yet; all debounced

    # Let the debounce fire and the isolated worker process finish.
    deadline = time.time() + 5.0
    while time.time() < deadline and (window.state.rendered_midi_wav is None or window.preview_jobs):
        app.processEvents()
        time.sleep(0.02)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.01)

    assert window.preview_jobs == []
    assert window.state.rendered_midi_wav is not None
    assert window.state.rendered_midi_wav.exists()
    first_preview_dir = window.state.rendered_midi_wav.parent
    assert "re-rendered" in window.statusBar().currentMessage()

    # A later generation gets a distinct directory/path and removes the old
    # accepted preview directory after the player source is swapped.
    window._refresh_midi_preview([GuiMidiNote(pitch=64, start_seconds=0.0, duration_seconds=0.3, velocity=90)])
    deadline = time.time() + 5.0
    while time.time() < deadline and (window.state.rendered_midi_wav is None or window.state.rendered_midi_wav.parent == first_preview_dir or window.preview_jobs):
        app.processEvents()
        time.sleep(0.02)
    assert window.state.rendered_midi_wav is not None
    assert window.state.rendered_midi_wav.parent != first_preview_dir
    assert not first_preview_dir.exists()

    # A stale completion (older render id) must be ignored.
    window._preview_request_id = 99
    window.state.rendered_midi_wav = None
    stale = MidiPreviewResult(render_id=5, rendered_wav=tmp_path / "stale.wav")
    window._preview_render_finished(stale, tmp_path)
    assert window.state.rendered_midi_wav is None  # dropped, not applied

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_main_window_edit_selected_note_updates_tuned_notes_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote, gui_notes_to_midi

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.state.extracted_notes = [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90)]
    window._set_display_notes(window.state.extracted_notes)
    window._select_note(0)
    window.note_start_spin.setValue(0.25)
    window.note_duration_spin.setValue(0.75)
    window.note_pitch_spin.setValue(64)
    window.note_velocity_spin.setValue(72)

    window._apply_selected_note_edit()

    assert window.state.tuned_notes is not None
    edited = window.state.current_notes[0]
    assert edited == GuiMidiNote(pitch=64, start_seconds=0.25, duration_seconds=0.75, velocity=72)
    assert gui_notes_to_midi(window.state.current_notes)[0].pitch == 64
    assert "MIDI 64" in window.selected_note_label.text()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_drag_signal_updates_main_window_note_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.state.extracted_notes = [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90)]
    window._set_display_notes(window.state.extracted_notes)

    window._edit_note_from_piano_roll(0, 0.1, 0.4, 62, 90)

    assert window.state.current_notes[0].pitch == 62
    assert window.state.current_notes[0].start_seconds == pytest.approx(0.1)
    assert window.state.current_notes[0].duration_seconds == pytest.approx(0.4)
    assert window.selected_note_index == 0
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_uncommitted_drag_uses_lightweight_preview_path_offscreen() -> None:
    """An in-progress (uncommitted) drag must not run the full set_data/preview path."""

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.state.extracted_notes = [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90)]
    window._set_display_notes(window.state.extracted_notes)
    window._select_note(0)

    set_data_calls = 0
    set_notes_calls = 0
    preview_note_edit_calls = 0
    original_set_data = window.piano_roll.set_data
    original_set_notes = window.sequence.set_notes
    original_preview = window.piano_roll.preview_note_edit

    def counting_set_data(*args, **kwargs):
        nonlocal set_data_calls
        set_data_calls += 1
        return original_set_data(*args, **kwargs)

    def counting_set_notes(*args, **kwargs):
        nonlocal set_notes_calls
        set_notes_calls += 1
        return original_set_notes(*args, **kwargs)

    def counting_preview(*args, **kwargs):
        nonlocal preview_note_edit_calls
        preview_note_edit_calls += 1
        return original_preview(*args, **kwargs)

    window.piano_roll.set_data = counting_set_data  # type: ignore[method-assign]
    window.sequence.set_notes = counting_set_notes  # type: ignore[method-assign]
    window.piano_roll.preview_note_edit = counting_preview  # type: ignore[method-assign]

    # Uncommitted drag move: lightweight path only.
    window._edit_note_from_piano_roll(0, 0.2, 0.3, 61, 90, committed=False)
    assert set_data_calls == 0
    assert set_notes_calls == 0
    assert preview_note_edit_calls == 1
    # State and inspector still reflect the edit.
    assert window.state.current_notes[0].pitch == 61
    assert window.state.current_notes[0].start_seconds == pytest.approx(0.2)
    assert window.note_pitch_spin.value() == 61

    # Commit (release) runs the full path once.
    window._edit_note_from_piano_roll(0, 0.2, 0.3, 61, 90, committed=True)
    assert set_data_calls == 1
    assert set_notes_calls == 1

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_piano_roll_canvas_is_tall_enough_for_full_midi_range_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.state import GuiHeatmap
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    app = QApplication.instance() or QApplication([])
    widget = PianoRollWidget()
    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=list(range(21, 109)),
        frame_times=[0.0, 0.1],
        activations=[[0.0] * 88, [0.0] * 88],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )

    widget.set_data(heatmap, [])

    assert widget.minimumHeight() >= 88 * widget.note_height
    assert widget.sizeHint().height() >= 88 * widget.note_height
    app.processEvents()


@pytest.mark.gui
def test_main_window_delete_selected_note_updates_tuned_notes_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8], [0.7, 0.6]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.state.extracted_notes = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90),
        GuiMidiNote(pitch=64, start_seconds=0.0, duration_seconds=0.5, velocity=88),
    ]
    window._set_display_notes(window.state.extracted_notes)
    window._select_note(0)

    window._delete_selected_note()

    assert window.state.tuned_notes is not None
    assert [note.pitch for note in window.state.current_notes] == [64]
    assert window.selected_note_index is None
    assert "No note selected" == window.selected_note_label.text()
    window.close()
    app.processEvents()


@pytest.mark.gui
def test_undo_redo_note_edits_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    baseline = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90),
        GuiMidiNote(pitch=64, start_seconds=1.0, duration_seconds=0.5, velocity=90),
    ]
    # Establish the analysis baseline (as _analysis_finished would).
    window.state.extracted_notes = baseline
    window.edit_history.begin(baseline)
    window._set_display_notes(baseline)

    # Nothing to undo at baseline.
    window._undo_edit()
    assert [n.pitch for n in window.state.current_notes] == [60, 64]

    # Delete the first note, then undo should bring it back.
    window._select_note(0)
    window._delete_selected_note()
    assert [n.pitch for n in window.state.current_notes] == [64]
    window._undo_edit()
    assert [n.pitch for n in window.state.current_notes] == [60, 64]
    # Redo re-applies the delete.
    window._redo_edit()
    assert [n.pitch for n in window.state.current_notes] == [64]

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_uncommitted_drag_is_one_undo_step_offscreen() -> None:
    """A multi-tick drag must record a single undo step, not one per move."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    start = [GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90)]
    window.state.extracted_notes = start
    window.edit_history.begin(start)
    window._set_display_notes(start)

    # Simulate a drag: several uncommitted moves, then one committed release.
    window._edit_note_from_piano_roll(0, 0.1, 0.5, 61, 90, committed=False)
    window._edit_note_from_piano_roll(0, 0.2, 0.5, 62, 90, committed=False)
    window._edit_note_from_piano_roll(0, 0.3, 0.5, 63, 90, committed=True)
    assert window.state.current_notes[0].pitch == 63

    # A single undo returns to the pre-drag state (pitch 60), not an intermediate.
    window._undo_edit()
    assert window.state.current_notes[0].pitch == 60
    # Nothing more to undo (the whole drag was one step).
    window._undo_edit()
    assert window.state.current_notes[0].pitch == 60

    window.close()
    app.processEvents()


@pytest.mark.gui
def test_stats_strip_scopes_to_selection_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    app = QApplication.instance() or QApplication([])
    window = MainWindow(render_midi=False)
    window.state.heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60, 61, 62, 63, 64],
        frame_times=[0.0, 0.1],
        activations=[[0.9, 0.8, 0.7, 0.6, 0.5], [0.7, 0.6, 0.5, 0.4, 0.3]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    window.waveform.set_preview([0.0, 0.1], sample_rate=1, duration_seconds=20.0)
    notes = [
        GuiMidiNote(pitch=60, start_seconds=0.0, duration_seconds=0.5, velocity=90),
        GuiMidiNote(pitch=62, start_seconds=10.0, duration_seconds=0.5, velocity=90),
    ]
    window.state.extracted_notes = notes
    window._set_display_notes(notes)
    # Whole-song stats: both notes, no "Selection:" prefix.
    assert "Notes 2" in window.stats_label.text()
    assert not window.stats_label.text().startswith("Selection:")

    # Select a range covering only the first note.
    window._set_analysis_range_from_waveform(0.0, 5.0)
    assert window.stats_label.text().startswith("Selection:")
    assert "Notes 1" in window.stats_label.text()

    window.close()
    app.processEvents()
