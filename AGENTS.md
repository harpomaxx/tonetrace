# AGENTS.md

## Project idea

This repository is a Linux/free-software clone/spike inspired by noteGRABBER-style workflows: analyze an audio sample, visualize pitch salience as a piano-roll heatmap, extract candidate MIDI notes, and let the user compare the original audio with the generated MIDI.

Current scope is **CLI + local web visualization/server + native standalone GUI app**. No VST/LV2/CLAP plugin has been implemented yet.

## Current implementation

Python package under `src/notegrabber/`:

- `cli.py` — command-line interface.
- `analyzer.py` — audio analysis backends and heatmap generation.
- `midi.py` — minimal Standard MIDI File writer.
- `visualizer.py` — generates a self-contained browser viewer.
- `server.py` — local upload/re-analysis web server for generating fresh viewers from selected files; renders MIDI audio previews by default when TiMidity++ is available.
- `gui/` — PySide6 standalone GUI app with waveform, heatmap/piano-roll, controls, sequence table, background analysis worker, original/MIDI playback, note selection/delete, and MIDI export.

Main commands:

```bash
notegrabber analyze input.wav --out output.mid
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend cqt
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend basic-pitch
notegrabber visualize input.wav --out-dir viewer-dir
notegrabber visualize input.wav --out-dir viewer-dir --backend basic-pitch --onset-threshold 0.5 --frame-threshold 0.3 --min-duration 0.05
notegrabber serve --out-dir out/server
notegrabber gui
notegrabber-gui
```

Backends:

- `simple` — deterministic stdlib DSP baseline for synthetic test fixtures.
- `cqt` — librosa Constant-Q Transform backend for more music-aligned heatmaps and baseline MIDI extraction.
- `basic-pitch` — Spotify Basic Pitch/ONNX backend for stronger ML note transcription and probability heatmaps.

The visualization command defaults to Basic Pitch and writes:

- `index.html`
- `heatmap.json`
- `analysis.mid`
- rendered `analysis.wav` via TiMidity++ when available
- a copy of the original audio

The browser viewer overlays extracted MIDI rectangles on the heatmap and supports hover/click note inspection, show/hide overlay, horizontal zoom, fit-to-width, and live threshold/min-duration re-extraction from the loaded heatmap. It also includes a **Detected sequence** panel with a whole-phrase piano-roll overview/minimap, onset-grouped note/chord table, clickable rows/blocks that jump playback, and CSV copy for the currently visible/tuned sequence. The original-audio panel includes a browser file picker and waveform/signal canvas; in static viewers, selected files update playback and waveform preview only, while analysis/heatmap data still comes from the generated viewer artifacts. To select a file and generate new MIDI/heatmap/notes from the browser, use `notegrabber serve` and upload through the local web app. For very large files, the viewer compresses the heatmap canvas horizontally to stay within browser canvas limits; generated HTML/JSON can still be large.

The native GUI is launched with `notegrabber-gui` or `notegrabber gui`. It implements standalone GUI milestones 0–4 in basic form: open an audio file, load waveform previews and low-resolution full-song pitch overviews in background workers, analyze full audio or a selected time range in a background worker, show heatmap/MIDI rectangles, show a grouped sequence table, retune CQT thresholds in memory, compare original vs rendered MIDI playback, edit/delete selected notes, and export current notes to MIDI. Clicking the waveform, piano roll, or sequence rows seeks both players and updates the playhead. Clicking a note rectangle selects/highlights it; hovered/selected notes show resize handles and cursor feedback; the inspector edits start, duration, pitch, and velocity; dragging a note body moves time/pitch; dragging note edges resizes boundaries; Delete/Backspace or the delete button removes it from the tuned note list used for export. Inspector edits, deletes, CQT retunes, and committed piano-roll drags re-render the MIDI WAV preview when rendering is enabled, so playback reflects edited notes. Editing polish such as undo/redo and keyboard nudging is still future work.

Tuning flags available on both `analyze` and `visualize`:

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

Current expected result: **53 passed** when optional ML and PySide6 GUI dependencies are installed.

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

`oxi.wav` is a local real-audio sample used for manual testing. Generated outputs are under `out/`, especially `out/oxi-viewer/index.html`. The viewer has most recently been generated with the default Basic Pitch backend.

These are working artifacts, not core source code.

## Development notes

- Keep the existing CLI contract stable unless tests/docs are updated together.
- Prefer adding tests before changing analysis behavior.
- Do not commit generated caches: `.pytest_cache/`, `__pycache__/`, etc.
- Treat `.pi-subagents/` as agent/runtime artifacts, not project source.
- CQT extraction is still heuristic. Basic Pitch is currently the best default backend for real samples.
- If editing `visualizer.py`, regenerate `out/oxi-viewer/index.html` for manual checks and run a JavaScript syntax check on the extracted script when possible (for example with `node --check`) because the viewer is generated as an embedded script string.
- If editing `src/notegrabber/gui/`, run `python3 -m compileall -q src tests` and `NOTEGRABBER_BIN=notegrabber python3 -m pytest -q`; when PySide6 is installed, offscreen GUI smoke tests should run.

## Recommended next steps

User priorities for the next work are **UI polish, speed/responsiveness, playback/playhead sync, and zoom/navigation polish**.

1. **Playback/playhead synchronization polish**: smooth interpolated playhead sync is implemented for waveform + heatmap, with MIDI-follow correction during Play both. Range-analysis MIDI preview renders in a local timeline and maps back to the full-song waveform/heatmap timeline, clamping at the selected range end. Continue manual checks with edited MIDI preview and Qt audio backend edge cases.
2. **Zoom/navigation polish**: note edits/clicks no longer compound the heatmap zoom by recalculating fit from the already-zoomed canvas. Pitch-row vertical zoom is implemented with Shift+wheel, and time zoom remains Ctrl+wheel; the left transcription box no longer contains zoom sliders. Continue improving horizontal zoom-out/in behavior, preserve cursor-centered zoom, add fit-to-range / fit-to-selection controls, and keep horizontal scroll position intuitive when zoom changes.
3. **Speed/responsiveness**: waveform/heatmap playhead updates now repaint only narrow old/new playhead regions instead of redrawing the full canvases. Continue large-file UX work with progress detail, cancel analysis/overview jobs, optional overview/heatmap level-of-detail caching, and background/debounced MIDI-preview rendering if TiMidity rendering becomes noticeably slow.
4. **UI polish**: heatmap view height is now capped relative to the window so vertical pitch zoom does not hide the inspector/sequence area, and the left-panel action buttons are placed above reserved/stub controls so Open/Analyze/Delete/Export remain visible. Continue refining the ToneTrace dark pro-DAW layout, especially control density, selected-region affordances, selected-note editing affordances, and visual hierarchy between overview waveform, detail heatmap, and sequence table.
5. **Editing workflow polish**: add keyboard nudging, undo/redo, and optional ghost previews during drag.
6. **Output/workflow polish**: add native CSV/minimap parity where useful, project/session save-load, and maybe export browser-tuned notes if the browser workflow remains relevant.
7. **Analysis comparison and quality**: add explicit compare mode for CQT vs Basic Pitch outputs and tolerant regression fixtures/metrics for real samples if legally/shareably possible.
8. Later, move toward plugin implementation using JUCE/DPF/iPlug2/NIH-plug.
