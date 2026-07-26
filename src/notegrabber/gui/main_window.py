"""Main window for the native notegrabber standalone GUI."""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from notegrabber.analyzer import BackendName
from notegrabber.midi import write_midi
from notegrabber.visualizer import render_midi_to_wav

from .analysis_worker import AnalysisRequest, AnalysisResult, AnalysisWorker
from .overview_worker import OverviewResult, OverviewWorker
from .state import GuiMidiNote, ProjectState, delete_gui_note, gui_notes_to_midi, retune_notes_from_heatmap, update_gui_note
from .theme import APP_STYLESHEET, polish_button
from .widgets.controls import AnalysisControls
from .widgets.piano_roll import PianoRollWidget
from .widgets.sequence import SequenceWidget
from .waveform_worker import WaveformResult, WaveformWorker
from .widgets.transport import TransportWidget
from .widgets.waveform import WaveformWidget


class MainWindow(QMainWindow):
    """NeuralNote-inspired standalone app shell."""

    def __init__(self, *, initial_backend: BackendName = "basic-pitch", render_midi: bool = True) -> None:
        super().__init__()
        self.state = ProjectState(backend=initial_backend)
        self.render_midi = render_midi
        self.analysis_thread: QThread | None = None
        self.analysis_worker: AnalysisWorker | None = None
        self.waveform_runs: list[tuple[QThread, WaveformWorker]] = []
        self.overview_runs: list[tuple[QThread, OverviewWorker]] = []
        self.selected_note_index: int | None = None
        self.playback_mode = "stopped"
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(50)

        self.setWindowTitle("ToneTrace")
        self.resize(1280, 820)
        self.controls = AnalysisControls()
        self.controls.backend_combo.setCurrentText(initial_backend)
        self.waveform = WaveformWidget()
        self.piano_roll = PianoRollWidget()
        self.sequence = SequenceWidget()
        self.transport = TransportWidget()
        self.file_label = QLabel("No audio loaded")
        self.selected_note_label = QLabel("No note selected")
        self.note_start_spin = self._seconds_spin()
        self.note_duration_spin = self._seconds_spin(minimum=0.001)
        self.note_pitch_spin = QSpinBox()
        self.note_pitch_spin.setRange(0, 127)
        self.note_velocity_spin = QSpinBox()
        self.note_velocity_spin.setRange(1, 127)
        self.apply_note_button = QPushButton("Apply")
        polish_button(self.apply_note_button, role="primary", icon_name="export")
        self.original_audio = QAudioOutput(self)
        self.midi_audio = QAudioOutput(self)
        self.original_player = QMediaPlayer(self)
        self.midi_player = QMediaPlayer(self)
        self.original_player.setAudioOutput(self.original_audio)
        self.midi_player.setAudioOutput(self.midi_audio)
        self.original_audio.setVolume(0.85)
        self.midi_audio.setVolume(0.85)
        self.file_label.setObjectName("fileLabel")
        self.selected_note_label.setObjectName("selectedNoteLabel")
        self.selected_note_label.setTextFormat(Qt.TextFormat.RichText)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selected_note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._set_note_inspector_enabled(False)

        self._build_layout()
        self._connect_signals()
        self._apply_style()

    def load_audio(self, path: Path) -> None:
        """Load an audio file into the GUI without analyzing it yet."""

        path = path.expanduser().resolve()
        if not path.exists() or not path.is_file():
            QMessageBox.warning(self, "Audio not found", f"Cannot open audio file:\n{path}")
            return
        self.state.audio_path = path
        self.state.rendered_midi_wav = None
        self.state.heatmap = None
        self.state.extracted_notes = []
        self.state.tuned_notes = None
        self.state.analysis_start_seconds = 0.0
        self.state.analysis_duration_seconds = None
        self.state.midi_preview_offset_seconds = 0.0
        self._select_note(None)
        self.waveform.set_selection(None, None)
        self.waveform.set_pitch_overview(None)
        self.piano_roll.set_data(None, [])
        self.sequence.set_notes([])
        self.controls.set_can_export(False)
        self.file_label.setText(f"{path.name} — {path}")
        self.original_player.setSource(QUrl.fromLocalFile(str(path)))
        self.midi_player.setSource(QUrl())
        self.waveform.set_message("Loading waveform preview…")
        self._start_waveform_load(path)
        self._start_overview_load(path)
        self.transport.set_status("Audio loaded. Loading waveform and overview in background…")
        self.transport.set_playback_available(original=True, midi=False)

    def _seconds_spin(self, *, minimum: float = 0.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, 36_000.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.01)
        spin.setSuffix(" s")
        spin.setKeyboardTracking(False)
        spin.setMaximumWidth(96)
        return spin

    def _build_note_inspector(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("noteInspector")
        panel.setMaximumHeight(58)
        self.note_pitch_spin.setMaximumWidth(68)
        self.note_velocity_spin.setMaximumWidth(68)
        self.apply_note_button.setMaximumWidth(86)

        title = QLabel("Selected note")
        title.setObjectName("inlineFieldLabel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(self.selected_note_label, 1)
        layout.addWidget(self._inline_note_field("Start", self.note_start_spin))
        layout.addWidget(self._inline_note_field("Dur", self.note_duration_spin))
        layout.addWidget(self._inline_note_field("Pitch", self.note_pitch_spin))
        layout.addWidget(self._inline_note_field("Vel", self.note_velocity_spin))
        layout.addWidget(self.apply_note_button)
        return panel

    @staticmethod
    def _inline_note_field(label_text: str, editor: QWidget) -> QWidget:
        field = QWidget()
        layout = QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setObjectName("inlineFieldLabel")
        layout.addWidget(label)
        layout.addWidget(editor)
        return field

    def _build_layout(self) -> None:
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.addWidget(self.transport)
        top_layout.addWidget(self.file_label)
        top_layout.addWidget(self.waveform)

        self.piano_scroll = QScrollArea()
        self.piano_scroll.setWidgetResizable(True)
        self.piano_scroll.setWidget(self.piano_roll)
        self.piano_scroll.setMinimumHeight(240)
        self.piano_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(10)
        right_layout.addWidget(top)
        right_layout.addWidget(self._section_label("Heatmap + MIDI note map"))
        right_layout.addWidget(self.piano_scroll, 2)
        right_layout.addWidget(self._build_note_inspector())
        right_layout.addWidget(self._section_label("Detected sequence"))
        self.sequence.setMinimumHeight(150)
        right_layout.addWidget(self.sequence, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.controls)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1000])
        self.setCentralWidget(splitter)
        self._update_heatmap_view_height()
        self.statusBar().showMessage("Ready")

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _connect_signals(self) -> None:
        self.controls.open_requested.connect(self._open_audio_dialog)
        self.controls.analyze_requested.connect(self._start_analysis)
        self.controls.export_requested.connect(self._export_midi_dialog)
        self.controls.delete_requested.connect(self._delete_selected_note)
        self.controls.retune_requested.connect(self._retune_from_controls)
        self.controls.overlay_toggled.connect(self.piano_roll.set_show_notes)
        self.transport.play_both_requested.connect(self._play_both)
        self.transport.play_original_requested.connect(self._play_original)
        self.transport.play_midi_requested.connect(self._play_midi)
        self.transport.pause_requested.connect(self._pause_playback)
        self.transport.stop_requested.connect(self._stop_playback)
        self.original_player.positionChanged.connect(self._playback_position_changed)
        self.midi_player.positionChanged.connect(self._midi_position_changed)
        self.original_player.playbackStateChanged.connect(self._playback_state_changed)
        self.midi_player.playbackStateChanged.connect(self._playback_state_changed)
        self.playback_timer.timeout.connect(self._sync_playback_tick)
        self.waveform.seek_requested.connect(self._seek_seconds)
        self.waveform.range_selected.connect(self._set_analysis_range_from_waveform)
        self.piano_roll.seek_requested.connect(self._seek_seconds)
        self.piano_roll.note_selected.connect(self._select_note)
        self.piano_roll.note_edited.connect(self._edit_note_from_piano_roll)
        self.sequence.seek_requested.connect(self._seek_seconds)
        self.apply_note_button.clicked.connect(self._apply_selected_note_edit)

    def _open_audio_dialog(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Open audio",
            str(Path.home()),
            "Audio files (*.wav *.wave *.mp3 *.flac *.ogg *.aiff *.aif);;All files (*)",
        )
        if path:
            self.load_audio(Path(path))

    def _start_overview_load(self, path: Path) -> None:
        thread = QThread(self)
        worker = OverviewWorker(path)
        self.overview_runs.append((thread, worker))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._set_status)
        worker.finished.connect(self._overview_finished)
        worker.failed.connect(self._overview_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda thread=thread, worker=worker: self._overview_thread_finished(thread, worker))
        thread.start()

    def _overview_finished(self, result: OverviewResult) -> None:
        if self.state.audio_path != result.audio_path:
            return
        self.waveform.set_pitch_overview(result.overview)
        self._set_status("Low-resolution pitch overview ready. Drag waveform to choose a range, then Analyze.")

    def _overview_failed(self, audio_path: Path, message: str) -> None:
        if self.state.audio_path != audio_path:
            return
        self.waveform.set_pitch_overview(None)
        self._set_status(f"Pitch overview unavailable: {message}")

    def _overview_thread_finished(self, thread: QThread, worker: OverviewWorker) -> None:
        self.overview_runs = [(run_thread, run_worker) for run_thread, run_worker in self.overview_runs if run_thread is not thread]
        worker.deleteLater()
        thread.deleteLater()

    def _start_waveform_load(self, path: Path) -> None:
        thread = QThread(self)
        worker = WaveformWorker(path)
        self.waveform_runs.append((thread, worker))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._waveform_finished)
        worker.failed.connect(self._waveform_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda thread=thread, worker=worker: self._waveform_thread_finished(thread, worker))
        thread.start()

    def _waveform_finished(self, result: WaveformResult) -> None:
        if self.state.audio_path != result.audio_path:
            return
        self.waveform.set_preview(
            result.samples,
            sample_rate=result.sample_rate,
            duration_seconds=result.duration_seconds,
        )
        self.controls.set_audio_duration(result.duration_seconds)
        if not self._maybe_enable_default_range_for_long_audio(result.duration_seconds):
            self._set_status("Waveform preview ready. Drag to choose a range or click Analyze.")

    def _waveform_failed(self, audio_path: Path, message: str) -> None:
        if self.state.audio_path != audio_path:
            return
        self.waveform.set_message("Waveform preview unavailable")
        self._set_status(f"Audio loaded, but waveform preview failed: {message}")

    def _waveform_thread_finished(self, thread: QThread, worker: WaveformWorker) -> None:
        self.waveform_runs = [(run_thread, run_worker) for run_thread, run_worker in self.waveform_runs if run_thread is not thread]
        worker.deleteLater()
        thread.deleteLater()

    def _maybe_enable_default_range_for_long_audio(self, duration_seconds: float) -> bool:
        if duration_seconds < 180.0 or self.controls.range_enabled.isChecked():
            return False
        default_duration = min(30.0, duration_seconds)
        self.controls.set_analysis_range(0.0, default_duration)
        self.waveform.set_selection(0.0, default_duration)
        self._set_status("Long file loaded: range analysis enabled for the first 30s. Drag waveform selection to choose another section.")
        return True

    def _start_analysis(self) -> None:
        if self.state.audio_path is None:
            QMessageBox.information(self, "Open audio", "Open an audio file before analyzing.")
            return
        if self.analysis_thread is not None:
            return

        backend: BackendName = self.controls.backend()  # type: ignore[assignment]
        self.state.backend = backend
        self.state.threshold = self.controls.threshold()
        self.state.onset_threshold = self.controls.onset_threshold()
        self.state.frame_threshold = self.controls.frame_threshold()
        self.state.min_duration = self.controls.min_duration_seconds()

        range_start_seconds, range_duration_seconds = self.controls.analysis_range()
        request = AnalysisRequest(
            audio_path=self.state.audio_path,
            backend=backend,
            render_midi=self.render_midi,
            threshold=self.state.threshold,
            onset_threshold=self.state.onset_threshold,
            frame_threshold=self.state.frame_threshold,
            min_duration_seconds=self.state.min_duration,
            range_start_seconds=range_start_seconds,
            range_duration_seconds=range_duration_seconds,
        )
        self.analysis_thread = QThread(self)
        self.analysis_worker = AnalysisWorker(request)
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.progress.connect(self._set_status)
        self.analysis_worker.finished.connect(self._analysis_finished)
        self.analysis_worker.failed.connect(self._analysis_failed)
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.failed.connect(self.analysis_thread.quit)
        self.analysis_thread.finished.connect(self._analysis_thread_finished)
        self.controls.set_busy(True)
        if range_duration_seconds is not None:
            self._set_status(f"Analyzing {range_duration_seconds:.1f}s range from {range_start_seconds:.1f}s with {backend}…")
        else:
            self._set_status(f"Analyzing full audio with {backend}…")
        self.analysis_thread.start()

    def _analysis_finished(self, result: AnalysisResult) -> None:
        self.state.audio_path = result.audio_path
        self.state.backend = result.backend
        self.state.rendered_midi_wav = result.rendered_midi_wav
        self.state.heatmap = result.heatmap
        self.state.extracted_notes = result.notes
        self.state.tuned_notes = None
        self.state.analysis_start_seconds = result.analysis_start_seconds
        self.state.analysis_duration_seconds = result.analysis_duration_seconds
        self.state.midi_preview_offset_seconds = result.midi_preview_offset_seconds
        self._select_note(None)
        self._set_display_notes(result.notes)
        if result.rendered_midi_wav is not None:
            self.midi_player.setSource(QUrl.fromLocalFile(str(result.rendered_midi_wav)))
        else:
            self.midi_player.setSource(QUrl())
        self.transport.set_playback_available(original=True, midi=result.rendered_midi_wav is not None)
        message = f"Analyzed {len(result.notes)} notes with {result.backend}."
        if result.render_error:
            message += f" MIDI preview unavailable: {result.render_error}"
        self._set_status(message)

    def _analysis_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Analysis failed", message)
        self._set_status(f"Analysis failed: {message}")

    def _analysis_thread_finished(self) -> None:
        self.controls.set_busy(False)
        if self.analysis_worker is not None:
            self.analysis_worker.deleteLater()
        if self.analysis_thread is not None:
            self.analysis_thread.deleteLater()
        self.analysis_worker = None
        self.analysis_thread = None

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._update_heatmap_view_height()
        super().resizeEvent(event)

    def _update_heatmap_view_height(self) -> None:
        if hasattr(self, "piano_scroll"):
            self.piano_scroll.setMaximumHeight(max(260, round(self.height() * 0.48)))

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_note()
            event.accept()
            return
        super().keyPressEvent(event)

    def _retune_from_controls(self) -> None:
        if self.state.heatmap is None:
            return
        self.state.threshold = self.controls.threshold()
        self.state.min_duration = self.controls.min_duration_seconds()
        if self.state.heatmap.backend == "cqt":
            tuned = retune_notes_from_heatmap(
                self.state.heatmap,
                threshold=self.state.threshold,
                min_duration_seconds=self.state.min_duration,
            )
            self.state.tuned_notes = tuned
            self._select_note(None)
            self._set_display_notes(tuned)
            self.controls.set_can_export(True)
            preview_status = self._refresh_midi_preview(tuned)
            self._set_status(f"Retuned CQT notes in memory: {len(tuned)} notes. Export writes tuned notes.{preview_status}")
        elif self.state.heatmap.backend == "basic-pitch":
            self._set_status("Basic Pitch threshold changes require Analyze to rerun the ML model for now.")

    def _set_display_notes(self, notes: list[GuiMidiNote]) -> None:
        self.piano_roll.set_data(self.state.heatmap, notes)
        self.sequence.set_notes(notes)
        self.controls.set_can_export(self.state.heatmap is not None)

    def _select_note(self, index: int | None, _seek_seconds: float | None = None) -> None:
        notes = self.state.current_notes
        self.selected_note_index = index if index is not None and 0 <= index < len(notes) else None
        self.piano_roll.set_selected_note_index(self.selected_note_index)
        self.controls.set_can_delete(self.selected_note_index is not None)
        self._set_note_inspector_enabled(self.selected_note_index is not None)
        if self.selected_note_index is None:
            self.selected_note_label.setText("No note selected")
            return
        note = notes[self.selected_note_index]
        self._populate_note_inspector(note)
        self.selected_note_label.setText(self._note_summary(note))

    def _delete_selected_note(self) -> None:
        if self.selected_note_index is None:
            return
        current = list(self.state.current_notes)
        deleted = current[self.selected_note_index]
        self.state.tuned_notes = delete_gui_note(current, self.selected_note_index)
        self._select_note(None)
        self._set_display_notes(self.state.tuned_notes)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        self._set_status(f"Deleted MIDI {deleted.pitch}. Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}")

    def _apply_selected_note_edit(self) -> None:
        if self.selected_note_index is None:
            return
        self._edit_note(
            self.selected_note_index,
            start_seconds=self.note_start_spin.value(),
            duration_seconds=self.note_duration_spin.value(),
            pitch=self.note_pitch_spin.value(),
            velocity=self.note_velocity_spin.value(),
            status_prefix="Edited selected note",
            update_preview=True,
        )

    def _edit_note_from_piano_roll(
        self,
        index: int,
        start_seconds: float,
        duration_seconds: float,
        pitch: int,
        velocity: int,
        committed: bool = True,
    ) -> None:
        self._edit_note(
            index,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            pitch=pitch,
            velocity=velocity,
            status_prefix="Moved/resized selected note",
            update_preview=committed,
        )

    def _edit_note(
        self,
        index: int,
        *,
        start_seconds: float,
        duration_seconds: float,
        pitch: int,
        velocity: int,
        status_prefix: str,
        update_preview: bool,
    ) -> None:
        current = list(self.state.current_notes)
        if index < 0 or index >= len(current):
            return
        self.state.tuned_notes = update_gui_note(
            current,
            index,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            pitch=pitch,
            velocity=velocity,
        )
        self.selected_note_index = index
        self._set_display_notes(self.state.tuned_notes)
        self._select_note(index)
        if update_preview:
            preview_status = self._refresh_midi_preview(self.state.tuned_notes)
            self._set_status(f"{status_prefix}. Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}")
        else:
            self._set_status(f"Editing selected note… release to update MIDI preview. Export writes {len(self.state.tuned_notes)} edited notes.")

    def _refresh_midi_preview(self, notes: list[GuiMidiNote]) -> str:
        if not self.render_midi:
            return " MIDI preview rendering disabled."
        preview_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-edit-"))
        midi_path = preview_dir / "edited.mid"
        wav_path = preview_dir / "edited.wav"
        try:
            preview_notes = self._notes_for_midi_preview(notes)
            write_midi(midi_path, gui_notes_to_midi(preview_notes))
            if preview_notes:
                rendered_path, render_error = render_midi_to_wav(midi_path, wav_path)
            else:
                self._write_silent_wav(wav_path)
                rendered_path, render_error = wav_path, None
        except Exception as exc:
            self.state.rendered_midi_wav = None
            self.midi_player.setSource(QUrl())
            self.transport.set_playback_available(original=self.state.audio_path is not None, midi=False)
            return f" MIDI preview unavailable: {exc}"
        if rendered_path is None:
            self.state.rendered_midi_wav = None
            self.midi_player.setSource(QUrl())
            self.transport.set_playback_available(original=self.state.audio_path is not None, midi=False)
            return f" MIDI preview unavailable: {render_error or 'unknown render error'}"
        previous_display_seconds = self._display_seconds_from_midi_position(self.midi_player.position())
        self.state.rendered_midi_wav = rendered_path
        self.state.midi_preview_offset_seconds = self.state.analysis_start_seconds if self.state.analysis_duration_seconds is not None else 0.0
        self.midi_player.setSource(QUrl.fromLocalFile(str(rendered_path)))
        if previous_display_seconds > 0:
            self.midi_player.setPosition(self._midi_position_from_display_seconds(previous_display_seconds))
        self.transport.set_playback_available(original=self.state.audio_path is not None, midi=True)
        return " MIDI preview re-rendered."

    def _notes_for_midi_preview(self, notes: list[GuiMidiNote]) -> list[GuiMidiNote]:
        """Return notes in the local MIDI-preview timeline.

        Exported notes stay in the full-song timeline.  Range previews are
        rendered locally so a selection starting at e.g. 40s does not produce a
        MIDI WAV with 40s of leading silence; playback maps local MIDI time back
        to the full waveform/heatmap timeline.
        """

        if self.state.analysis_duration_seconds is None:
            return list(notes)
        start = self.state.analysis_start_seconds
        end = start + self.state.analysis_duration_seconds
        preview_notes: list[GuiMidiNote] = []
        for note in notes:
            note_start = max(note.start_seconds, start)
            note_end = min(note.end_seconds, end)
            if note_end <= note_start:
                continue
            preview_notes.append(
                GuiMidiNote(
                    pitch=note.pitch,
                    start_seconds=note_start - start,
                    duration_seconds=note_end - note_start,
                    velocity=note.velocity,
                    source=note.source,
                )
            )
        return preview_notes

    def _write_silent_wav(self, path: Path) -> None:
        """Write a silent WAV preview for an intentionally empty edited note list."""

        sample_rate = 44_100
        duration = max(1.0, self.waveform.duration_seconds())
        frame_count = max(1, round(duration * sample_rate))
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b"\x00\x00" * frame_count)

    def _set_note_inspector_enabled(self, enabled: bool) -> None:
        for widget in (
            self.note_start_spin,
            self.note_duration_spin,
            self.note_pitch_spin,
            self.note_velocity_spin,
            self.apply_note_button,
        ):
            widget.setEnabled(enabled)

    def _populate_note_inspector(self, note: GuiMidiNote) -> None:
        self.note_start_spin.setValue(note.start_seconds)
        self.note_duration_spin.setValue(note.duration_seconds)
        self.note_pitch_spin.setValue(note.pitch)
        self.note_velocity_spin.setValue(note.velocity)

    @staticmethod
    def _note_summary(note: GuiMidiNote) -> str:
        note_name = MainWindow._note_name(note.pitch)
        return f'<span style="color:#ffd15f; font-weight:900; font-size:15px;">{note_name}</span> <span style="color:#9aa8bd;">MIDI {note.pitch}</span>'

    @staticmethod
    def _note_name(pitch: int) -> str:
        names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return f"{names[pitch % 12]}{pitch // 12 - 1}"

    def _set_analysis_range_from_waveform(self, start_seconds: float, duration_seconds: float) -> None:
        self.controls.set_analysis_range(start_seconds, duration_seconds)
        self.waveform.set_selection(start_seconds, duration_seconds)
        self._seek_seconds(start_seconds)
        self._set_status(f"Selected range {start_seconds:.2f}s–{start_seconds + duration_seconds:.2f}s ({duration_seconds:.2f}s). Click Analyze.")

    def _seek_seconds(self, seconds: float) -> None:
        display_seconds = max(0.0, float(seconds))
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        if self.state.rendered_midi_wav is not None:
            self.midi_player.setPosition(self._midi_position_from_display_seconds(display_seconds))
        self._set_playhead(display_seconds)

    def _play_both(self) -> None:
        if self.state.audio_path is None or self.state.rendered_midi_wav is None:
            return
        display_seconds = self._play_start_display_seconds()
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        self.midi_player.setPosition(self._midi_position_from_display_seconds(display_seconds))
        self.playback_mode = "both"
        self._set_playhead(display_seconds)
        self.playback_timer.start()
        self.original_player.play()
        self.midi_player.play()
        self._set_status("Playing original + rendered MIDI")

    def _play_original(self) -> None:
        if self.state.audio_path is None:
            return
        display_seconds = self._play_start_display_seconds()
        self.midi_player.pause()
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        self.playback_mode = "original"
        self._set_playhead(display_seconds)
        self.playback_timer.start()
        self.original_player.play()
        self._set_status("Playing original audio")

    def _play_midi(self) -> None:
        if self.state.rendered_midi_wav is None:
            return
        display_seconds = self._play_start_display_seconds()
        self.original_player.pause()
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        self.midi_player.setPosition(self._midi_position_from_display_seconds(display_seconds))
        self.playback_mode = "midi"
        self._set_playhead(display_seconds)
        self.playback_timer.start()
        self.midi_player.play()
        self._set_status("Playing rendered MIDI")

    def _play_start_display_seconds(self) -> float:
        current = self._current_display_seconds()
        range_bounds = self._active_analysis_range()
        if range_bounds is not None:
            start, end = range_bounds
            if current < start or current >= max(start, end - 0.1):
                return start
            return current
        duration = self._shared_duration_ms() / 1000.0
        if duration > 0 and current >= max(0.0, duration - 0.1):
            return 0.0
        return max(0.0, current)

    def _current_display_seconds(self) -> float:
        if self.playback_mode == "midi":
            return self._display_seconds_from_midi_position(self.midi_player.position())
        if self.playback_mode == "both" and self.original_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return self._display_seconds_from_midi_position(self.midi_player.position())
        return self._display_seconds_from_original_position(self.original_player.position())

    def _display_seconds_from_original_position(self, position_ms: int) -> float:
        return max(0.0, float(position_ms) / 1000.0)

    def _display_seconds_from_midi_position(self, position_ms: int) -> float:
        seconds = self.state.midi_preview_offset_seconds + max(0.0, float(position_ms) / 1000.0)
        return self._clamp_to_analysis_range(seconds)

    def _original_position_from_display_seconds(self, seconds: float) -> int:
        return max(0, round(max(0.0, float(seconds)) * 1000))

    def _midi_position_from_display_seconds(self, seconds: float) -> int:
        local_seconds = max(0.0, float(seconds) - self.state.midi_preview_offset_seconds)
        if self.state.analysis_duration_seconds is not None:
            local_seconds = min(local_seconds, self.state.analysis_duration_seconds)
        return max(0, round(local_seconds * 1000))

    def _active_analysis_range(self) -> tuple[float, float] | None:
        if self.state.analysis_duration_seconds is None:
            return None
        start = max(0.0, self.state.analysis_start_seconds)
        return start, start + max(0.0, self.state.analysis_duration_seconds)

    def _clamp_to_analysis_range(self, seconds: float) -> float:
        bounds = self._active_analysis_range()
        if bounds is None:
            return max(0.0, seconds)
        start, end = bounds
        return max(start, min(float(seconds), end))

    def _shared_duration_ms(self) -> int:
        durations = [self.original_player.duration(), round(self.waveform.duration_seconds() * 1000)]
        if self.state.heatmap is not None:
            durations.append(round(self.state.heatmap.duration_seconds * 1000))
        bounds = self._active_analysis_range()
        if bounds is not None:
            durations.append(round(bounds[1] * 1000))
        return max(0, *(duration for duration in durations if duration is not None))

    @staticmethod
    def _play_start_position(position_ms: int, duration_ms: int, *, end_tolerance_ms: int = 100) -> int:
        """Return a sane playback start position, rewinding if the reference player is at the end."""

        if duration_ms > 0 and position_ms >= max(0, duration_ms - end_tolerance_ms):
            return 0
        return max(0, position_ms)

    def _pause_playback(self) -> None:
        display_seconds = self._current_display_seconds()
        self.original_player.pause()
        self.midi_player.pause()
        self.playback_mode = "paused"
        self.playback_timer.stop()
        self._set_playhead(display_seconds)
        self._set_status("Playback paused")

    def _stop_playback(self) -> None:
        self.original_player.stop()
        self.midi_player.stop()
        self.playback_mode = "stopped"
        self.playback_timer.stop()
        self._set_playhead(0.0)
        self._set_status("Playback stopped")

    def _playback_position_changed(self, milliseconds: int) -> None:
        if not self.playback_timer.isActive() and self.playback_mode != "midi":
            self._set_playhead(self._display_seconds_from_original_position(milliseconds))

    def _midi_position_changed(self, milliseconds: int) -> None:
        if not self.playback_timer.isActive() and self.playback_mode == "midi":
            self._set_playhead(self._display_seconds_from_midi_position(milliseconds))

    def _playback_state_changed(self, _state: QMediaPlayer.PlaybackState) -> None:
        if self.original_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if not self.playback_timer.isActive():
                self.playback_timer.start()
            return
        if self.midi_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if not self.playback_timer.isActive():
                self.playback_timer.start()
            return
        self.playback_timer.stop()
        self._set_playhead(self._current_display_seconds())

    def _sync_playback_tick(self) -> None:
        if self.playback_mode == "midi":
            display_seconds = self._display_seconds_from_midi_position(self.midi_player.position())
        else:
            display_seconds = self._display_seconds_from_original_position(self.original_player.position())
            if self.playback_mode == "both":
                self._sync_player_position(self.midi_player, self._midi_position_from_display_seconds(display_seconds))
        if self._stop_if_past_analysis_end(display_seconds):
            return
        self._set_playhead(display_seconds)

    def _stop_if_past_analysis_end(self, display_seconds: float) -> bool:
        bounds = self._active_analysis_range()
        if bounds is None or self.playback_mode not in {"both", "midi", "original"}:
            return False
        _start, end = bounds
        if display_seconds < end:
            return False
        self.original_player.pause()
        self.midi_player.pause()
        self.playback_mode = "paused"
        self.playback_timer.stop()
        self._set_playhead(end)
        self._set_status("Reached selected analysis range end")
        return True

    @staticmethod
    def _position_needs_sync(reference_ms: int, follower_ms: int, *, tolerance_ms: int = 80) -> bool:
        return abs(int(reference_ms) - int(follower_ms)) > tolerance_ms

    def _sync_player_position(self, player: QMediaPlayer, reference_ms: int) -> None:
        if self._position_needs_sync(reference_ms, player.position()):
            player.setPosition(max(0, reference_ms))

    def _set_playhead(self, seconds: float) -> None:
        clamped_seconds = max(0.0, min(float(seconds), self._shared_duration_ms() / 1000.0 if self._shared_duration_ms() > 0 else float(seconds)))
        self.waveform.set_playhead(clamped_seconds)
        self.piano_roll.set_playhead(clamped_seconds)

    def _export_midi_dialog(self) -> None:
        notes = self.state.current_notes
        if self.state.heatmap is None:
            QMessageBox.information(self, "No analysis", "Analyze audio before exporting MIDI.")
            return
        path, _filter = QFileDialog.getSaveFileName(self, "Export MIDI", "analysis.mid", "MIDI files (*.mid *.midi);;All files (*)")
        if not path:
            return
        output = Path(path)
        if output.suffix.lower() not in {".mid", ".midi"}:
            output = output.with_suffix(".mid")
        try:
            write_midi(output, gui_notes_to_midi(notes))
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self._set_status(f"Exported {len(notes)} notes to {output}")

    def _set_status(self, text: str) -> None:
        self.transport.set_status(text)
        self.statusBar().showMessage(text)

    def _apply_style(self) -> None:
        self.setStyleSheet(APP_STYLESHEET)
