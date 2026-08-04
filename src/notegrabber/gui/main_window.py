"""Main window for the native notegrabber standalone GUI."""

from __future__ import annotations

import pickle
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from notegrabber.analyzer import BackendName
from notegrabber.export import EXPORT_FORMATS, export_notes, format_for_path

from .analysis_worker import AnalysisRequest, AnalysisResult
from .audio_load_worker import OverviewReady, WaveformReady
from .edit_history import EditHistory
from .transcription_stats import compute_stats
from .midi_preview_worker import MidiPreviewRequest, MidiPreviewResult, render_midi_preview
from .process_jobs import JobArtifact, JobProgress, ProcessJob
from .state import GuiMidiNote, ProjectState, add_gui_note, add_gui_notes, audible_gui_notes, delete_gui_notes, gui_notes_to_midi, normalized_gui_note, set_gui_notes_muted, retune_notes_from_heatmap
from .theme import THEMES, active_theme, build_stylesheet, polish_button, set_active_theme
from .widgets.collapsible import CollapsibleSection
from .widgets.controls import AnalysisControls
from .widgets.piano_roll import PianoRollWidget
from .widgets.sequence import SequenceWidget
from .widgets.transport import TransportWidget
from .widgets.waveform import WaveformWidget


class MainWindow(QMainWindow):
    """NeuralNote-inspired standalone app shell."""

    # Keep windows whose first close was deferred alive until their child
    # processes report NotRunning; otherwise Python GC can destroy the QProcess
    # parent while cancellation is still in flight.
    _deferred_close_windows: list["MainWindow"] = []

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
        self.analysis_job: ProcessJob | None = None
        self._analysis_generation = 0
        # One decode-once process per open feeds both the waveform preview and
        # overview (issue #33), isolated so it can be cancelled safely.
        self.audio_load_job: ProcessJob | None = None
        self._audio_load_generation = 0
        self._stale_jobs: list[ProcessJob] = []
        self._analysis_settings_by_generation: dict[int, tuple[BackendName, float, float, float, float]] = {}
        self.selected_note_index: int | None = None
        # Mirrors the piano roll's selection set; selected_note_index stays as
        # the "exactly one note" view of it for the inspector (issue #35).
        self.selected_indices: set[int] = set()
        # Copied notes, stored relative to the earliest one in *both* time and
        # pitch, so a paste can re-anchor the whole pattern anywhere in the roll
        # while keeping its rhythm and its intervals (issue #63).
        self._clipboard_notes: list[GuiMidiNote] = []
        # The pitch the buffer was copied from, used when a paste has no anchor
        # pitch to transpose onto (pointer outside the roll).
        self._clipboard_root_pitch: int = 60
        # Audio-derived tempo and beat positions from the last analysis, set by
        # the worker where librosa can run off the UI thread (issue #14).
        self._audio_tempo_bpm: float | None = None
        self._beat_times: tuple[float, ...] = ()
        self.playback_mode = "stopped"
        # The last mode actually played, kept across pause/stop so Space can
        # resume what was playing rather than guessing (issue #64).
        self.last_playback_mode = "both"
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
        self.preview_jobs: list[tuple[int, ProcessJob]] = []
        self._preview_dir: Path | None = None
        self._retired_preview_dirs: list[Path] = []
        # The temp work dir of the current analysis result (heatmap.json, the
        # rendered preview WAV, range WAVs). Removed once a newer analysis
        # supersedes it or on close, so re-analyses do not pile up in /tmp.
        self._analysis_dir: Path | None = None
        self._preview_request_id = 0
        self._pending_preview_notes: list[GuiMidiNote] | None = None
        self.preview_debounce_timer = QTimer(self)
        self.preview_debounce_timer.setSingleShot(True)
        self.preview_debounce_timer.setInterval(250)
        self._closing = False
        self._final_close = False

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
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("jobProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(6)
        # The bar stays laid out for the window's whole life and is only styled
        # invisible when idle. Hiding it collapsed its row and shifted the
        # waveform and piano roll up/down on every job, which made editing notes
        # jumpy -- the click target moved mid-gesture.
        self._set_progress_idle(True)
        # File name shown as a prefix on the stats strip (no longer its own row).
        self._current_file_name = ""
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
        # A separate player for note audition (issue #67), so previewing a note
        # never disturbs the transport's position or playback state.
        self.audition_audio = QAudioOutput(self)
        self.audition_player = QMediaPlayer(self)
        self.audition_player.setAudioOutput(self.audition_audio)
        self.audition_audio.setVolume(self.SOLO_VOLUME)
        self.audition_enabled = True
        self._audition_dir: Path | None = None
        self._audition_generation = 0
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

        # Restore the persisted theme (default when unset/unknown) before styling
        # so the first paint uses it.
        self._settings = QSettings("tonetrace", "tonetrace")
        saved_theme = self._settings.value("theme", "default")
        set_active_theme(saved_theme if saved_theme in THEMES else "default")

        self._build_layout()
        self._connect_signals()
        # Reflect the restored theme in the combo (without re-emitting).
        self.controls.set_theme(active_theme().id)
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
        # Copy/paste/duplicate the note selection (issue #63).
        copy = QShortcut(QKeySequence.StandardKey.Copy, self)  # Ctrl+C
        copy.activated.connect(self._copy_selected_notes)
        paste = QShortcut(QKeySequence.StandardKey.Paste, self)  # Ctrl+V
        paste.activated.connect(self._paste_notes)
        duplicate = QShortcut(QKeySequence("Ctrl+D"), self)
        duplicate.activated.connect(self._duplicate_selected_notes)
        # Transport shortcuts (issue #64). Guarded so they never fire while a
        # text/number field has focus, where Space and digits must type.
        transport = (
            ("Space", self._toggle_playback),
            ("1", lambda: self._play_mode("original")),
            ("2", lambda: self._play_mode("midi")),
            ("3", lambda: self._play_mode("both")),
            ("0", self._stop_playback),
            ("Esc", self._stop_playback),
        )
        for sequence, handler in transport:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self._guarded_transport(handler))

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
        self._preview_request_id += 1
        for _generation, job in list(self.preview_jobs):
            job.cancel()
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
        # The file name folds into the stats strip; keep the full path as its
        # tooltip so it is still discoverable without its own row.
        self._current_file_name = path.name
        self.file_label.setText(f"{path.name} — {path}")
        self.stats_label.setToolTip(f"{path}\n\nTranscription summary: file · note count · duration · estimated tempo · detected key.")
        self._update_stats([])
        self.original_player.setSource(QUrl.fromLocalFile(str(path)))
        self.midi_player.setSource(QUrl())
        self._retry_retired_preview_dirs()
        self.waveform.set_message("Loading waveform preview…")
        self._start_audio_load(path)
        self._set_status("Audio loaded. Loading waveform and overview in background…")
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
        # --- Top block: actions + playback, the single status line, then the stats
        # strip. The status line lives HERE (in the always-visible top block), not
        # in the QMainWindow status bar. A bottom status bar can be clipped when the
        # window is taller than the screen; keeping status in the top block (and
        # making the left column scroll, below) guarantees it is always on screen.
        # One status home, guaranteed visible.
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        # Action buttons and the Playback group share one row to save vertical space.
        action_row = QWidget()
        action_row_layout = QHBoxLayout(action_row)
        action_row_layout.setContentsMargins(0, 0, 0, 0)
        action_row_layout.setSpacing(10)
        action_row_layout.addWidget(self.controls.build_action_bar())
        action_row_layout.addStretch(1)
        action_row_layout.addWidget(self.transport.playback_group)
        top_layout.addWidget(action_row)
        # The one status line: themed LED strip, always on screen.
        top_layout.addWidget(self.transport.status_label)
        # The progress bar sits in a slot of fixed height so the row is reserved
        # whether or not a job is running; see _set_progress_idle.
        progress_slot = QWidget()
        progress_slot.setFixedHeight(self.progress_bar.maximumHeight())
        progress_slot_layout = QVBoxLayout(progress_slot)
        progress_slot_layout.setContentsMargins(0, 0, 0, 0)
        progress_slot_layout.setSpacing(0)
        progress_slot_layout.addWidget(self.progress_bar)
        top_layout.addWidget(progress_slot)
        # The loaded file name folds into the stats strip rather than owning its own
        # row (it is reference info, not a section). The waveform sits below it.
        self.stats_label = QLabel("Notes 0  ·  0:00  ·  — BPM  ·  —")
        self.stats_label.setObjectName("statsStrip")
        self.stats_label.setToolTip("Transcription summary: file · note count · duration · estimated tempo · detected key. Drag a range on the waveform to scope it to that slice (shown as 'Selection:'). Updates after Analyze and edits.")
        top_layout.addWidget(self.stats_label)
        top_layout.addWidget(self.waveform)

        self.piano_scroll = QScrollArea()
        self.piano_scroll.setWidgetResizable(True)
        self.piano_scroll.setWidget(self.piano_roll)
        self.piano_scroll.setMinimumHeight(200)
        self.piano_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Repaint the whole canvas as it scrolls horizontally so the pinned
        # keyboard (drawn at the visible left edge) does not smear or ghost.
        self.piano_scroll.horizontalScrollBar().valueChanged.connect(self.piano_roll.update)

        # The heatmap/roll pane: its section label plus the scrollable canvas.
        roll_pane = QWidget()
        roll_layout = QVBoxLayout(roll_pane)
        roll_layout.setContentsMargins(0, 0, 0, 0)
        roll_layout.setSpacing(6)
        roll_layout.addWidget(self._section_label("Heatmap + MIDI note map"))
        roll_layout.addWidget(self.piano_scroll, 1)

        # The detail pane: the always-visible note inspector, then the collapsible
        # detected-notes table docked underneath (collapsed by default so the roll
        # owns the height until the list is wanted).
        detail_pane = QWidget()
        detail_layout = QVBoxLayout(detail_pane)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(self._build_note_inspector())
        self.sequence_section = CollapsibleSection("Detected notes", self.sequence, expanded=False)
        self.sequence.count_changed.connect(self._on_sequence_count_changed)
        detail_layout.addWidget(self.sequence_section, 1)

        # A vertical splitter lets the roll and the detail pane be resized against
        # each other (and the detail pane collapse to just its header row), so the
        # note list is always reachable without ever pushing the roll off-screen.
        content_splitter = QSplitter(Qt.Orientation.Vertical)
        content_splitter.addWidget(roll_pane)
        content_splitter.addWidget(detail_pane)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setChildrenCollapsible(False)
        self.content_splitter = content_splitter

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(10)
        right_layout.addWidget(top)
        right_layout.addWidget(content_splitter, 1)

        # Wrap the left control column in a scroll area so its tall stack of groups
        # cannot force the whole window taller than a laptop screen (which was
        # pushing the bottom of the app off-screen). It scrolls when space is tight.
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setWidget(self.controls)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        controls_scroll.setMinimumWidth(260)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(controls_scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1000])
        self.setCentralWidget(splitter)
        self._update_heatmap_view_height()
        # The visible status is the top-block strip; the QMainWindow status bar is
        # kept (tests read currentMessage()) but hidden so status shows in exactly
        # one place and cannot be clipped when the window exceeds the screen height.
        self.statusBar().showMessage("Ready")
        self.statusBar().hide()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _connect_signals(self) -> None:
        self.controls.open_requested.connect(self._open_audio_dialog)
        self.controls.analyze_requested.connect(self._start_analysis)
        self.controls.cancel_requested.connect(self._cancel_current_job)
        self.controls.export_requested.connect(self._export_midi_dialog)
        self.controls.delete_requested.connect(self._delete_selected_note)
        self.controls.fit_requested.connect(self._fit_zoom)
        self.controls.reset_zoom_requested.connect(self._reset_zoom)
        self.controls.retune_requested.connect(self._retune_from_controls)
        self.controls.overlay_toggled.connect(self.piano_roll.set_show_notes)
        self.controls.heatmap_toggled.connect(self.piano_roll.set_show_heatmap)
        self.controls.pitch_bends_toggled.connect(self.piano_roll.set_show_pitch_bends)
        self.controls.audition_toggled.connect(self.set_audition_enabled)
        self.controls.theme_changed.connect(self._on_theme_changed)
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
        # Audition is wired to note_selected (a real click on a note) rather than
        # selection_changed, which also fires for nudges, pastes and rubber-band
        # sweeps and would retrigger audio constantly (issue #67).
        self.piano_roll.note_selected.connect(self._audition_selected_index)
        self.piano_roll.note_edited.connect(self._edit_note_from_piano_roll)
        self.piano_roll.note_created.connect(self._create_note_from_piano_roll)
        self.piano_roll.selection_changed.connect(self._on_selection_changed)
        self.piano_roll.notes_edited.connect(self._edit_notes_from_piano_roll)
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

    def _start_audio_load(self, path: Path) -> None:
        """Decode the file once and feed both the waveform preview and overview."""

        self._audio_load_generation += 1
        generation = self._audio_load_generation
        if self.audio_load_job is not None:
            self._stale_jobs.append(self.audio_load_job)
            self.audio_load_job.cancel()
        job = ProcessJob("audio-load", parent=self)
        job.set_request({"audio_path": path, "sample_rate": 8000})
        self.audio_load_job = job
        job.progress.connect(lambda progress, generation=generation, job=job: self._audio_load_progress(generation, job, progress))
        job.artifact.connect(lambda artifact, generation=generation, job=job: self._audio_load_artifact(generation, job, artifact))
        job.stage_failed.connect(lambda stage, message, generation=generation: self._audio_load_stage_failed(generation, stage, message))
        job.failed.connect(lambda message, generation=generation: self._audio_load_process_failed(generation, path, message))
        job.cancelled.connect(lambda generation=generation: self._audio_load_cancelled(generation))
        job.done.connect(lambda generation=generation, job=job: self._audio_load_done(generation, job))
        self._refresh_job_ui()
        job.start()

    def _waveform_ready(self, result: WaveformReady) -> None:
        if self.state.audio_path != result.audio_path:
            return
        self.waveform.set_preview(
            result.samples,
            sample_rate=result.sample_rate,
            duration_seconds=result.duration_seconds,
        )
        self.controls.set_audio_duration(result.duration_seconds)
        if self.analysis_job is None and not self._maybe_enable_default_range_for_long_audio(result.duration_seconds):
            self._set_status("Waveform preview ready. Drag to choose a range or click Analyze.")

    def _overview_ready(self, result: OverviewReady) -> None:
        if self.state.audio_path != result.audio_path:
            return
        self.waveform.set_pitch_overview(result.overview)
        if self.analysis_job is None:
            self._set_status("Low-resolution pitch overview ready. Drag waveform to choose a range, then Analyze.")

    def _audio_load_failed(self, audio_path: Path, stage: str, message: str) -> None:
        if self.state.audio_path != audio_path:
            return
        if stage == "waveform":
            self.waveform.set_message("Waveform preview unavailable")
            self._set_status(f"Audio loaded, but waveform preview failed: {message}")
        else:
            self.waveform.set_pitch_overview(None)
            self._set_status(f"Pitch overview unavailable: {message}")

    def _audio_load_progress(self, generation: int, job: ProcessJob, progress: JobProgress) -> None:
        if generation != self._audio_load_generation or job is not self.audio_load_job:
            return
        self._job_progress(progress, priority="audio-load")

    def _audio_load_artifact(self, generation: int, job: ProcessJob, artifact: JobArtifact) -> None:
        if self._closing or generation != self._audio_load_generation or job is not self.audio_load_job:
            return
        try:
            with artifact.path.open("rb") as handle:
                result = pickle.load(handle)
            artifact.path.unlink(missing_ok=True)
        except Exception as exc:
            self._audio_load_stage_failed(generation, artifact.name, str(exc))
            return
        if artifact.name == "waveform":
            if not isinstance(result, WaveformReady):
                self._audio_load_stage_failed(generation, artifact.name, "waveform artifact had an unexpected type")
                return
            self._waveform_ready(result)
        elif artifact.name == "overview":
            if not isinstance(result, OverviewReady):
                self._audio_load_stage_failed(generation, artifact.name, "overview artifact had an unexpected type")
                return
            self._overview_ready(result)

    def _audio_load_stage_failed(self, generation: int, stage: str, message: str) -> None:
        if self._closing or generation != self._audio_load_generation or self.state.audio_path is None:
            return
        self._audio_load_failed(self.state.audio_path, stage, message)

    def _audio_load_process_failed(self, generation: int, path: Path, message: str) -> None:
        if self._closing or generation != self._audio_load_generation:
            return
        if self.state.audio_path != path:
            return
        self.waveform.set_message("Waveform preview unavailable")
        self.waveform.set_pitch_overview(None)
        self._set_status(f"Audio loading failed: {message}")

    def _audio_load_cancelled(self, generation: int) -> None:
        if generation != self._audio_load_generation:
            return
        self._set_status("Audio loading cancelled.")

    def _audio_load_done(self, generation: int, job: ProcessJob) -> None:
        if job is self.audio_load_job:
            self.audio_load_job = None
        self._stale_jobs = [stale for stale in self._stale_jobs if stale is not job]
        self._refresh_job_ui()
        self._maybe_finish_deferred_close()

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
        if self.analysis_job is not None:
            return

        backend: BackendName = self.controls.backend()  # type: ignore[assignment]
        threshold = self.controls.threshold()
        onset_threshold = self.controls.onset_threshold()
        frame_threshold = self.controls.frame_threshold()
        min_duration = self.controls.min_duration_seconds()

        range_start_seconds, range_duration_seconds = self.controls.analysis_range()
        request = AnalysisRequest(
            audio_path=self.state.audio_path,
            backend=backend,
            render_midi=self.render_midi,
            threshold=threshold,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            min_duration_seconds=min_duration,
            range_start_seconds=range_start_seconds,
            range_duration_seconds=range_duration_seconds,
        )
        self._analysis_generation += 1
        generation = self._analysis_generation
        self._analysis_settings_by_generation[generation] = (backend, threshold, onset_threshold, frame_threshold, min_duration)
        job = ProcessJob("analysis", parent=self)
        job.set_request(request)
        self.analysis_job = job
        job.progress.connect(lambda progress, generation=generation, job=job: self._analysis_progress(generation, job, progress))
        job.succeeded.connect(lambda result, generation=generation, job=job: self._analysis_process_finished(generation, job, result))
        job.failed.connect(lambda message, generation=generation: self._analysis_failed(message, generation=generation))
        job.cancelled.connect(lambda generation=generation: self._analysis_cancelled(generation))
        job.done.connect(lambda generation=generation, job=job: self._analysis_job_done(generation, job))
        self._refresh_job_ui()
        if range_duration_seconds is not None:
            self._set_status(f"Analyzing {range_duration_seconds:.1f}s range from {range_start_seconds:.1f}s with {backend}…")
        else:
            self._set_status(f"Analyzing full audio with {backend}…")
        job.start()

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
        # Audio-derived tempo/beats from this analysis (issue #14). Cleared and
        # replaced on every analysis so a stale tempo never outlives its audio.
        self._audio_tempo_bpm = result.audio_tempo_bpm
        self._beat_times = result.beat_times
        self._select_note(None)
        self._set_display_notes(result.notes)
        if result.rendered_midi_wav is not None:
            self.midi_player.setSource(QUrl.fromLocalFile(str(result.rendered_midi_wav)))
        else:
            self.midi_player.setSource(QUrl())
        # The player now points at the new result's WAV (or nothing), so the
        # previous analysis's work dir is no longer referenced -- remove it before
        # adopting the new one. Skip if a re-analysis reused the same dir.
        if self._analysis_dir is not None and self._analysis_dir != result.work_dir:
            shutil.rmtree(self._analysis_dir, ignore_errors=True)
        self._analysis_dir = result.work_dir
        self.transport.set_playback_available(original=True, midi=result.rendered_midi_wav is not None)
        message = f"Analyzed {len(result.notes)} notes with {result.backend}."
        if result.render_error:
            message += f" MIDI preview unavailable: {result.render_error}"
        self._set_status(message)

    def _analysis_progress(self, generation: int, job: ProcessJob, progress: JobProgress) -> None:
        if generation != self._analysis_generation or job is not self.analysis_job:
            return
        self._job_progress(progress, priority="analysis")

    def _analysis_process_finished(self, generation: int, job: ProcessJob, result: AnalysisResult) -> None:
        if self._closing or generation != self._analysis_generation or job is not self.analysis_job:
            return
        if not isinstance(result, AnalysisResult):
            self._analysis_failed("analysis result had an unexpected type", generation=generation)
            return
        settings = self._analysis_settings_by_generation.get(generation)
        if settings is not None:
            backend, threshold, onset_threshold, frame_threshold, min_duration = settings
            self.state.backend = backend
            self.state.threshold = threshold
            self.state.onset_threshold = onset_threshold
            self.state.frame_threshold = frame_threshold
            self.state.min_duration = min_duration
        job.take_work_dir()
        self._analysis_finished(result)

    def _analysis_failed(self, message: str, *, generation: int | None = None) -> None:
        if self._closing or (generation is not None and generation != self._analysis_generation):
            return
        QMessageBox.critical(self, "Analysis failed", message)
        self._set_status(f"Analysis failed: {message}")

    def _analysis_cancelled(self, generation: int) -> None:
        if generation != self._analysis_generation:
            return
        self._set_status("Analysis cancelled. Previous transcription preserved.")

    def _analysis_job_done(self, generation: int, job: ProcessJob) -> None:
        if job is self.analysis_job:
            self.analysis_job = None
        for old_generation in list(self._analysis_settings_by_generation):
            if old_generation <= generation:
                self._analysis_settings_by_generation.pop(old_generation, None)
        self._refresh_job_ui()
        self._maybe_finish_deferred_close()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._update_heatmap_view_height()
        super().resizeEvent(event)

    def _update_heatmap_view_height(self) -> None:
        # The vertical splitter now governs how height is shared between the roll
        # and the (collapsed-by-default) detail pane, so the roll no longer needs a
        # tight cap. Keep only a generous ceiling so it does not grow unbounded on
        # a very tall monitor.
        if hasattr(self, "piano_scroll"):
            self.piano_scroll.setMaximumHeight(max(360, round(self.height() * 0.72)))

    # Nudge steps (issue #65). Time is a fixed musical step rather than the
    # heatmap grid: _grid_interval_seconds returns whole seconds (>= 1s), far too
    # coarse to fine-tune a note with.
    NUDGE_SECONDS = 0.05
    NUDGE_SECONDS_FINE = 0.01
    NUDGE_SECONDS_COARSE = 0.25
    NUDGE_VELOCITY = 1
    NUDGE_VELOCITY_LARGE = 10

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.handle_piano_roll_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def handle_piano_roll_key(self, event) -> bool:
        """Handle an editing key, returning True when it was consumed.

        Called both from this window and from the piano roll itself: the roll
        sits in a QScrollArea that would otherwise swallow the arrow keys to
        scroll, so it offers keys here first and falls back to scrolling for
        anything not claimed (issue #65).
        """

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_note()
            return True
        if event.key() == Qt.Key.Key_M and not event.modifiers():
            return self._toggle_selection_muted()
        return self._nudge_from_key(event)

    def _toggle_selection_muted(self) -> bool:
        """M: mute or unmute the selection as one undo step (issue #66).

        Mixed selections mute wholesale rather than flipping each note, which is
        what "mute this lot" means; pressing M again unmutes them all.
        """

        if self.state.heatmap is None or not self.selected_indices:
            return False
        current = list(self.state.current_notes)
        indices = {index for index in self.selected_indices if 0 <= index < len(current)}
        if not indices:
            return False
        muted = not all(current[index].muted for index in indices)
        self.edit_history.record(current)
        self.state.tuned_notes = set_gui_notes_muted(current, indices, muted)
        self._set_display_notes(self.state.tuned_notes)
        self.piano_roll.set_selected_indices(indices)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        count = len(indices)
        plural = "note" if count == 1 else "notes"
        action = "Muted" if muted else "Unmuted"
        audible = len(audible_gui_notes(self.state.tuned_notes))
        audible_plural = "note" if audible == 1 else "notes"
        self._set_status(
            f"{action} {count} {plural}. "
            f"Export writes {audible} audible {audible_plural}.{preview_status}"
        )
        return True

    def _nudge_from_key(self, event) -> bool:
        """Nudge the selection with the arrow keys or +/- (issue #65).

        Returns True when the key was consumed. Arrows move time and pitch,
        +/- adjust velocity; Shift is finer/larger and Ctrl is coarser/octave.
        """

        if self.state.heatmap is None or not self.selected_indices:
            return False
        key = event.key()
        modifiers = event.modifiers()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if ctrl:
                step = self.NUDGE_SECONDS_COARSE
            elif shift:
                step = self.NUDGE_SECONDS_FINE
            else:
                step = self.NUDGE_SECONDS
            direction = -1.0 if key == Qt.Key.Key_Left else 1.0
            return self._nudge_selection(delta_seconds=direction * step)

        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 12 if ctrl else 1
            direction = 1 if key == Qt.Key.Key_Up else -1
            return self._nudge_selection(delta_pitch=direction * step)

        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            step = self.NUDGE_VELOCITY_LARGE if shift else self.NUDGE_VELOCITY
            direction = -1 if key == Qt.Key.Key_Minus else 1
            return self._nudge_selection(delta_velocity=direction * step)

        return False

    def _nudge_selection(
        self,
        *,
        delta_seconds: float = 0.0,
        delta_pitch: int = 0,
        delta_velocity: int = 0,
    ) -> bool:
        """Apply one nudge to every selected note as a single undo step.

        Deltas are clamped as a *group* by the most-constrained member, matching
        the group drag in #36, so a selection meeting a boundary keeps its
        relative spacing and intervals instead of collapsing.
        """

        notes = self.state.current_notes
        indices = sorted(index for index in self.selected_indices if 0 <= index < len(notes))
        if not indices:
            return False
        chosen = [notes[index] for index in indices]

        if delta_seconds:
            earliest = min(note.start_seconds for note in chosen)
            delta_seconds = max(delta_seconds, -earliest)
        if delta_pitch:
            floor_pitch, ceiling_pitch = self.piano_roll._drawable_pitch_range()
            lowest = min(note.pitch for note in chosen)
            highest = max(note.pitch for note in chosen)
            delta_pitch = max(delta_pitch, floor_pitch - lowest)
            delta_pitch = min(delta_pitch, ceiling_pitch - highest)
        if delta_velocity:
            lowest_velocity = min(note.velocity for note in chosen)
            highest_velocity = max(note.velocity for note in chosen)
            delta_velocity = max(delta_velocity, 1 - lowest_velocity)
            delta_velocity = min(delta_velocity, 127 - highest_velocity)

        if not delta_seconds and not delta_pitch and not delta_velocity:
            # Already hard against a boundary: nothing to do, but the key was
            # still ours, so do not let it scroll the view.
            return True

        edits = [
            (
                index,
                max(0.0, note.start_seconds + delta_seconds),
                note.duration_seconds,
                note.pitch + delta_pitch,
                note.velocity + delta_velocity,
            )
            for index, note in zip(indices, chosen)
        ]
        # Reuses the batch path from #36, so this is one undo step and the
        # sequence table, inspector and MIDI preview all refresh.
        self._edit_notes_from_piano_roll(edits, True, verb="Nudged")
        self.piano_roll.set_selected_indices(set(indices))
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._final_close:
            self._cleanup_owned_dirs()
            if self in self._deferred_close_windows:
                self._deferred_close_windows.remove(self)
            super().closeEvent(event)
            return
        self._closing = True
        self._audio_load_generation += 1
        self._analysis_generation += 1
        self._preview_request_id += 1
        self.preview_debounce_timer.stop()
        self._stop_playback()
        self.midi_player.stop()
        self.midi_player.setSource(QUrl())
        self._cancel_all_jobs()
        self._refresh_job_ui()
        self._pump_job_shutdown_events(250)
        if self._has_active_jobs():
            event.ignore()
            if self not in self._deferred_close_windows:
                self._deferred_close_windows.append(self)
            self.hide()
            self._show_progress(indeterminate=True)
            self._set_status("Stopping background jobs…")
            return
        self._final_close = True
        self._cleanup_owned_dirs()
        super().closeEvent(event)

    def _cancel_current_job(self) -> None:
        cancelled = False
        if self.analysis_job is not None:
            self.analysis_job.cancel()
            cancelled = True
        if self.audio_load_job is not None:
            self.audio_load_job.cancel()
            cancelled = True
        if cancelled:
            self._show_progress(indeterminate=True)
            self._set_status("Cancelling background job…")
            self._refresh_job_ui()
        else:
            self._set_status("No cancellable job is running.")

    def _cancel_all_jobs(self) -> None:
        if self.analysis_job is not None:
            self.analysis_job.cancel()
        if self.audio_load_job is not None:
            self.audio_load_job.cancel()
        for _generation, job in list(self.preview_jobs):
            job.cancel()
        for job in list(self._stale_jobs):
            job.cancel()

    def _has_active_jobs(self) -> bool:
        jobs = [self.analysis_job, self.audio_load_job, *self._stale_jobs, *(job for _generation, job in self.preview_jobs)]
        return any(job is not None and not job.is_finished for job in jobs)

    def _pump_job_shutdown_events(self, timeout_ms: int) -> None:
        deadline = QElapsedTimer()
        deadline.start()
        while self._has_active_jobs() and deadline.elapsed() < timeout_ms:
            QApplication.processEvents()

    def _maybe_finish_deferred_close(self) -> None:
        if not self._closing or self._has_active_jobs():
            return
        self._final_close = True
        self._hide_progress()
        QTimer.singleShot(0, self.close)

    def _cleanup_owned_dirs(self) -> None:
        if self._preview_dir is not None:
            self._retire_preview_dir(self._preview_dir)
            self._preview_dir = None
        self._retry_retired_preview_dirs()
        if self._analysis_dir is not None:
            shutil.rmtree(self._analysis_dir, ignore_errors=True)
            self._analysis_dir = None
        if self._audition_dir is not None:
            # Release the file the player may still hold before removing the dir.
            self.audition_player.stop()
            self.audition_player.setSource(QUrl())
            shutil.rmtree(self._audition_dir, ignore_errors=True)
            self._audition_dir = None

    def _retire_preview_dir(self, path: Path) -> None:
        if self._try_remove_dir(path):
            return
        if path not in self._retired_preview_dirs:
            self._retired_preview_dirs.append(path)

    def _retry_retired_preview_dirs(self) -> None:
        self._retired_preview_dirs = [path for path in self._retired_preview_dirs if not self._try_remove_dir(path)]

    @staticmethod
    def _try_remove_dir(path: Path) -> bool:
        if not path.exists():
            return True
        try:
            shutil.rmtree(path)
        except OSError:
            return False
        return not path.exists()

    def _refresh_job_ui(self) -> None:
        analysis_running = self.analysis_job is not None and not self.analysis_job.is_finished
        cancellable_running = analysis_running or (self.audio_load_job is not None and not self.audio_load_job.is_finished)
        self.controls.set_busy(analysis_running)
        self.controls.set_cancellable(cancellable_running)
        if self._has_active_jobs():
            self._set_progress_idle(False)
        else:
            self._hide_progress()

    def _job_progress(self, progress: JobProgress, *, priority: str) -> None:
        if priority == "preview" and (self.analysis_job is not None or self.audio_load_job is not None):
            return
        if priority == "audio-load" and self.analysis_job is not None:
            return
        if progress.total is not None and progress.total > 0 and progress.completed is not None:
            self.progress_bar.setRange(0, int(progress.total))
            self.progress_bar.setValue(max(0, min(int(progress.completed), int(progress.total))))
            self._set_progress_idle(False)
        else:
            self._show_progress(indeterminate=True)
        self._set_status(progress.message)

    def _set_progress_idle(self, idle: bool) -> None:
        """Show or hide the progress bar *without* changing the layout.

        The bar lives in a fixed-height slot that stays in the layout whether or
        not the bar is drawn, so nothing below it -- including the piano roll --
        moves when a job starts or finishes.

        The bar's own visibility is what toggles. Restyling it to be transparent
        instead does not work: once the MainWindow's themed stylesheet has
        applied, re-setting the child's own stylesheet is ignored, leaving the
        previous groove and chunk painted on screen.
        """

        if self.progress_bar.property("idle") == idle:
            return
        self.progress_bar.setProperty("idle", idle)
        self.progress_bar.setVisible(not idle)

    def _show_progress(self, *, indeterminate: bool) -> None:
        if indeterminate:
            self.progress_bar.setRange(0, 0)
        self._set_progress_idle(False)

    def _hide_progress(self) -> None:
        if not self._has_active_jobs():
            self._set_progress_idle(True)
            # Reset *after* going idle: an indeterminate bar (range 0,0) keeps
            # animating a chunk, and a determinate one leaves its last fill
            # painted, so the groove must also be emptied to actually go blank.
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)

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
            stats = compute_stats(
                notes,
                duration_seconds=self.waveform.duration_seconds(),
                audio_tempo_bpm=self._audio_tempo_bpm,
            )
        text = stats.strip_text()
        if self._current_file_name:
            text = f"{self._current_file_name}  ·  {text}"
        self.stats_label.setText(text)

    def _select_note(self, index: int | None, _seek_seconds: float | None = None) -> None:
        """Select exactly one note, or clear the selection."""

        notes = self.state.current_notes
        valid = index if index is not None and 0 <= index < len(notes) else None
        self.piano_roll.set_selected_note_index(valid)
        self._sync_selection_ui({valid} if valid is not None else set())

    def _on_selection_changed(self, indices: set[int]) -> None:
        """Mirror a selection set coming from the piano roll (issue #35)."""

        self._sync_selection_ui(set(indices))

    def _sync_selection_ui(self, indices: set[int]) -> None:
        """Drive the inspector, label and delete button from a selection set.

        The single-note inspector only makes sense for exactly one note, so it
        is disabled for a multi-selection; delete works for any non-empty set.
        """

        notes = self.state.current_notes
        self.selected_indices = {index for index in indices if 0 <= index < len(notes)}
        single = next(iter(self.selected_indices)) if len(self.selected_indices) == 1 else None
        self.selected_note_index = single
        self.controls.set_can_delete(bool(self.selected_indices))
        self._set_note_inspector_enabled(single is not None)
        if single is not None:
            note = notes[single]
            self._populate_note_inspector(note)
            self.selected_note_label.setText(self._note_summary(note))
        elif self.selected_indices:
            self.selected_note_label.setText(f"{len(self.selected_indices)} notes selected")
        else:
            self.selected_note_label.setText("No note selected")

    def _create_note_from_piano_roll(
        self,
        start_seconds: float,
        duration_seconds: float,
        pitch: int,
        velocity: int,
    ) -> None:
        """Insert a note double-clicked into empty piano-roll space (issue #37).

        One undoable step: edit_history snapshots the whole list, so undo removes
        the note for free.  The new note is selected so it can be tweaked in the
        inspector straight away.
        """

        if self.state.heatmap is None:
            return
        current = list(self.state.current_notes)
        self.edit_history.record(current)
        created = GuiMidiNote(
            pitch=int(pitch),
            start_seconds=float(start_seconds),
            duration_seconds=float(duration_seconds),
            velocity=int(velocity),
            source="manual",
        )
        self.state.tuned_notes, index = add_gui_note(current, created)
        self._set_display_notes(self.state.tuned_notes)
        self._select_note(index)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        self._set_status(
            f"Added MIDI {created.pitch} at {created.start_seconds:.2f}s. "
            f"Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}"
        )

    # Small nudge so a duplicate does not land exactly on top of its source,
    # where it would be invisible and impossible to grab.
    DUPLICATE_OFFSET_SECONDS = 0.25

    def _audition_selected_index(self, index: int, _start_seconds: float = 0.0) -> None:
        """Audition the note a click just selected."""

        notes = self.state.current_notes
        if 0 <= index < len(notes):
            self._audition_note(notes[index])

    def _audition_note(self, note: GuiMidiNote) -> None:
        """Play one note in isolation so it can be judged by ear (issue #67).

        Uses the built-in synth rather than slicing the rendered preview WAV:
        the preview is optional (it needs TiMidity++ or a completed native
        render, and may be stale mid-edit), while the synth is always available
        and gives the same voice. A dedicated player keeps this off the
        transport, so auditioning never disturbs playback position or state.
        """

        if not self.audition_enabled or self.state.heatmap is None:
            return
        # Never audition mid-drag: a drag emits an edit per tick, which would
        # retrigger continuously. Keyed on drag_has_moved rather than drag_mode,
        # because a plain click arms drag_mode *before* emitting note_selected --
        # testing drag_mode would suppress the very click meant to audition.
        if self.piano_roll.drag_has_moved:
            return
        try:
            from notegrabber.native_synth import render_note_to_wav
        except Exception:
            return

        if self._audition_dir is None:
            self._audition_dir = Path(tempfile.mkdtemp(prefix="notegrabber-audition-"))
        # A fresh filename per audition: QMediaPlayer caches by URL, so reusing
        # one path can replay stale audio.
        self._audition_generation += 1
        wav_path = self._audition_dir / f"note-{self._audition_generation}.wav"
        rendered, _error = render_note_to_wav(
            wav_path,
            pitch=note.pitch,
            duration_seconds=note.duration_seconds,
            velocity=note.velocity,
            bend_semitones=note.pitch_bends,
        )
        if rendered is None:
            return
        self.audition_player.stop()
        self.audition_player.setSource(QUrl.fromLocalFile(str(rendered)))
        self.audition_player.setPosition(0)
        self.audition_player.play()
        self._prune_audition_files(keep=wav_path)

    def _prune_audition_files(self, *, keep: Path) -> None:
        """Drop previous audition WAVs, leaving the one now playing."""

        if self._audition_dir is None:
            return
        for path in self._audition_dir.glob("note-*.wav"):
            if path == keep:
                continue
            try:
                path.unlink()
            except OSError:
                # Still held by the player on some backends; the directory is
                # removed wholesale at shutdown, so a leftover is harmless.
                pass

    def set_audition_enabled(self, enabled: bool) -> None:
        """Toggle note audition on selection (issue #67)."""

        self.audition_enabled = bool(enabled)
        if not self.audition_enabled:
            self.audition_player.stop()

    def _selected_notes(self) -> list[GuiMidiNote]:
        """The currently selected notes, in start-time order."""

        notes = self.state.current_notes
        chosen = [notes[index] for index in sorted(self.selected_indices) if 0 <= index < len(notes)]
        return sorted(chosen, key=lambda note: note.start_seconds)

    def _relative_to_first(self, notes: list[GuiMidiNote]) -> list[GuiMidiNote]:
        """Rebase notes onto the earliest one, in both time and pitch.

        The result is a pattern rather than a position: note 0 sits at t=0 and
        pitch offset 0, and the rest carry their gaps and intervals. Anchoring
        that anywhere reproduces the shape.
        """

        origin_seconds = notes[0].start_seconds
        origin_pitch = notes[0].pitch
        return [
            replace(
                note,
                start_seconds=note.start_seconds - origin_seconds,
                pitch=note.pitch - origin_pitch,
            )
            for note in notes
        ]

    def _copy_selected_notes(self) -> None:
        """Copy the selection into the internal buffer (issue #63)."""

        chosen = self._selected_notes()
        if not chosen:
            self._set_status("Nothing to copy: select one or more notes first.")
            return
        self._clipboard_notes = self._relative_to_first(chosen)
        self._clipboard_root_pitch = chosen[0].pitch
        plural = "note" if len(chosen) == 1 else "notes"
        self._set_status(
            f"Copied {len(chosen)} {plural}. Ctrl+V pastes at the mouse pointer "
            "(or the playhead when the pointer is outside the roll)."
        )

    def _paste_notes(self) -> None:
        """Paste the buffer at the mouse pointer, else the playhead (issue #63).

        The pointer gives both a time and a pitch, so the pattern can be placed
        freely and transposes to the row under the cursor. When the pointer is
        not over the grid there is no pitch to read, so it falls back to the
        playhead at the pattern's original pitch.
        """

        if not self._clipboard_notes:
            self._set_status("Nothing to paste: copy a selection first with Ctrl+C.")
            return
        target = self.piano_roll.cursor_target()
        if target is None:
            # No pointer over the grid: fall back to the playhead, at the pitch
            # the pattern was copied from.
            seconds = max(0.0, float(self.piano_roll.playhead_seconds))
            pitch = self._clipboard_root_pitch
        else:
            seconds, pitch = target
        self._insert_notes(self._clipboard_notes, at_seconds=seconds, at_pitch=pitch, verb="Pasted")

    def _duplicate_selected_notes(self) -> None:
        """Copy and paste the selection in one undoable step (issue #63).

        Anchored just after the selection at its own pitch, so a duplicate lands
        beside its source wherever the pointer and playhead happen to be.
        """

        chosen = self._selected_notes()
        if not chosen:
            self._set_status("Nothing to duplicate: select one or more notes first.")
            return
        self._insert_notes(
            self._relative_to_first(chosen),
            at_seconds=chosen[0].start_seconds + self.DUPLICATE_OFFSET_SECONDS,
            at_pitch=chosen[0].pitch,
            verb="Duplicated",
        )

    def _insert_notes(
        self,
        relative_notes: list[GuiMidiNote],
        *,
        at_seconds: float,
        at_pitch: int,
        verb: str,
    ) -> None:
        """Insert a relative pattern anchored at ``at_seconds`` / ``at_pitch``.

        The pattern transposes so its first note lands on ``at_pitch``, which
        moves the group while preserving the intervals between its notes.

        One undoable step whatever the count: edit_history snapshots the whole
        list, exactly as multi-delete relies on.  The inserted notes become the
        selection so they can be moved or edited straight away.
        """

        if self.state.heatmap is None or not relative_notes:
            return
        current = list(self.state.current_notes)
        self.edit_history.record(current)
        anchored = [
            replace(
                note,
                start_seconds=at_seconds + note.start_seconds,
                # The buffer carries pitch *offsets* from its first note.
                pitch=note.pitch + at_pitch,
            )
            for note in relative_notes
        ]
        self.state.tuned_notes, inserted = add_gui_notes(current, anchored)
        self._set_display_notes(self.state.tuned_notes)
        self.piano_roll.set_selected_indices(inserted)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        plural = "note" if len(anchored) == 1 else "notes"
        self._set_status(
            f"{verb} {len(anchored)} {plural} at {at_seconds:.2f}s. "
            f"Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}"
        )

    def _fit_zoom(self) -> None:
        """Zoom to whatever the user is most plausibly working on (issue #10).

        Selected notes first -- that is what editing leaves you holding, and the
        one span there is otherwise no way to zoom to. Failing that the dragged
        analysis range, which the user set deliberately, and failing that the
        whole song.
        """

        if self.state.heatmap is None:
            self._set_status("Nothing to fit: analyze audio first.")
            return

        chosen = self._selected_notes()
        if chosen:
            start = min(note.start_seconds for note in chosen)
            end = max(note.end_seconds for note in chosen)
            pitches = [note.pitch for note in chosen]
            self.piano_roll.fit_to_span(
                start, end, pitch_range=(min(pitches), max(pitches))
            )
            plural = "note" if len(chosen) == 1 else "notes"
            self._set_status(f"Zoomed to {len(chosen)} selected {plural}.")
            return

        start = self.waveform.selection_start_seconds
        length = self.waveform.selection_duration_seconds
        if start is not None and length is not None and length > 0:
            self.piano_roll.fit_to_span(start, start + length)
            self._set_status(
                f"Zoomed to the analysis range ({start:.2f}s–{start + length:.2f}s). "
                "Select notes and press Fit to zoom to them."
            )
            return

        self._reset_zoom()

    def _reset_zoom(self) -> None:
        """Zoom back out to the whole song (issue #10)."""

        if self.state.heatmap is None:
            return
        # Zoom 1.0 *is* the whole-song fit, so set it directly rather than
        # fitting a span, which would leave a margin and a non-zero scroll.
        self.piano_roll.zoom_to(1.0)
        self.piano_roll.set_vertical_zoom(1.0)
        bar = self.piano_roll._horizontal_scroll_bar()
        if bar is not None:
            bar.setValue(0)
        vertical = self.piano_roll._vertical_scroll_bar()
        if vertical is not None:
            vertical.setValue(0)
        self._set_status("Zoom reset to the whole song.")

    def _delete_selected_note(self) -> None:
        """Delete every selected note as one undoable step (issue #35)."""

        if not self.selected_indices:
            return
        current = list(self.state.current_notes)
        doomed = sorted(index for index in self.selected_indices if 0 <= index < len(current))
        if not doomed:
            return
        self.edit_history.record(current)
        # edit_history snapshots the whole list, so a batch delete undoes in one
        # step just like a single delete does.
        self.state.tuned_notes = delete_gui_notes(current, set(doomed))
        if len(doomed) == 1:
            what = f"Deleted MIDI {current[doomed[0]].pitch}"
        else:
            what = f"Deleted {len(doomed)} notes"
        self._select_note(None)
        self._set_display_notes(self.state.tuned_notes)
        preview_status = self._refresh_midi_preview(self.state.tuned_notes)
        self._set_status(f"{what}. Export writes {len(self.state.tuned_notes)} edited notes.{preview_status}")

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

    def _edit_notes_from_piano_roll(
        self,
        edits: list,
        committed: bool = True,
        verb: str = "Moved/resized",
    ) -> None:
        """Apply a group drag as one undoable edit (issue #36).

        Mirrors _edit_note's snapshot discipline: the pre-gesture list is
        recorded once on the first tick, so a multi-tick group drag collapses
        into a single undo step however many notes it moves.
        """

        if self.state.heatmap is None or not edits:
            return
        notes = self.state.current_notes
        applicable = [edit for edit in edits if 0 <= int(edit[0]) < len(notes)]
        if not applicable:
            return
        if self._pre_edit_snapshot is None:
            self._pre_edit_snapshot = list(self.state.current_notes)
            self.state.tuned_notes = list(self.state.current_notes)
        working = self.state.tuned_notes
        touched: set[int] = set()
        for index, start_seconds, duration_seconds, pitch, velocity in applicable:
            index = int(index)
            # replace() keeps pitch_bends, so bend curves survive a group edit.
            working[index] = normalized_gui_note(
                replace(
                    working[index],
                    start_seconds=float(start_seconds),
                    duration_seconds=float(duration_seconds),
                    pitch=int(pitch),
                    velocity=int(velocity),
                )
            )
            touched.add(index)
        self.selected_indices = touched
        self.selected_note_index = next(iter(touched)) if len(touched) == 1 else None
        if not committed:
            # The widget already previewed each note's rect during the drag, so
            # skip the full set_data path and just report progress.
            self._set_status(
                f"Editing {len(touched)} notes… release to update MIDI preview. "
                f"Export writes {len(working)} edited notes."
            )
            return
        if self._pre_edit_snapshot is not None:
            self.edit_history.record(self._pre_edit_snapshot)
            self._pre_edit_snapshot = None
        self._set_display_notes(working)
        self.piano_roll.set_selected_indices(touched)
        preview_status = self._refresh_midi_preview(working)
        self._set_status(
            f"{verb} {len(touched)} {'note' if len(touched) == 1 else 'notes'}. "
            f"Export writes {len(working)} edited notes.{preview_status}"
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
        if index < 0 or index >= len(self.state.current_notes):
            return
        edited_note = normalized_gui_note(
            replace(
                self.state.current_notes[index],
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
                pitch=pitch,
                velocity=velocity,
            )
        )
        if self._pre_edit_snapshot is None:
            # First tick of an edit gesture: snapshot the pre-mutation list once
            # (so a multi-tick drag records a single undo step) and adopt a fresh
            # working list we can mutate in place for the rest of the gesture.
            self._pre_edit_snapshot = list(self.state.current_notes)
            self.state.tuned_notes = list(self.state.current_notes)
        # Mutate the working list by index -- no per-tick full-list copy.
        self.state.tuned_notes[index] = edited_note
        # The dragged note is the selection for the duration of the gesture;
        # keep both views of it in step.
        self.selected_note_index = index
        self.selected_indices = {index}
        if not update_preview:
            # Uncommitted drag: update just the dragged note with a partial
            # repaint and refresh the inspector, skipping the full set_data
            # (canvas resize + full repaint) and the sequence-table rebuild.
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

    def _build_preview_request(self, notes: list[GuiMidiNote], work_dir: Path) -> MidiPreviewRequest:
        return MidiPreviewRequest(
            render_id=self._preview_request_id,
            notes=self._notes_for_midi_preview(notes),
            midi_path=work_dir / "edited.mid",
            wav_path=work_dir / "edited.wav",
            silent_duration_seconds=max(1.0, self.waveform.duration_seconds()),
        )

    def _start_preview_render(self) -> None:
        if self._pending_preview_notes is None:
            return
        notes = self._pending_preview_notes
        self._pending_preview_notes = None
        # Newer preview work supersedes and cancels older work; every generation
        # writes into its own child-owned directory so stale work cannot overwrite
        # an accepted WAV.
        for _generation, old_job in list(self.preview_jobs):
            old_job.cancel()
        job = ProcessJob("preview", parent=self)
        request = self._build_preview_request(notes, job.work_dir)
        job.set_request(request)
        generation = request.render_id
        self.preview_jobs.append((generation, job))
        job.progress.connect(lambda progress, generation=generation, job=job: self._preview_progress(generation, job, progress))
        job.succeeded.connect(lambda result, generation=generation, job=job: self._preview_process_finished(generation, job, result))
        job.failed.connect(lambda message, request=request: self._preview_render_failed(request, message))
        job.done.connect(lambda generation=generation, job=job: self._preview_job_done(generation, job))
        self._refresh_job_ui()
        job.start()

    def _preview_progress(self, generation: int, job: ProcessJob, progress: JobProgress) -> None:
        if generation != self._preview_request_id or not any(run_job is job for _run_generation, run_job in self.preview_jobs):
            return
        self._job_progress(progress, priority="preview")

    def _preview_process_finished(self, generation: int, job: ProcessJob, result: MidiPreviewResult) -> None:
        if self._closing or generation != self._preview_request_id or not isinstance(result, MidiPreviewResult) or result.render_id != self._preview_request_id:
            return
        accepted_dir = job.take_work_dir()
        self._preview_render_finished(result, accepted_dir)

    def _preview_job_done(self, generation: int, job: ProcessJob) -> None:
        self.preview_jobs = [(run_generation, run_job) for run_generation, run_job in self.preview_jobs if run_job is not job]
        self._refresh_job_ui()
        self._maybe_finish_deferred_close()

    def _preview_render_finished(self, result: MidiPreviewResult, work_dir: Path | None = None) -> None:
        if result.render_id != self._preview_request_id:
            return  # superseded by a newer edit; ignore this stale render
        previous_display_seconds = self._display_seconds_from_midi_position(self.midi_player.position())
        old_preview_dir = self._preview_dir
        if work_dir is not None:
            self._preview_dir = work_dir
        self.state.rendered_midi_wav = result.rendered_wav
        self.state.midi_preview_offset_seconds = self.state.analysis_start_seconds if self.state.analysis_duration_seconds is not None else 0.0
        self.midi_player.setSource(QUrl.fromLocalFile(str(result.rendered_wav)))
        if previous_display_seconds > 0:
            self.midi_player.setPosition(self._midi_position_from_display_seconds(previous_display_seconds))
        if old_preview_dir is not None and old_preview_dir != self._preview_dir:
            self._retire_preview_dir(old_preview_dir)
        self._retry_retired_preview_dirs()
        self.transport.set_playback_available(original=self.state.audio_path is not None, midi=True)
        self._set_status("MIDI preview re-rendered.")

    def _preview_render_failed(self, request: MidiPreviewRequest, message: str) -> None:
        if self._closing or request.render_id != self._preview_request_id:
            return
        self.state.rendered_midi_wav = None
        self.midi_player.setSource(QUrl())
        self._retry_retired_preview_dirs()
        self.transport.set_playback_available(original=self.state.audio_path is not None, midi=False)
        self._set_status(f"MIDI preview unavailable: {message}")

    def _flush_preview_render(self) -> None:
        """Render any pending/in-flight preview synchronously (used by tests)."""

        if self.preview_debounce_timer.isActive():
            self.preview_debounce_timer.stop()
        if self._pending_preview_notes is None:
            return
        work_dir = Path(tempfile.mkdtemp(prefix="notegrabber-gui-preview-sync-"))
        request = self._build_preview_request(self._pending_preview_notes, work_dir)
        self._pending_preview_notes = None
        try:
            rendered_wav = render_midi_preview(request)
        except Exception as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            self._preview_render_failed(request, str(exc))
            return
        self._preview_render_finished(MidiPreviewResult(render_id=request.render_id, rendered_wav=rendered_wav), work_dir)

    def _notes_for_midi_preview(self, notes: list[GuiMidiNote]) -> list[GuiMidiNote]:
        """Return notes in the local MIDI-preview timeline.

        Exported notes stay in the full-song timeline.  Range previews are
        rendered locally so a selection starting at e.g. 40s does not produce a
        MIDI WAV with 40s of leading silence; playback maps local MIDI time back
        to the full waveform/heatmap timeline.
        """

        # Muted notes are dropped first, so they never reach the preview render
        # regardless of which timeline branch runs below (issue #66).
        notes = audible_gui_notes(notes)
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
        self.last_playback_mode = "both"
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
        self.last_playback_mode = "original"
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
        self.last_playback_mode = "midi"
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

    def _guarded_transport(self, handler):
        """Wrap a transport handler so it is skipped while a field has focus.

        Space and the digits are transport shortcuts, but they are also ordinary
        input in a spin box or combo: typing a velocity of 100 must not start
        playback three times (issue #64).
        """

        def run() -> None:
            if self._text_entry_has_focus():
                return
            handler()

        return run

    def _text_entry_has_focus(self) -> bool:
        """True when focus is in a widget where digits/space are real input."""

        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit

        widget = QApplication.focusWidget()
        if widget is None:
            return False
        if isinstance(widget, (QAbstractSpinBox, QComboBox, QLineEdit)):
            return True
        # A spin box's internal QLineEdit is the actual focus widget on most
        # styles, so check the parent chain too.
        parent = widget.parentWidget()
        return isinstance(parent, (QAbstractSpinBox, QComboBox))

    def _toggle_playback(self) -> None:
        """Space: pause if playing, otherwise resume the last mode (issue #64).

        Resuming reuses the ordinary play path, which starts from the current
        playhead, so pause/resume keeps its position.
        """

        if self.playback_mode in {"both", "original", "midi"}:
            self._pause_playback()
            return
        self._play_mode(self.last_playback_mode)

    def _play_mode(self, mode: str) -> bool:
        """Start one playback mode by name, falling back when it is unavailable.

        Returns True when something started. A transcription without a rendered
        preview cannot play MIDI or both, so those fall back to the original
        rather than doing nothing silently.
        """

        has_audio = self.state.audio_path is not None
        has_midi = self.state.rendered_midi_wav is not None
        fell_back = False
        if mode in {"midi", "both"} and not has_midi:
            fell_back = True
            mode = "original"
        if mode in {"original", "both"} and not has_audio:
            self._set_status("Load an audio file to play.")
            return False
        if mode == "midi" and not has_midi:
            self._set_status("No rendered MIDI preview to play yet.")
            return False

        if mode == "both":
            self._play_both()
        elif mode == "original":
            self._play_original()
        elif mode == "midi":
            self._play_midi()
        else:
            return False
        if fell_back:
            # Set after playing: each _play_* writes its own status, which would
            # otherwise bury the explanation for the substitution.
            self._set_status("No rendered MIDI preview yet: playing the original instead.")
        return True

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
            QMessageBox.information(self, "No analysis", "Analyze audio before exporting.")
            return
        # One dialog offering MIDI plus rendered-audio formats. The chosen filter
        # decides the default extension; the file's own extension wins if the user
        # types one (so "song.mp3" under the MIDI filter still exports MP3).
        filters = ";;".join(
            (
                "MIDI files (*.mid *.midi)",
                "WAV audio (*.wav)",
                "MP3 audio (*.mp3)",
                "FLAC audio (*.flac)",
                "OGG audio (*.ogg)",
                "All files (*)",
            )
        )
        path, selected_filter = QFileDialog.getSaveFileName(self, "Export", "analysis.mid", filters)
        if not path:
            return
        output = self._resolve_export_path(Path(path), selected_filter)
        self._run_export(gui_notes_to_midi(notes), output, len(notes))

    @staticmethod
    def _resolve_export_path(output: Path, selected_filter: str) -> Path:
        """Give the output path an extension: keep a supported one, else use the filter's."""

        if format_for_path(output) is not None:
            return output
        # Map the selected filter to an extension when the user gave none.
        for ext in ("wav", "mp3", "flac", "ogg", "mid"):
            if f"*.{ext}" in selected_filter:
                return output.with_suffix(f".{ext}")
        return output.with_suffix(".mid")

    def _run_export(self, midi_notes, output: Path, note_count: int) -> None:
        """Export notes to ``output`` (MIDI or audio), with a busy cursor and status."""

        fmt = format_for_path(output) or "mid"
        label = EXPORT_FORMATS.get(fmt, fmt.upper())
        # Audio export synthesizes the whole song, so it can take a moment; show a
        # busy cursor rather than freezing silently.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        if fmt != "mid":
            self._set_status(f"Rendering {label} to {output.name}…")
            QApplication.processEvents()
        try:
            result, error = export_notes(midi_notes, output)
        finally:
            QApplication.restoreOverrideCursor()
        if result is None:
            QMessageBox.critical(self, "Export failed", error or "Unknown error")
            self._set_status("Export failed.")
            return
        self._set_status(f"Exported {note_count} notes as {label} to {output}")

    def _set_status(self, text: str) -> None:
        # Single status home: the themed strip in the always-visible top block, so
        # it is never clipped even when the window is taller than the screen. The
        # QMainWindow status bar is kept in sync as a harmless mirror (and for the
        # tests that read currentMessage()), but the strip is the one users see.
        self.transport.set_status(text)
        self.statusBar().showMessage(text)

    def _on_sequence_count_changed(self, count: int) -> None:
        """Reflect the detected-note count in the collapsible section header."""

        self.sequence_section.set_suffix(f"  ·  {count}" if count else "")

    def _apply_style(self) -> None:
        self.setStyleSheet(build_stylesheet(active_theme()))

    def _on_theme_changed(self, theme_id: str) -> None:
        """Switch to ``theme_id``: reskin the chrome, repaint canvases, persist."""

        if theme_id not in THEMES:
            return
        set_active_theme(theme_id)
        self._apply_style()
        # The painted widgets read colors from the active theme at paint time but
        # are not rebuilt, so force a repaint of each so the new palette shows.
        for widget in (self.piano_roll, self.waveform):
            widget.update()
        self.controls.update()
        self._settings.setValue("theme", theme_id)
