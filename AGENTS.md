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
  - `state.py` — Qt-free GUI models and note-edit helpers. Heatmaps use the shared compact `HeatmapData` float32 `(frame, note)` matrix from `heatmap.py` (with a pure-Python fallback when numpy is absent).
  - `widgets/piano_roll.py` — heatmap + MIDI note map. Vectorized numpy paint; timeline spans the full song; frame indexing is offset-aware for range analyses.
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

The native GUI is launched with `notegrabber-gui` or `notegrabber gui`. It implements standalone GUI milestones 0–4 in basic form: open an audio file, load waveform previews and low-resolution full-song pitch overviews in cancellable child processes, analyze full audio or a selected time range with progress/cancellation, show heatmap/MIDI rectangles, show a grouped sequence table, retune CQT thresholds in memory, compare original vs rendered MIDI playback, edit/delete selected notes, and export current notes to MIDI. Clicking the waveform, piano roll, or sequence rows seeks both players and updates the playhead. Clicking a note rectangle selects/highlights it; hovered/selected notes show resize handles and cursor feedback; the inspector edits start, duration, pitch, and velocity; dragging a note body moves time/pitch; dragging note edges resizes boundaries; Delete/Backspace or the delete button removes it from the tuned note list used for export. Inspector edits, deletes, CQT retunes, and committed piano-roll drags re-render the MIDI WAV preview when rendering is enabled, so playback reflects edited notes. The primary Open/Analyze/Delete/Export buttons sit in a horizontal action bar above the waveform (not the left panel). A "Notes only (hide heatmap)" checkbox hides the heatmap so only extracted notes show. An always-visible stats strip under the waveform shows note count, duration, estimated tempo (BPM), and detected key (scoped to a selected range when one is active), refreshed on every note edit. Committed edits support undo/redo (Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z). Keyboard nudging is still future work.

Tuning flags available on `analyze`:

- `--threshold` — CQT heatmap-to-note activation threshold.
- `--onset-threshold` — Basic Pitch onset threshold.
- `--frame-threshold` — Basic Pitch frame threshold.
- `--min-duration` — minimum note duration in seconds for CQT/Basic Pitch extraction.

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

Current expected result: **287 passed** when optional ML and PySide6 GUI dependencies are installed. (Stem-separation tests mock `demucs_onnx`, so they run without downloading the model.)

Quick GUI manual check:

```bash
notegrabber-gui oxi.wav
# Click Analyze, then test Play both, piano-roll note selection/drag/resize/delete, inspector edits, and Export MIDI.
```

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

- GitHub repo: `harpomaxx/tonetrace`; default branch is `main`. The backlog lives in [GitHub Issues](https://github.com/harpomaxx/tonetrace/issues) (the old in-repo `issues/` folder was removed). Issues #1–#8, #13, #19, and #27 are implemented (vectorized paint paths, key/stats, cancellable process jobs with clean shutdown, and compact heatmap storage).
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
2. **Zoom/navigation polish**: note edits/clicks no longer compound the heatmap zoom by recalculating fit from the already-zoomed canvas. Pitch-row vertical zoom is implemented with Shift+wheel, and time zoom remains Ctrl+wheel; the left transcription box no longer contains zoom sliders. Continue improving horizontal zoom-out/in behavior, preserve cursor-centered zoom, add fit-to-range / fit-to-selection controls, and keep horizontal scroll position intuitive when zoom changes.
3. **Speed/responsiveness**: playhead updates repaint only narrow regions; the heatmap paint uses compact/vectorized numpy storage (issues #1/#27); per-drag note repaint is partial (issue #3); waveform/overview paths are vectorized (issues #5/#6); and audio-load, analysis, and MIDI-preview work now runs in cancellable isolated processes with stage progress and safe shutdown (issues #13/#19). Remaining: optional finer-grained backend progress and overview/heatmap level-of-detail caching.
4. **UI polish**: heatmap view height is now capped relative to the window so vertical pitch zoom does not hide the inspector/sequence area, and the left-panel action buttons are placed above reserved/stub controls so Open/Analyze/Delete/Export remain visible. Continue refining the ToneTrace dark pro-DAW layout, especially control density, selected-region affordances, selected-note editing affordances, and visual hierarchy between overview waveform, detail heatmap, and sequence table.
5. **Editing workflow polish**: add keyboard nudging and optional ghost previews during drag; committed edits already support undo/redo.
6. **Output/workflow polish**: add native CSV/minimap parity where useful and project/session save-load.
7. **Analysis comparison and quality**: add explicit compare mode for CQT vs Basic Pitch outputs and tolerant regression fixtures/metrics for real samples if legally/shareably possible.
8. Later, move toward plugin implementation using JUCE/DPF/iPlug2/NIH-plug.
