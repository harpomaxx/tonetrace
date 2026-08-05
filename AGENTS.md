# AGENTS.md

## Project idea

This repository is a Linux/free-software clone/spike inspired by noteGRABBER-style workflows: analyze an audio sample, visualize pitch salience as a piano-roll heatmap, extract candidate MIDI notes, and let the user compare the original audio with the generated MIDI.

Current scope is **CLI (transcription + stem separation) + native standalone GUI app**. No VST/LV2/CLAP plugin has been implemented yet. (An earlier browser viewer / upload server was removed in favor of the native GUI.)

## Current implementation

Python package under `src/notegrabber/`:

- `cli.py` — command-line interface.
- `analyzer.py` — audio analysis backends and heatmap generation.
- `midi.py` — minimal Standard MIDI File writer.
- `separator.py` — stem separation (split a mix into vocals/drums/bass/other WAVs). Thin, backend-swappable wrapper over the `demucs-onnx` package (HT-Demucs via pure numpy + onnxruntime, no PyTorch; model auto-downloads on first use). Optional `[separate]` extra; graceful "install the extra" error when missing. Output stems feed straight into `analyze`.
- `midi_render.py` — renders MIDI to WAV with TiMidity++ when it is on PATH (`render_midi_to_wav`); shared by the GUI's edited-MIDI preview. Degrades gracefully when TiMidity++ is missing.
- `gui/` — PySide6 standalone GUI app. Key modules:
  - `main_window.py` — app shell: playback/playhead sync, note editing, analysis/preview orchestration.
  - `state.py` — Qt-free GUI models and note-edit helpers (`add_gui_note`/`add_gui_notes`, `delete_gui_note`/`delete_gui_notes`, `update_gui_note`, `normalized_gui_note`). The insert helpers deliberately insert into the existing list rather than sorting the merged one: drag-edits can leave notes out of start-time order, and re-sorting would silently renumber untouched notes, which `edit_history` snapshots and the sequence table address by position. Heatmaps use the shared compact `HeatmapData` float32 `(frame, note)` matrix from `heatmap.py` (with a pure-Python fallback when numpy is absent).
  - `widgets/piano_roll.py` — heatmap + MIDI note map, and the whole note-editing surface (selection, drag/resize, rubber band, note creation, anchored zoom, fit). Vectorized numpy paint; timeline spans the full song; frame indexing is offset-aware for range analyses. See "Piano-roll editing model" below before changing it.
  - `widgets/waveform.py` — overview + pitch strip. Has a `left_gutter` matching the piano roll keyboard so both share one seconds→x mapping (playheads line up). The paint path precomputes a cached numpy per-column (min, max) envelope (issue #5) instead of slicing samples per pixel.
  - `key_detection.py` — Qt-free key/scale estimator (issue #7). Builds a 12-bin pitch-class profile from notes (weighted by duration × velocity, range-aware) and scores it against the 24 major/minor keys with Krumhansl-Schmuckler correlation; reports confidence and flags uncertain/atonal input.
  - `transcription_stats.py` — Qt-free stats bundle (issue #8): note count, duration, note-onset inter-onset-interval tempo estimate (folded into 60–180 BPM), and the detected key, formatted as a one-line summary. Shown in an always-visible stats strip under the waveform.
  - `widgets/knob.py` — custom rotary `KnobWidget` (transcription controls). Emits `editingFinished` on commit (drag release / wheel / key); CQT retune is wired to that, not `valueChanged`, so dragging does not retune per tick.
  - `widgets/controls.py`, `widgets/sequence.py`, `widgets/transport.py` — left controls, sequence table, transport bar.
  - `process_jobs.py` / `job_runner.py` — cancellable `QProcess` job controller and isolated child runner for audio decode/overview, transcription, and edited-MIDI preview. Opaque librosa/ONNX/native-synthesis work never runs in a GUI-owned compute thread; cancellation uses bounded terminate→kill escalation, stale generations are ignored, and window close waits asynchronously for child processes to stop.
  - `analysis_worker.py`, `audio_load_worker.py`, `midi_preview_worker.py` — pure computation/request-result helpers reused by the child runner; compatibility QObject wrappers remain for synchronous tests. The GUI shows stage progress and a Cancel action, and debounced MIDI previews use generation-specific output directories.
  - `theme.py` — dark pro-DAW stylesheet and button/icon helpers.

Main commands:

```bash
notegrabber analyze input.wav --out output.mid
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend cqt
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend basic-pitch
notegrabber separate input.mp3 --out-dir stems/ --stems bass,vocals
notegrabber gui
notegrabber-gui
```

Backends:

- `simple` — deterministic stdlib DSP baseline for synthetic test fixtures.
- `cqt` — librosa Constant-Q Transform backend for more music-aligned heatmaps and baseline MIDI extraction.
- `basic-pitch` — Spotify Basic Pitch/ONNX backend for stronger ML note transcription and probability heatmaps. Defaults match NeuralNote's Basic Pitch usage (onset 0.3, frame 0.5, 11-frame / 127.7 ms minimum note length). `analyze_basic_pitch` calls `run_inference` + `model_output_to_notes` directly (not `predict()`), so `infer_onsets` and the frequency bounds are exposed, tunable parameters. The model wrapper is constructed explicitly around ONNX Runtime so Basic Pitch does not probe incompatible installed runtimes (notably TFLite after range extraction).

The native GUI is launched with `notegrabber-gui` or `notegrabber gui`. It implements standalone GUI milestones 0–4 in basic form: open an audio file, load waveform previews and low-resolution full-song pitch overviews in cancellable child processes, analyze full audio or a selected time range with progress/cancellation, show heatmap/MIDI rectangles, show a grouped sequence table, retune CQT thresholds in memory, compare original vs rendered MIDI playback, edit/delete selected notes, and export current notes to MIDI. Clicking the waveform, piano roll, or sequence rows seeks both players and updates the playhead. Clicking a note rectangle selects/highlights it; hovered/selected notes show resize handles and cursor feedback; the inspector edits start, duration, pitch, and velocity; dragging a note body moves time/pitch; dragging note edges resizes boundaries; Delete/Backspace or the delete button removes it from the tuned note list used for export. Inspector edits, deletes, CQT retunes, and committed piano-roll drags re-render the MIDI WAV preview when rendering is enabled, so playback reflects edited notes. The primary Open/Analyze/Cancel/Delete/Export buttons sit in a horizontal action bar above the waveform (not the left panel), with Fit/Reset zoom controls after a divider. A "Notes only (hide heatmap)" checkbox hides the heatmap so only extracted notes show. An always-visible stats strip under the waveform shows note count, duration, estimated tempo (BPM), and detected key (scoped to a selected range when one is active), refreshed on every note edit. Committed edits support undo/redo (Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z).

### Piano-roll editing model

The editing surface grew a lot in issues #35/#36/#37/#63 and #10. The pieces interact, so read this before touching `piano_roll.py`:

- **Selection is a set.** `PianoRollWidget.selected_indices: set[int]` is the source of truth. `selected_note_index` survives as a read-only property meaning *"the one selected note, or None when zero or several are selected"*, which is what the single-note inspector and edit-apply paths read. `selection_changed` carries the set to `main_window`, which mirrors it in `selected_indices` and drives the label ("N notes selected"), the inspector (single only) and delete (any non-empty).
- **Empty-space gestures are arbitrated by movement.** Mouse-down on empty space arms a rubber band; moving past `drag_threshold_pixels` makes it a selection; releasing without moving is a plain click, which clears the selection and seeks. Seek fires on *release*, not press, so a band drag does not also move the playhead. Double-click on empty space creates a note — chosen precisely because it collides with neither.
- **Group drag clamps as a group.** When more than one note is selected, a body/edge drag snapshots every selected note into `drag_group_originals` at press and recomputes from those originals each tick. The delta is bounded by the *most-constrained member* before being applied, so a group meeting a boundary keeps its spacing and intervals rather than collapsing. Pitch is bounded by `_drawable_pitch_range()` — the heatmap's own rows — **not** 0..127: `_note_rect` returns None for a pitch with no row, so a note clamped to a valid-but-undrawn MIDI pitch stays in the data and vanishes from the roll.
- **`piano_roll.notes` is the same list object as the model's note list.** Anything that previews an edit in place (`preview_note_edit`) mutates the model. `_emit_group_edit` therefore emits `notes_edited` *before* previewing, so `main_window`'s first-tick `_pre_edit_snapshot` captures the pre-drag state; otherwise undo rewinds only to mid-drag. Tests that exercise drag committing must drive the real `mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent` sequence — calling the emit helpers directly does not exercise this ordering.
- **Batch edits are one undo step.** `notes_edited = Signal(list, bool)` carries every affected note per move rather than firing the single-note signal N times. `edit_history.record` snapshots the whole list, so batch delete, paste and group drag all undo in one step for free.
- **Zoom is anchored.** `zoom_to` / `vertical_zoom_to` take an optional anchor and solve for the scroll offset that keeps that point under the cursor; with no anchor they hold the viewport centre so buttons do not jump the view. At the top/left edge, zooming out cannot honour the anchor (it would need a negative scroll offset), so it clamps and the anchored point drifts — that is geometry, not a bug.
- **The copy buffer is a pattern, not a position.** Copied notes are stored relative to their earliest note in *both* time and pitch, plus the root pitch they came from, so a paste re-anchors anywhere and transposes onto the row under the pointer while keeping intervals.
- **Editing keys route through the roll, not the window.** The roll sits in a `QScrollArea`, which defaults to `StrongFocus` and eats the arrow keys to scroll; before the roll took focus and offered keys to `MainWindow.handle_piano_roll_key`, nudging silently did nothing. Anything the window does not claim still falls through to the scroll area, so arrows keep scrolling when no notes are selected. Transport keys (Space, digits) are separately guarded against firing while a spin box or combo has focus, or typing a velocity would start playback.
- **Mute is a note field, filtered centrally.** `GuiMidiNote.muted` keeps a note in the project and on the roll while excluding it from sound: `gui_notes_to_midi` drops muted notes itself, so every export path honours it without each caller remembering, and `_notes_for_midi_preview` strips them before either timeline branch runs. A mixed selection mutes wholesale rather than flipping each note.
- **Audition is guarded on `drag_has_moved`, not `drag_mode`.** `mousePressEvent` arms `drag_mode` *before* emitting `note_selected`, so guarding on `drag_mode` suppresses every click and audition never fires. It is wired to `note_selected` (a real click) rather than `selection_changed`, which also fires for nudges, pastes and rubber-band sweeps and would retrigger audio continuously.
- **Beat times are advisory display data.** The roll draws `beat_times` from the analysis result; `beats_per_bar` is a user setting because tracking finds the pulse but not the metre. `_beat_grid_is_visible()` is shared by the beat drawing and the seconds-grid dimming so the two cannot disagree, and the grid thins to downbeats (then hides) when beats fall closer than `MIN_BEAT_SPACING_PIXELS`.

Tuning flags available on `analyze`:

- `--threshold` — CQT heatmap-to-note activation threshold.
- `--onset-threshold` — Basic Pitch onset threshold.
- `--frame-threshold` — Basic Pitch frame threshold.
- `--min-duration` — minimum note duration in seconds for CQT/Basic Pitch extraction.

## Issue workflow

When fixing an issue or building a feature, follow these steps in order. **Do not merge without the user's go-ahead** — they test the change in the real GUI first, because most of this project's behaviour (gestures, layout, playback) cannot be judged from a passing test suite.

1. **Sync and branch.** Start from an up-to-date `main` (`git fetch origin`, confirm the working tree is clean) and create a topic branch: `feat/<slug>` for enhancements, `fix/<slug>` for bugs, `perf/<slug>` for performance work.
2. **Fix the issue.** Verify the issue's own code references before relying on them — line numbers in older issues drift as files grow.
3. **Run the tests**, and add new ones when the change is not already covered. A regression test should be checked to actually fail against the old behaviour, otherwise it pins nothing. Run the full suite before handing over, not just the new file.
4. **Stop and hand over for testing.** Report what changed, how to exercise it in the GUI, any decisions made, and anything deliberately left out. Then wait.
5. **Commit, push, and open a PR** once the user confirms. Explain *why* in the commit body — the measured symptom, the approach, and any approach that was tried and rejected.
6. **Merge** only when the user asks for it.

Notes:

- **Keep bare `#N` out of PR titles for partial work.** GitHub treats the title as a closing context, so a title containing `(#10)` closes issue #10 on merge even when the body says "Part of #10". Reference the issue in the body instead, or expect to reopen it.
- Conversely, `Closes #N` in the commit or PR body is what actually auto-closes an issue; a plain `(#13, #19)` reference does not.
- Prefer several small PRs over one large one when an issue lists independent improvements; issues often say so themselves.

## Test workflow

Install dev dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e '.[standalone]'
```

Run tests:

```bash
NOTEGRABBER_BIN=notegrabber python3 -m pytest -q
```

Current expected result: **520 passed** when optional ML and PySide6 GUI dependencies are installed. (Stem-separation tests mock `demucs_onnx`, so they run without downloading the model.)

Quick GUI manual check:

```bash
notegrabber-gui oxi.wav
# Click Analyze, then test: Space/1/2/3 transport; single-note select/drag/resize/delete;
# shift-click and rubber-band multi-select; group drag of a multi-selection (including to the very
# top/left, where it must clamp without losing spacing); double-click empty space to add a note;
# arrow/+- nudging; M to mute (muted notes stay visible but drop out of preview and export);
# clicking a note auditions it; Ctrl+C/V/D copy, paste-at-pointer and duplicate; Ctrl+wheel and
# Shift+wheel zoom; Fit/Reset; the detected-beat overlay and its bar length; inspector edits;
# undo/redo after each; and Export MIDI.
```

Where the piano-roll editing behaviour is pinned, if you change that surface:

| File | Covers |
|---|---|
| `test_add_note.py` | double-click note creation (#37) |
| `test_multi_note_selection.py` | selection set, rubber band, gesture arbitration, multi-delete (#35) |
| `test_group_move_resize.py` | group drag/resize, group clamping, one-step undo (#36) |
| `test_copy_paste_notes.py` | copy/paste/duplicate, transposition, the relative buffer (#63) |
| `test_cursor_centered_zoom.py` | cursor-anchored zoom on both axes (#10) |
| `test_keyboard_nudge.py` | arrow/velocity nudging and key routing through the roll (#65) |
| `test_note_mute.py` | non-destructive mute and its export/preview exclusion (#66) |
| `test_note_audition.py` | single-note audition and its drag guard (#67) |
| `test_transport_shortcuts.py` | Space/1/2/3/0/Esc and the text-entry focus guard (#64) |
| `test_beat_tempo.py` | librosa tempo/beat estimation and the confidence policy (#14) |
| `test_beat_grid_overlay.py` | the beat overlay, beats-per-bar, and low-zoom density (#14) |
| `test_fit_reset_zoom.py` | Fit/Reset and the fallback chain (#10) |
| `test_piano_roll_hit_index.py` | per-row hit-testing index (#29) |
| `test_progress_layout_stable.py` | the progress strip not shifting the roll |

Incremental markers:

```bash
python3 -m pytest -m tier0
python3 -m pytest -m tier1
python3 -m pytest -m tier2
python3 -m pytest -m tier3
python3 -m pytest -m heatmap
python3 -m pytest -m cqt
python3 -m pytest -m basic_pitch
python3 -m pytest -m gui
```

## Local sample

`oxi.wav` is a local real-audio sample used for manual testing (e.g. `notegrabber-gui oxi.wav`, or `notegrabber separate oxi.wav --out-dir stems/`). Generated outputs go under `out/`.

These are working artifacts, not core source code.

## Development notes

- GitHub repo: `harpomaxx/tonetrace`; default branch is `main`. The backlog lives in [GitHub Issues](https://github.com/harpomaxx/tonetrace/issues) (the old in-repo `issues/` folder was removed). Issues #1–#8, #10, #13, #14, #19, #27, #35, #36, #37, #63, #64, #65, #66 and #67 are implemented: vectorized paint paths, key/stats, cancellable process jobs with clean shutdown, compact heatmap storage, librosa beat tracking with a beat overlay, and the piano-roll editing suite (note creation, multi-select with rubber band, group move/resize, copy/paste/duplicate, keyboard nudging, note audition, non-destructive mute, transport shortcuts, cursor-anchored zoom with Fit/Reset). Still open from the #68–#79 batch: #77 (sequence-table selection sync) and #74 (waveform minimap) are the closest neighbours; #75 (zoom toolbar) overlaps the Fit/Reset controls now in the action bar and probably wants rescoping rather than implementing.
- Packaging: `python3 -m build` produces a wheel + sdist in `dist/` that install and run outside the source tree (`pip install 'dist/*.whl[gui]'`, then `notegrabber-gui` from anywhere). The GUI icon SVGs under `gui/resources/` are bundled via `[tool.hatch.build.targets.wheel] artifacts` and loaded at runtime with `Path(__file__)`, so keep that relative layout intact. `dist/`/`build/` are gitignored.
- Keep the existing CLI contract stable unless tests/docs are updated together.
- Prefer adding tests before changing analysis behavior.
- Do not commit generated caches: `.pytest_cache/`, `__pycache__/`, etc.
- Treat `.pi-subagents/` as agent/runtime artifacts, not project source.
- CQT extraction is still heuristic. Basic Pitch is currently the best default backend for real samples.
- If editing `src/notegrabber/gui/`, run `python3 -m compileall -q src tests` and `NOTEGRABBER_BIN=notegrabber python3 -m pytest -q`; when PySide6 is installed, offscreen GUI smoke tests should run.

## Recommended next steps

User priorities for the next work are **UI polish, speed/responsiveness, playback/playhead sync, and zoom/navigation polish**.

1. **Playback/playhead synchronization polish**: smooth interpolated playhead sync is implemented for waveform + heatmap, with MIDI-follow correction during Play both. The resync loop checks drift every 12 ticks (~192ms) with a 120ms tolerance; a tighter cadence/tolerance was tried and reverted because `QMediaPlayer.position()` updates in coarse steps on this system's FFmpeg backend, causing visible backward playhead snaps. The interpolated clock also freezes while either `QMediaPlayer` reports `StalledMedia`/`BufferingMedia` so the playhead does not race ahead of stuttering audio. Range-analysis MIDI preview renders in a local timeline and maps back to the full-song waveform/heatmap timeline, clamping at the selected range end. Continue manual checks with edited MIDI preview and Qt audio backend edge cases, especially real stall/buffering behavior on large files.
2. **Zoom/navigation polish**: note edits/clicks no longer compound the heatmap zoom by recalculating fit from the already-zoomed canvas. Pitch-row vertical zoom is implemented with Shift+wheel, and time zoom remains Ctrl+wheel; the left transcription box no longer contains zoom sliders. Cursor-anchored zoom is implemented on both axes: Ctrl+wheel holds the time under the cursor and Shift+wheel holds the pitch, by solving for the scroll offset that puts the anchored point back under the cursor at the new scale. Zoom with no cursor (buttons, keyboard) holds the viewport centre. Note the inherent limit at the top/left edge — honouring the anchor while zooming out there would need a negative scroll offset, so it clamps at 0 and the anchored point drifts for those last clicks. Fit/Reset buttons are in the action bar: Fit zooms to the selected notes (both axes), falling back to the analysis range then the whole song. The horizontal zoom cap was raised 32x → 256x for Fit, since fitting a couple of seconds inside a multi-minute song needs far more than 32x; the canvas stays viewport-bounded at any zoom. **This issue is complete** — further zoom work is #75.
3. **Speed/responsiveness**: playhead updates repaint only narrow regions; the heatmap paint uses compact/vectorized numpy storage (issues #1/#27); per-drag note repaint is partial (issue #3); waveform/overview paths are vectorized (issues #5/#6); and audio-load, analysis, and MIDI-preview work now runs in cancellable isolated processes with stage progress and safe shutdown (issues #13/#19). Remaining: optional finer-grained backend progress and overview/heatmap level-of-detail caching.
4. **UI polish**: heatmap view height is now capped relative to the window so vertical pitch zoom does not hide the inspector/sequence area, and the left-panel action buttons are placed above reserved/stub controls so Open/Analyze/Delete/Export remain visible. Continue refining the ToneTrace dark pro-DAW layout, especially control density, selected-region affordances, selected-note editing affordances, and visual hierarchy between overview waveform, detail heatmap, and sequence table.
5. **Editing workflow polish**: essentially built. Mouse — create (#37), multi-select with rubber band (#35), group move/resize (#36), copy/paste/duplicate (#63), multi-delete, audition on click (#67). Keyboard — nudging (#65), transport shortcuts (#64), non-destructive mute (#66). All edits are single undo steps. Optional ghost previews during drag remain unbuilt, as does sequence-table selection sync (#77). Read the "Piano-roll editing model" section before extending any of this; the gestures on empty space are already arbitrated between seek, rubber band and note creation, so a new one needs to fit that scheme rather than claim a fourth meaning, and the keys are similarly crowded.
6. **Output/workflow polish**: add native CSV/minimap parity where useful and project/session save-load.
7. **Analysis comparison and quality**: add explicit compare mode for CQT vs Basic Pitch outputs and tolerant regression fixtures/metrics for real samples if legally/shareably possible.
8. Later, move toward plugin implementation using JUCE/DPF/iPlug2/NIH-plug.
