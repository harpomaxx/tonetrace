# AGENTS.md

## Project idea

This repository is a Linux/free-software clone/spike inspired by noteGRABBER-style workflows: analyze an audio sample, visualize pitch salience as a piano-roll heatmap, extract candidate MIDI notes, and let the user compare the original audio with the generated MIDI.

Current scope is **CLI + local web visualization only**. No VST/LV2/CLAP plugin has been implemented yet.

## Current implementation

Python package under `src/notegrabber/`:

- `cli.py` — command-line interface.
- `analyzer.py` — audio analysis backends and heatmap generation.
- `midi.py` — minimal Standard MIDI File writer.
- `visualizer.py` — generates a self-contained browser viewer.

Main commands:

```bash
notegrabber analyze input.wav --out output.mid
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend cqt
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend basic-pitch
notegrabber visualize input.wav --out-dir viewer-dir
notegrabber visualize input.wav --out-dir viewer-dir --backend basic-pitch --onset-threshold 0.5 --frame-threshold 0.3 --min-duration 0.05
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

The browser viewer overlays extracted MIDI rectangles on the heatmap and supports hover/click note inspection, show/hide overlay, horizontal zoom, fit-to-width, and live threshold/min-duration re-extraction from the loaded heatmap. It also includes a **Detected sequence** panel with a whole-phrase piano-roll overview/minimap, onset-grouped note/chord table, clickable rows/blocks that jump playback, and CSV copy for the currently visible/tuned sequence.

Tuning flags available on both `analyze` and `visualize`:

- `--threshold` — CQT heatmap-to-note activation threshold.
- `--onset-threshold` — Basic Pitch onset threshold.
- `--frame-threshold` — Basic Pitch frame threshold.
- `--min-duration` — minimum note duration in seconds for CQT/Basic Pitch extraction.

## Test workflow

Install dev dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e '.[ml]'
```

Run tests:

```bash
NOTEGRABBER_BIN=notegrabber python3 -m pytest -q
```

Current expected result: **16 passed**.

Incremental markers:

```bash
python3 -m pytest -m tier0
python3 -m pytest -m tier1
python3 -m pytest -m tier2
python3 -m pytest -m tier3
python3 -m pytest -m heatmap
python3 -m pytest -m cqt
python3 -m pytest -m basic_pitch
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

## Recommended next steps

1. Add export of browser-tuned notes back to MIDI (the viewer currently retunes overlays/tables in-browser but does not rewrite MIDI).
2. Add explicit compare mode for CQT vs Basic Pitch outputs on one page.
3. Add tolerant regression fixtures/metrics for real samples if legally/shareably possible.
4. Later, move toward a native Linux standalone/plugin implementation using JUCE/DPF/iPlug2/NIH-plug.
