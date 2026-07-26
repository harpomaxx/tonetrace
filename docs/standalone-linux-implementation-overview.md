# Standalone Linux implementation overview

This document summarizes the current `notegrabber` spike and the implementation approach used so another agent can quickly build the same idea as a native standalone Linux app.

## Goal

Build a free-software, Linux-friendly clone/spike of a noteGRABBER-style workflow:

1. Load an audio sample.
2. Analyze pitch salience over time.
3. Extract candidate MIDI notes.
4. Visualize the heatmap and note sequence.
5. Compare original audio with rendered/generated MIDI.
6. Allow interactive tuning of note extraction.

The current repository implements this as a Python CLI, local browser viewer/server, and an early PySide6 standalone GUI. It does **not** implement VST/LV2/CLAP yet.

## Current architecture

```text
src/notegrabber/
├── cli.py          # CLI commands: analyze, visualize, serve
├── analyzer.py     # analysis backends, heatmaps, MIDI note extraction
├── midi.py         # minimal Standard MIDI File writer
├── visualizer.py   # self-contained HTML viewer generation
├── server.py       # local upload/re-analysis web server
├── gui/            # PySide6 standalone GUI app, state/models, worker, widgets
├── __init__.py
└── __main__.py
```

Key entry points:

- [`src/notegrabber/cli.py`](../src/notegrabber/cli.py)
- [`src/notegrabber/analyzer.py`](../src/notegrabber/analyzer.py)
- [`src/notegrabber/visualizer.py`](../src/notegrabber/visualizer.py)
- [`src/notegrabber/server.py`](../src/notegrabber/server.py)
- [`src/notegrabber/midi.py`](../src/notegrabber/midi.py)

Tests:

- [`tests/test_analyze_audio_to_midi.py`](../tests/test_analyze_audio_to_midi.py)
- [`tests/test_analyze_heatmap.py`](../tests/test_analyze_heatmap.py)
- [`tests/test_cqt_backend.py`](../tests/test_cqt_backend.py)
- [`tests/test_basic_pitch_backend.py`](../tests/test_basic_pitch_backend.py)
- [`tests/test_visualize.py`](../tests/test_visualize.py)
- [`tests/test_cli_smoke.py`](../tests/test_cli_smoke.py)
- [`tests/test_gui_state.py`](../tests/test_gui_state.py)
- [`tests/test_gui_app.py`](../tests/test_gui_app.py)

Project notes:

- [`AGENTS.md`](../AGENTS.md)
- [`FEATURES.md`](../FEATURES.md)

## Commands implemented

### Analyze

```bash
notegrabber analyze input.wav --out output.mid
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend cqt
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend basic-pitch
```

### Visualize

```bash
notegrabber visualize input.wav --out-dir viewer-dir
```

This generates a static local viewer directory containing:

- `index.html`
- `heatmap.json`
- `analysis.mid`
- optional `analysis.wav` rendered by TiMidity++
- a copy of the original audio

### Native GUI

```bash
notegrabber-gui
notegrabber gui
```

The GUI currently opens audio files, draws a waveform, runs analysis in a background worker, displays a heatmap with MIDI rectangles, shows an onset-grouped sequence table, supports CQT retuning from the loaded heatmap, compares original vs rendered MIDI playback with Qt Multimedia, seeks both players from the waveform/piano roll/sequence table, selects/highlights piano-roll notes, edits selected notes through an inspector, drags/resizes notes directly in the piano roll, deletes selected notes into an edited/tuned list, re-renders the MIDI WAV preview after committed edits when rendering is enabled, and exports the current note list as MIDI. It implements milestones 0–4 in basic form. Editing polish such as visible handles/cursors/undo is a future milestone.

### Serve/upload/re-analyze

```bash
notegrabber serve --out-dir out/server
```

This starts a local web app at `http://127.0.0.1:8765/`. Uploading an audio file runs the Python analyzer and creates a new viewer with fresh MIDI, heatmap, waveform, and note sequence. This exists because a static HTML file cannot run Basic Pitch/Python locally in the browser.

## Analysis backends

### 1. `simple`

Implemented in [`analyzer.py`](../src/notegrabber/analyzer.py).

Purpose:

- deterministic test baseline
- no heavy dependencies
- works on synthetic sine-wave fixtures

Approach:

- read PCM WAV with stdlib `wave`
- find non-silent RMS segments
- correlate each segment against MIDI note frequencies 21–108
- group detected pitches into MIDI notes

Use this backend for fast contract tests, not real transcription.

### 2. `cqt`

Implemented in [`build_cqt_heatmap`](../src/notegrabber/analyzer.py).

Dependencies:

- `librosa`
- `numpy`

Approach:

- load audio with `librosa.load`
- compute Constant-Q Transform aligned to MIDI notes 21–108
- normalize magnitudes to `0..1`
- export heatmap JSON
- extract notes heuristically by thresholding local peaks over time

Why CQT:

- frequency bins align with musical pitch
- produces a useful piano-roll-like heatmap
- better visualization than raw FFT bins

Limitations:

- MIDI extraction is heuristic
- threshold-sensitive
- less reliable than Basic Pitch on real samples

### 3. `basic-pitch`

Implemented in [`analyze_basic_pitch`](../src/notegrabber/analyzer.py).

Dependencies:

- `basic-pitch[onnx]`
- `onnxruntime`
- `numpy`

Approach:

- use Spotify Basic Pitch ONNX model
- call `basic_pitch.inference.predict`
- convert note events into internal `MidiNote`
- convert model note probabilities into the same heatmap JSON schema

This is currently the best backend for real samples.

Tuning flags:

```bash
--onset-threshold 0.5
--frame-threshold 0.3
--min-duration 0.05
```

## Heatmap JSON schema

All backends that support heatmap output use this common structure:

```json
{
  "version": 1,
  "backend": "basic-pitch",
  "sample_rate": 86,
  "hop_size": 1,
  "window_size": 1,
  "midi_notes": [21, 22, 23],
  "frames": [
    {
      "time_seconds": 0.0,
      "activations": [0.0, 0.2, 0.9]
    }
  ]
}
```

The important invariant is:

```text
len(frame.activations) == len(midi_notes)
```

For CQT:

- `sample_rate` is the source sample rate
- `hop_size` is audio samples per frame

For Basic Pitch:

- `sample_rate` is the annotation frame rate (`86` fps)
- `hop_size` is `1`
- `window_size` is `1`

This simplified Basic Pitch timing makes browser heatmap extraction and display consistent.

## MIDI writing and timing

MIDI writing lives in [`midi.py`](../src/notegrabber/midi.py).

The critical timing fix:

```python
TICKS_PER_BEAT = 480
TEMPO_MICROSECONDS_PER_BEAT = 500_000  # 120 BPM
TICKS_PER_SECOND = round(TICKS_PER_BEAT * 1_000_000 / TEMPO_MICROSECONDS_PER_BEAT)
```

At 120 BPM and 480 ticks/beat, there are 960 ticks/second. Earlier versions wrote 480 ticks/second while declaring 120 BPM, causing generated MIDI to play at 2× speed.

A standalone app must keep these three values internally consistent:

1. MIDI ticks per beat
2. tempo metadata
3. conversion from seconds to ticks

## Browser viewer features

Generated by [`visualizer.py`](../src/notegrabber/visualizer.py).

Implemented features:

- original audio playback
- rendered MIDI audio playback when TiMidity++ is available
- audio waveform/signal preview
- file picker for waveform/playback preview
- heatmap canvas
- MIDI note overlay
- hover inspection: time, pitch, activation
- click note to jump playback
- show/hide overlay
- horizontal zoom
- fit-to-width
- live threshold/min-duration re-extraction from already loaded heatmap
- full phrase minimap / sequence overview
- detected sequence table grouped by onset/chord
- click row/block to jump playback
- copy currently visible/tuned sequence as CSV

Important static-viewer limitation:

- Selecting a new file inside a generated `index.html` changes only browser playback and waveform preview.
- It does **not** rerun transcription or produce new heatmap/MIDI because the static browser page cannot run Python/ONNX.
- Use `notegrabber serve` for browser-based upload and fresh analysis.

## Local upload server

Implemented in [`server.py`](../src/notegrabber/server.py).

Purpose:

- make “select a file and generate new MIDI/heatmap/notes” possible from a browser
- keep everything local
- avoid needing a full web framework

Implementation:

- stdlib `http.server.ThreadingHTTPServer`
- multipart upload via stdlib `cgi.FieldStorage`
- saves uploads under `out/server/uploads/`
- runs `create_visualization(...)`
- redirects to `/viewer/<analysis-id>/index.html`

For large files:

- Basic Pitch can take seconds to minutes depending on length and hardware
- generated `heatmap.json` and `index.html` can become large
- heatmap canvas is horizontally compressed above a safe maximum width to avoid browser canvas limits

## Dependencies

Configured in [`pyproject.toml`](../pyproject.toml) and [`requirements-dev.txt`](../requirements-dev.txt).

Optional groups:

```toml
[project.optional-dependencies]
cqt = ["librosa>=0.10"]
basic-pitch = ["basic-pitch[onnx]>=0.4"]
ml = ["librosa>=0.10", "basic-pitch[onnx]>=0.4"]
gui = ["PySide6>=6.7", "pyqtgraph>=0.13"]
standalone = ["librosa>=0.10", "basic-pitch[onnx]>=0.4", "PySide6>=6.7", "pyqtgraph>=0.13"]
```

TiMidity++ is optional and external. It is used to render MIDI into WAV for browser playback.

Linux install example:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pip install -e '.[standalone]'
sudo apt install timidity
```

## Test strategy

The project is test-first around CLI contracts.

Core ideas:

- Generate deterministic synthetic WAV fixtures in Python.
- Use exact tests for simple sine fixtures.
- Use optional/skipped tests for dependency-heavy backends.
- Verify MIDI timing in real seconds, not just note names.
- Verify viewer HTML contains the expected UI hooks.

Run:

```bash
NOTEGRABBER_BIN=notegrabber python3 -m pytest -q
```

Current expected result with ML and GUI dependencies installed:

```text
36 passed
```

## Guidance for a native standalone Linux app

A native app could keep the same internal architecture, replacing the generated HTML with a desktop UI.

Recommended layers:

### Analysis layer

Keep backend abstraction:

```text
Audio file -> backend -> { MidiNote[], Heatmap }
```

Backends:

- Basic Pitch ONNX as primary
- CQT as visualization/fallback
- simple backend only for tests

For native implementation:

- use ONNX Runtime directly for Basic Pitch
- use a C++ CQT implementation or call a DSP library
- keep heatmap schema or equivalent internal model

### Data model

Keep explicit structures:

```text
MidiNote {
  pitch
  start_seconds or start_tick
  duration_seconds or duration_ticks
  velocity
}

Heatmap {
  backend
  midi_notes
  frames[{ time_seconds, activations[] }]
}
```

### UI layer

A standalone Linux app should provide:

- file picker
- waveform overview
- heatmap canvas
- MIDI overlay
- piano-roll sequence overview
- grouped note/chord list
- original/MIDI playback A/B comparison
- threshold/onset/frame/min-duration controls
- export tuned MIDI

Good toolkit candidates:

- Qt/PySide or Qt/C++ for a fast standalone app
- GTK4/libadwaita for GNOME-style Linux UI
- Tauri/Electron only if web UI reuse is more important than native footprint
- JUCE later if moving toward plugin/DAW integration

### Audio/MIDI playback

Options:

- use FluidSynth for MIDI rendering/playback
- use TiMidity++ as a simple external renderer
- use a native audio engine for synchronized original/MIDI playback

### Critical next implementation detail

The native GUI now makes tuned/exported notes first-class for CQT retuning plus selection, inspector edits, drag/resize edits, and deletion edits. It also re-renders the MIDI preview WAV after committed edits when rendering is enabled; an intentionally empty note list produces a silent WAV preview so stale MIDI is not heard. The browser viewer still retunes only in memory and cannot export a new tuned MIDI file. For the native GUI, the next critical step is editing polish and persistence:

1. user edits/deletes notes
2. sequence table and piano-roll overlay update immediately
3. playback preview and export use edited notes
4. future work can preserve edits in a project/session file

## Recommended next milestones

1. Add editing polish: visible resize handles/cursors, keyboard nudging, undo/redo.
2. Move edited MIDI-preview rendering off the GUI thread if TiMidity rendering becomes noticeably slow.
3. Add richer GUI playback polish: volume controls and Qt audio error display.
4. Add compare mode: Basic Pitch vs CQT side-by-side.
5. Add large-file UX: progress indicator detail, cancel button, and background worker cancellation.
6. Add persistent project/session format containing audio path, backend params, heatmap path, and tuned notes.
7. Move toward native standalone app packaging and later plugin work.
