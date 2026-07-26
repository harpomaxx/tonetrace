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
