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
notegrabber visualize input.wav --out-dir viewer-dir
```

Backends:

- `simple` — deterministic stdlib DSP baseline for synthetic test fixtures.
- `cqt` — librosa Constant-Q Transform backend for more music-aligned heatmaps and baseline MIDI extraction.

The visualization command defaults to CQT and writes:

- `index.html`
- `heatmap.json`
- `analysis.mid`
- rendered `analysis.wav` via TiMidity++ when available
- a copy of the original audio

The browser viewer overlays extracted MIDI rectangles on the heatmap and supports hover/click note inspection.

## Test workflow

Install dev dependencies:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e '.[cqt]'
```

Run tests:

```bash
NOTEGRABBER_BIN=notegrabber python3 -m pytest -q
```

Incremental markers:

```bash
python3 -m pytest -m tier0
python3 -m pytest -m tier1
python3 -m pytest -m tier2
python3 -m pytest -m tier3
python3 -m pytest -m heatmap
python3 -m pytest -m cqt
```

## Local sample

`oxi.wav` is a local real-audio sample used for manual testing. Generated outputs are under `out/`, especially `out/oxi-viewer/index.html`.

These are working artifacts, not core source code.

## Development notes

- Keep the existing CLI contract stable unless tests/docs are updated together.
- Prefer adding tests before changing analysis behavior.
- Do not commit generated caches: `.pytest_cache/`, `__pycache__/`, etc.
- Treat `.pi-subagents/` as agent/runtime artifacts, not project source.
- Current CQT extraction is still heuristic. For production-quality transcription, the next major step is a `basic-pitch` backend or a native ONNX/NeuralNote-inspired pipeline.

## Recommended next steps

1. Add `--backend basic-pitch` using Spotify Basic Pitch for stronger ML transcription.
2. Improve note grouping/threshold controls for CQT.
3. Add UI controls to the generated HTML viewer: threshold slider, show/hide note overlay, zoom controls.
4. Later, move toward a native Linux standalone/plugin implementation using JUCE/DPF/iPlug2/NIH-plug.
