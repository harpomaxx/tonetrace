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
    assert not window.transport.play_both.isEnabled()
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
    assert "Horizontal zoom" in controls.heatmap_zoom.toolTip()
    assert "Ctrl" in controls.heatmap_zoom.toolTip()
    assert controls.analysis_range() == (0.0, None)
    controls.range_enabled.setChecked(True)
    controls.range_start.setValue(12.0)
    controls.range_duration.setValue(30.0)
    assert controls.analysis_range() == (12.0, 30.0)
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
def test_waveform_widget_range_selection_math_offscreen() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication
    from notegrabber.gui.widgets.waveform import WaveformWidget

    app = QApplication.instance() or QApplication([])
    widget = WaveformWidget()
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
def test_piano_roll_wheel_zoom_syncs_main_window_slider_offscreen() -> None:
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

    assert window.controls.zoom_factor() == pytest.approx(1.2)
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

    assert calls < frame_count * note_count // 2
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
    import notegrabber.gui.main_window as main_window_module
    from notegrabber.gui.main_window import MainWindow
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote

    def fake_render_midi_to_wav(_midi_path, wav_path):
        wav_path.write_bytes(b"edited midi preview")
        return wav_path, None

    monkeypatch.setattr(main_window_module, "render_midi_to_wav", fake_render_midi_to_wav)

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

    assert window.state.rendered_midi_wav is not None
    assert window.state.rendered_midi_wav.read_bytes() == b"edited midi preview"
    assert "preview re-rendered" in window.statusBar().currentMessage()
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
