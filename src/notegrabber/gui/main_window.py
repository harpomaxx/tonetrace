"""Main window for the native notegrabber standalone GUI."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QThread, QTimer, Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
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

from .analysis_worker import AnalysisRequest, AnalysisResult, AnalysisWorker
from .edit_history import EditHistory
from .transcription_stats import compute_stats
from .midi_preview_worker import MidiPreviewRequest, MidiPreviewResult, MidiPreviewWorker, render_midi_preview
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

    # Solo playback volume for a single source.
    SOLO_VOLUME = 0.85
    # Play-both mix: duck the (usually busier) original and keep the rendered
    # MIDI at full level so the transcription is clearly audible for A/B checks.
    BOTH_ORIGINAL_VOLUME = 0.55
    BOTH_MIDI_VOLUME = 1.0

    PLAYBACK_RESYNC_TICKS = 12
    # QMediaPlayer.position() lags the audible position on buffered backends
    # (FFmpeg/GStreamer report the start of the current decode buffer), so it is
    # normally *behind* the smooth elapsed-timer estimate. We must not snap the
    # playhead backward to chase that lag, or the line stutters back every resync.
    # Only correct when the player is ahead of us (a real seek/underrun pushed
    # true time forward) or drift is far too large to be ordinary backend lag.
    PLAYBACK_RESYNC_AHEAD_TOLERANCE_SECONDS = 0.08
    PLAYBACK_RESYNC_MAX_LAG_SECONDS = 0.5

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
        self.playback_timer.setInterval(16)
        self.playback_clock = QElapsedTimer()
        self.playback_clock_anchor_seconds = 0.0
        self.playback_clock_valid = False
        self.playback_sync_ticks = 0
        self.playback_stalled = False
        self._suppress_reactive_playhead = False

        # Debounced, superseding, off-thread MIDI preview rendering so editing
        # notes does not freeze the UI on every edit.
        self.preview_runs: list[tuple[QThread, MidiPreviewWorker]] = []
        self._preview_dir: Path | None = None
        self._preview_request_id = 0
        self._pending_preview_notes: list[GuiMidiNote] | None = None
        self.preview_debounce_timer = QTimer(self)
        self.preview_debounce_timer.setSingleShot(True)
        self.preview_debounce_timer.setInterval(250)

        self.setWindowTitle("ToneTrace")
        self.resize(1280, 820)
        self.controls = AnalysisControls()
        self.controls.backend_combo.setCurrentText(initial_backend)
        self.waveform = WaveformWidget()
        self.piano_roll = PianoRollWidget()
        # Keep the waveform's left gutter equal to the piano roll's keyboard so
        # both timelines use the same seconds->x mapping and their playheads line up.
        self.waveform.left_gutter = self.piano_roll.keyboard_width
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
        self.original_audio.setVolume(self.SOLO_VOLUME)
        self.midi_audio.setVolume(self.SOLO_VOLUME)
        self.file_label.setObjectName("fileLabel")
        self.selected_note_label.setObjectName("selectedNoteLabel")
        self.selected_note_label.setTextFormat(Qt.TextFormat.RichText)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selected_note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._set_note_inspector_enabled(False)

        # Undo/redo history for committed note edits (delete, drag, inspector
        # apply, CQT retune). Baseline is set when a fresh analysis lands.
        self.edit_history: EditHistory = EditHistory()
        # Note list captured at the start of an edit gesture, recorded to history
        # on commit so a multi-tick drag is a single undo step.
        self._pre_edit_snapshot: list[GuiMidiNote] | None = None

        self._build_layout()
        self._connect_signals()
        self._apply_style()
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        undo = QShortcut(QKeySequence.StandardKey.Undo, self)  # Ctrl+Z
        undo.activated.connect(self._undo_edit)
        # Bind both common redo conventions: Ctrl+Y (Windows/Linux) and
        # Ctrl+Shift+Z (macOS / many DAWs), so redo works whatever the user expects.
        for sequence in ("Ctrl+Y", "Ctrl+Shift+Z"):
            redo = QShortcut(QKeySequence(sequence), self)
            redo.activated.connect(self._redo_edit)

    def load_audio(self, path: Path) -> None:
        """Load an audio file into the GUI without analyzing it yet."""

        path = path.expanduser().resolve()
        if not path.exists() or not path.is_file():
            QMessageBox.warning(self, "Audio not found", f"Cannot open audio file:\n{path}")
            return
        # Stop any in-progress playback before swapping the media sources. Calling
        # setSource() on a QMediaPlayer that is actively playing can deadlock the
        # audio backend and hang the app, so tear playback down first. Also cancel
        # any queued MIDI-preview render so it does not fire against the state we
        # are about to reset.
        self._stop_playback()
        self.preview_debounce_timer.stop()
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
        # One-line app status strip across the top.
        top_layout.addWidget(self.transport.status_label)
        # Action buttons and the Playback group share one row to save vertical space.
        action_row = QWidget()
        action_row_layout = QHBoxLayout(action_row)
        action_row_layout.setContentsMargins(0, 0, 0, 0)
        action_row_layout.setSpacing(10)
        action_row_layout.addWidget(self.controls.build_action_bar())
        action_row_layout.addStretch(1)
        action_row_layout.addWidget(self.transport.playback_group)
        top_layout.addWidget(action_row)
        top_layout.addWidget(self.file_label)
        top_layout.addWidget(self.waveform)
        self.stats_label = QLabel("Notes 0  ·  0:00  ·  — BPM  ·  —")
        self.stats_label.setObjectName("statsStrip")
        self.stats_label.setToolTip("Transcription summary: note count · duration · estimated tempo · detected key. Drag a range on the waveform to scope it to that slice (shown as 'Selection:'). Updates after Analyze and edits.")
        top_layout.addWidget(self.stats_label)

        self.piano_scroll = QScrollArea()
        self.piano_scroll.setWidgetResizable(True)
        self.piano_scroll.setWidget(self.piano_roll)
        self.piano_scroll.setMinimumHeight(240)
        self.piano_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Repaint the whole canvas as it scrolls horizontally so the pinned
        # keyboard (drawn at the visible left edge) does not smear or ghost.
        self.piano_scroll.horizontalScrollBar().valueChanged.connect(self.piano_roll.update)

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
        self.controls.heatmap_toggled.connect(self.piano_roll.set_show_heatmap)
        self.controls.pitch_bends_toggled.connect(self.piano_roll.set_show_pitch_bends)
        self.transport.play_both_requested.connect(self._play_both)
        self.transport.play_original_requested.connect(self._play_original)
        self.transport.play_midi_requested.connect(self._play_midi)
        self.transport.pause_requested.connect(self._pause_playback)
        self.transport.stop_requested.connect(self._stop_playback)
        self.original_player.positionChanged.connect(self._playback_position_changed)
        self.midi_player.positionChanged.connect(self._midi_position_changed)
        self.original_player.playbackStateChanged.connect(self._playback_state_changed)
        self.midi_player.playbackStateChanged.connect(self._playback_state_changed)
        self.original_player.mediaStatusChanged.connect(self._media_status_changed)
        self.midi_player.mediaStatusChanged.connect(self._media_status_changed)
        self.playback_timer.timeout.connect(self._sync_playback_tick)
        self.preview_debounce_timer.timeout.connect(self._start_preview_render)
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
        # A fresh analysis is the new undo baseline; discard prior edit history.
        self.edit_history.begin(result.notes)
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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.preview_debounce_timer.stop()
        for thread, _worker in list(self.preview_runs):
            thread.quit()
            thread.wait(2000)
        if self._preview_dir is not None:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
            self._preview_dir = None
        super().closeEvent(event)

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
            self.edit_history.record(self.state.current_notes)
            self.state.tuned_notes = tuned
            self._select_note(None)
            self._set_display_notes(tuned)
            self.controls.set_can_export(True)
            preview_status = self._refresh_midi_preview(tuned)
            self._set_status(f"Retuned CQT notes in memory: {len(tuned)} notes. Export writes tuned notes.{preview_status}")
        elif self.state.heatmap.backend == "basic-pitch":
            self._set_status("Basic Pitch threshold changes require Analyze to rerun the ML model for now.")

    def _set_display_notes(self, notes: list[GuiMidiNote]) -> None:
        self.piano_roll.set_data(self.state.heatmap, notes, full_duration_seconds=self.waveform.duration_seconds())
        self.sequence.set_notes(notes)
        self.controls.set_can_export(self.state.heatmap is not None)
        self._update_stats(notes)

    def _update_stats(self, notes: list[GuiMidiNote]) -> None:
        """Compute and display the transcription stats strip.

        When a waveform range is selected, the strip describes that slice
        (labelled "Selection:"); otherwise it covers the whole transcription.
        """

        start = self.waveform.selection_start_seconds
        length = self.waveform.selection_duration_seconds
        if start is not None and length is not None and length > 0:
            stats = compute_stats(
                notes,
                duration_seconds=length,
                start_seconds=start,
                end_seconds=start + length,
                is_selection=True,
            )
        else:
            stats = compute_stats(notes, duration_seconds=self.waveform.duration_seconds())
        self.stats_label.setText(stats.strip_text())

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
        self.edit_history.record(current)
        self.state.tuned_notes = delete_gui_note(current, self.selected_note_index)
        self._select_note(None)
        self._set_display_notes(self.state.tuned_notes)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        self._set_status(f"Deleted MIDI {deleted.pitch}. Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}")

    def _undo_edit(self) -> None:
        restored = self.edit_history.undo(self.state.current_notes)
        if restored is None:
            self._set_status("Nothing to undo.")
            return
        self._restore_notes(restored, "Undid edit")

    def _redo_edit(self) -> None:
        restored = self.edit_history.redo(self.state.current_notes)
        if restored is None:
            self._set_status("Nothing to redo.")
            return
        self._restore_notes(restored, "Redid edit")

    def _restore_notes(self, notes: list[GuiMidiNote], action: str) -> None:
        """Apply an undo/redo result: swap in the notes and refresh everything."""

        if self.state.heatmap is None:
            return
        self.state.tuned_notes = list(notes)
        self._select_note(None)
        self._set_display_notes(self.state.tuned_notes)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        self._set_status(f"{action}. {len(self.state.tuned_notes)} notes.{preview_status}")

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
        # Snapshot the note list once at the start of an edit gesture (before any
        # mutation), so a multi-tick drag records a single undo step for the whole
        # gesture rather than one per intermediate move.
        if self._pre_edit_snapshot is None:
            self._pre_edit_snapshot = current
        self.state.tuned_notes = update_gui_note(
            current,
            index,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            pitch=pitch,
            velocity=velocity,
        )
        self.selected_note_index = index
        if not update_preview:
            # Uncommitted drag: update just the dragged note with a partial
            # repaint and refresh the inspector, skipping the full set_data
            # (canvas resize + full repaint) and the sequence-table rebuild.
            edited_note = self.state.tuned_notes[index]
            self.piano_roll.preview_note_edit(index, edited_note)
            self._populate_note_inspector(edited_note)
            self.selected_note_label.setText(self._note_summary(edited_note))
            self._set_status(f"Editing selected note… release to update MIDI preview. Export writes {len(self.state.tuned_notes)} edited notes.")
            return
        # Committed edit: push the pre-gesture snapshot to the undo history.
        if self._pre_edit_snapshot is not None:
            self.edit_history.record(self._pre_edit_snapshot)
            self._pre_edit_snapshot = None
        self._set_display_notes(self.state.tuned_notes)
        self._select_note(index)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        self._set_status(f"{status_prefix}. Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}")

    def _refresh_midi_preview(self, notes: list[GuiMidiNote]) -> str:
        """Schedule a debounced, off-thread MIDI preview re-render.

        Returns immediately so editing never blocks on TiMidity.  The latest
        edit supersedes earlier pending/in-flight renders; the completion handler
        swaps the player source and restores playback position.
        """

        if not self.render_midi:
            return " MIDI preview rendering disabled."
        self._pending_preview_notes = list(notes)
        # Bump the id now so any in-flight render already dispatched is treated as
        # stale when it finishes.
        self._preview_request_id += 1
        self.preview_debounce_timer.start()
        return " MIDI preview updating…"

    def _preview_paths(self) -> tuple[Path, Path]:
        if self._preview_dir is None:
            self._preview_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-edit-"))
        return self._preview_dir / "edited.mid", self._preview_dir / "edited.wav"

    def _build_preview_request(self, notes: list[GuiMidiNote]) -> MidiPreviewRequest:
        midi_path, wav_path = self._preview_paths()
        return MidiPreviewRequest(
            render_id=self._preview_request_id,
            notes=self._notes_for_midi_preview(notes),
            midi_path=midi_path,
            wav_path=wav_path,
            silent_duration_seconds=max(1.0, self.waveform.duration_seconds()),
        )

    def _start_preview_render(self) -> None:
        if self._pending_preview_notes is None:
            return
        request = self._build_preview_request(self._pending_preview_notes)
        self._pending_preview_notes = None
        thread = QThread(self)
        worker = MidiPreviewWorker(request)
        self.preview_runs.append((thread, worker))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._preview_render_finished)
        worker.failed.connect(self._preview_render_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda thread=thread, worker=worker: self._preview_thread_finished(thread, worker))
        thread.start()

    def _preview_thread_finished(self, thread: QThread, worker: MidiPreviewWorker) -> None:
        self.preview_runs = [(run_thread, run_worker) for run_thread, run_worker in self.preview_runs if run_thread is not thread]
        worker.deleteLater()
        thread.deleteLater()

    def _preview_render_finished(self, result: MidiPreviewResult) -> None:
        if result.render_id != self._preview_request_id:
            return  # superseded by a newer edit; ignore this stale render
        previous_display_seconds = self._display_seconds_from_midi_position(self.midi_player.position())
        self.state.rendered_midi_wav = result.rendered_wav
        self.state.midi_preview_offset_seconds = self.state.analysis_start_seconds if self.state.analysis_duration_seconds is not None else 0.0
        self.midi_player.setSource(QUrl.fromLocalFile(str(result.rendered_wav)))
        if previous_display_seconds > 0:
            self.midi_player.setPosition(self._midi_position_from_display_seconds(previous_display_seconds))
        self.transport.set_playback_available(original=self.state.audio_path is not None, midi=True)
        self._set_status("MIDI preview re-rendered.")

    def _preview_render_failed(self, request: MidiPreviewRequest, message: str) -> None:
        if request.render_id != self._preview_request_id:
            return
        self.state.rendered_midi_wav = None
        self.midi_player.setSource(QUrl())
        self.transport.set_playback_available(original=self.state.audio_path is not None, midi=False)
        self._set_status(f"MIDI preview unavailable: {message}")

    def _flush_preview_render(self) -> None:
        """Render any pending/in-flight preview synchronously (used by tests)."""

        if self.preview_debounce_timer.isActive():
            self.preview_debounce_timer.stop()
        if self._pending_preview_notes is None:
            return
        request = self._build_preview_request(self._pending_preview_notes)
        self._pending_preview_notes = None
        try:
            rendered_wav = render_midi_preview(request)
        except Exception as exc:
            self._preview_render_failed(request, str(exc))
            return
        self._preview_render_finished(MidiPreviewResult(render_id=request.render_id, rendered_wav=rendered_wav))

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
                replace(
                    note,
                    start_seconds=note_start - start,
                    duration_seconds=note_end - note_start,
                )
            )
        return preview_notes

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
        # Refresh the stats strip to describe the newly selected slice.
        self._update_stats(self.state.current_notes)
        self._set_status(f"Selected range {start_seconds:.2f}s–{start_seconds + duration_seconds:.2f}s ({duration_seconds:.2f}s). Click Analyze.")

    def _seek_seconds(self, seconds: float) -> None:
        display_seconds = max(0.0, float(seconds))
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        if self.state.rendered_midi_wav is not None:
            self.midi_player.setPosition(self._midi_position_from_display_seconds(display_seconds))
        if self.playback_timer.isActive():
            self._start_playback_clock(display_seconds)
        self._set_playhead(display_seconds)

    def _apply_playback_mix(self, mode: str) -> None:
        """Balance the two players for the given playback mode.

        In ``both`` mode the busier original tends to mask the rendered MIDI, so
        duck the original and keep the MIDI at full level; solo modes use the
        normal level.
        """

        if mode == "both":
            self.original_audio.setVolume(self.BOTH_ORIGINAL_VOLUME)
            self.midi_audio.setVolume(self.BOTH_MIDI_VOLUME)
        else:
            self.original_audio.setVolume(self.SOLO_VOLUME)
            self.midi_audio.setVolume(self.SOLO_VOLUME)

    def _play_both(self) -> None:
        if self.state.audio_path is None or self.state.rendered_midi_wav is None:
            return
        display_seconds = self._play_start_display_seconds()
        self._apply_playback_mix("both")
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        self.midi_player.setPosition(self._midi_position_from_display_seconds(display_seconds))
        self.playback_mode = "both"
        self._start_playback_clock(display_seconds)
        self._set_playhead(display_seconds)
        self.playback_timer.start()
        self.original_player.play()
        self.midi_player.play()
        self._set_status("Playing original + rendered MIDI")

    def _play_original(self) -> None:
        if self.state.audio_path is None:
            return
        display_seconds = self._play_start_display_seconds()
        self._apply_playback_mix("original")
        self.midi_player.pause()
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        self.playback_mode = "original"
        self._start_playback_clock(display_seconds)
        self._set_playhead(display_seconds)
        self.playback_timer.start()
        self.original_player.play()
        self._set_status("Playing original audio")

    def _play_midi(self) -> None:
        if self.state.rendered_midi_wav is None:
            return
        display_seconds = self._play_start_display_seconds()
        self._apply_playback_mix("midi")
        self.original_player.pause()
        self.original_player.setPosition(self._original_position_from_display_seconds(display_seconds))
        self.midi_player.setPosition(self._midi_position_from_display_seconds(display_seconds))
        self.playback_mode = "midi"
        self._start_playback_clock(display_seconds)
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
        if self.playback_timer.isActive() and self.playback_clock_valid:
            return self._estimated_display_seconds()
        return self._reference_player_display_seconds()

    def _reference_player_display_seconds(self) -> float:
        if self.playback_mode == "midi":
            return self._display_seconds_from_midi_position(self.midi_player.position())
        if self.playback_mode == "both" and self.original_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return self._display_seconds_from_midi_position(self.midi_player.position())
        return self._display_seconds_from_original_position(self.original_player.position())

    def _start_playback_clock(self, display_seconds: float) -> None:
        self.playback_clock_anchor_seconds = max(0.0, float(display_seconds))
        self.playback_clock.restart()
        self.playback_clock_valid = True
        self.playback_sync_ticks = 0
        self.playback_stalled = False

    def _estimated_display_seconds(self) -> float:
        if not self.playback_clock_valid:
            return self._reference_player_display_seconds()
        return self._clamp_to_analysis_range(
            self._interpolated_display_seconds(self.playback_clock_anchor_seconds, self.playback_clock.elapsed())
        )

    @staticmethod
    def _interpolated_display_seconds(anchor_seconds: float, elapsed_ms: int) -> float:
        return max(0.0, float(anchor_seconds)) + max(0.0, float(elapsed_ms)) / 1000.0

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
        if self.state.analysis_duration_seconds is not None:
            start = max(0.0, self.state.analysis_start_seconds)
            return start, start + max(0.0, self.state.analysis_duration_seconds)
        # No analysis yet: fall back to the pending range selected in the controls
        # so playback still stops at the chosen range end before Analyze is run.
        start_seconds, duration_seconds = self.controls.analysis_range()
        if duration_seconds is None or duration_seconds <= 0:
            return None
        start = max(0.0, float(start_seconds))
        return start, start + float(duration_seconds)

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
        self._suppress_reactive_playhead = True
        self.original_player.pause()
        self.midi_player.pause()
        self._suppress_reactive_playhead = False
        self.playback_mode = "paused"
        self.playback_timer.stop()
        self.playback_clock_valid = False
        self.playback_stalled = False
        self._set_playhead(display_seconds)
        self._set_status("Playback paused")

    def _stop_playback(self) -> None:
        self._suppress_reactive_playhead = True
        self.original_player.stop()
        self.midi_player.stop()
        self._suppress_reactive_playhead = False
        self.playback_mode = "stopped"
        self.playback_timer.stop()
        self.playback_clock_valid = False
        self.playback_stalled = False
        self._set_playhead(0.0)
        self._set_status("Playback stopped")

    def _playback_position_changed(self, milliseconds: int) -> None:
        if self._suppress_reactive_playhead:
            return
        if not self.playback_timer.isActive() and self.playback_mode != "midi":
            self._set_playhead(self._display_seconds_from_original_position(milliseconds))

    def _midi_position_changed(self, milliseconds: int) -> None:
        if self._suppress_reactive_playhead:
            return
        if not self.playback_timer.isActive() and self.playback_mode == "midi":
            self._set_playhead(self._display_seconds_from_midi_position(milliseconds))

    def _playback_state_changed(self, _state: QMediaPlayer.PlaybackState) -> None:
        if self._suppress_reactive_playhead:
            return
        if self.original_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if not self.playback_timer.isActive():
                self._start_playback_clock(self._reference_player_display_seconds())
                self.playback_timer.start()
            return
        if self.midi_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if not self.playback_timer.isActive():
                self._start_playback_clock(self._reference_player_display_seconds())
                self.playback_timer.start()
            return
        display_seconds = self._current_display_seconds()
        self.playback_timer.stop()
        self.playback_clock_valid = False
        self._set_playhead(display_seconds)

    def _sync_playback_tick(self) -> None:
        if self.playback_stalled:
            return
        display_seconds = self._estimated_display_seconds()
        self.playback_sync_ticks += 1
        if self.playback_sync_ticks >= self.PLAYBACK_RESYNC_TICKS:
            display_seconds = self._maybe_resync_playback_clock(display_seconds)
            self.playback_sync_ticks = 0
        if self.playback_mode == "both":
            self._sync_player_position(self.midi_player, self._midi_position_from_display_seconds(display_seconds))
        if self._stop_if_past_analysis_end(display_seconds):
            return
        self._set_playhead(display_seconds)

    def _maybe_resync_playback_clock(self, estimated_display_seconds: float) -> float:
        actual_display_seconds = self._reference_player_display_seconds()
        drift = actual_display_seconds - estimated_display_seconds
        if self._resync_drift_needs_correction(drift):
            self._start_playback_clock(actual_display_seconds)
            return actual_display_seconds
        return estimated_display_seconds

    @classmethod
    def _resync_drift_needs_correction(cls, drift_seconds: float) -> bool:
        """Return True only for drift the interpolation clock should chase.

        ``drift_seconds`` is player position minus interpolated estimate.

        * Positive drift (player ahead of us) means true playback jumped forward
          from a seek/underrun; correct once it clears the small ahead tolerance.
        * Small negative drift is ordinary backend position lag; ignore it so the
          playhead never stutters backward.
        * Very large negative drift is a genuine desync (e.g. we ran past the end
          of media while the player rewound), so still correct past the lag cap.
        """

        if drift_seconds > cls.PLAYBACK_RESYNC_AHEAD_TOLERANCE_SECONDS:
            return True
        return drift_seconds < -cls.PLAYBACK_RESYNC_MAX_LAG_SECONDS

    def _media_status_changed(self, _status: QMediaPlayer.MediaStatus) -> None:
        """Freeze the interpolated clock while either player is buffering/stalled.

        Qt Multimedia can pause audio output on a stall without emitting a
        playbackStateChanged transition, so without this the interpolated
        playhead keeps advancing ahead of audio that has actually stopped.
        """

        relevant_statuses = {
            QMediaPlayer.MediaStatus.StalledMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        }
        stalled = (
            self.original_player.mediaStatus() in relevant_statuses
            or self.midi_player.mediaStatus() in relevant_statuses
        )
        if stalled == self.playback_stalled:
            return
        self.playback_stalled = stalled
        if stalled:
            return
        if self.playback_timer.isActive():
            self._start_playback_clock(self._reference_player_display_seconds())

    def _stop_if_past_analysis_end(self, display_seconds: float) -> bool:
        bounds = self._active_analysis_range()
        if bounds is None or self.playback_mode not in {"both", "midi", "original"}:
            return False
        _start, end = bounds
        if display_seconds < end:
            return False
        self._suppress_reactive_playhead = True
        self.original_player.pause()
        self.midi_player.pause()
        self._suppress_reactive_playhead = False
        self.playback_mode = "paused"
        self.playback_timer.stop()
        self.playback_clock_valid = False
        self.playback_stalled = False
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
        self._follow_playhead_in_piano_scroll(clamped_seconds)

    def _follow_playhead_in_piano_scroll(self, seconds: float) -> None:
        """Keep the moving playhead visible when zoomed in past the viewport.

        Only scrolls during active playback and only when the canvas is wider
        than the viewport. Uses a margin band so the scrollbar re-targets when
        the playhead nears an edge instead of moving every frame.
        """

        if not hasattr(self, "piano_scroll") or not self.playback_timer.isActive():
            return
        scrollbar = self.piano_scroll.horizontalScrollBar()
        if scrollbar is None or scrollbar.maximum() <= 0:
            return
        viewport_width = self.piano_scroll.viewport().width()
        if viewport_width <= 0:
            return
        playhead_x = self.piano_roll.x_for_seconds(seconds)
        offset = scrollbar.value()
        visible_left = offset + self.piano_roll.keyboard_width
        right_margin = max(48, viewport_width // 8)
        visible_right = offset + viewport_width - right_margin
        # When the playhead leaves the comfortable band, re-anchor it a quarter
        # of the way in so there is room to advance before the next scroll,
        # avoiding a per-frame scrollbar update.
        if playhead_x < visible_left or playhead_x > visible_right:
            lead_in = max(self.piano_roll.keyboard_width, viewport_width // 4)
            target = playhead_x - lead_in
            scrollbar.setValue(max(0, min(scrollbar.maximum(), target)))

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
