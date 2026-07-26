# ToneTrace / notegrabber feature summary

**ToneTrace** is the chosen product/app name for the native GUI. The underlying Python package and CLI are still named `notegrabber` for now. The project is currently a CLI, local browser-viewer, local upload server, and native standalone GUI spike for audio-to-MIDI workflows on Linux/free software.

## CLI analysis

```bash
notegrabber analyze input.wav --out output.mid
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend basic-pitch
```

Supported backends:

- `simple` — deterministic stdlib DSP baseline for synthetic tests.
- `cqt` — librosa Constant-Q Transform heatmap plus heuristic MIDI extraction.
- `basic-pitch` — Spotify Basic Pitch/ONNX ML transcription; current best backend for real samples.

Tuning flags:

- `--threshold` for CQT heatmap-to-note extraction.
- `--onset-threshold` for Basic Pitch.
- `--frame-threshold` for Basic Pitch.
- `--min-duration` for minimum extracted note length.

## Heatmap JSON

`--heatmap` writes a machine-readable pitch salience document with:

- backend name
- sample/frame timing metadata
- MIDI note rows 21–108
- per-frame normalized activations

## Browser viewer

```bash
notegrabber visualize input.wav --out-dir viewer-dir
notegrabber serve --out-dir out/server
```

The viewer defaults to `basic-pitch` and writes:

- `index.html`
- `heatmap.json`
- `analysis.mid`
- optional rendered `analysis.wav` via TiMidity++
- a copy of the original audio

Viewer capabilities:

- original-vs-rendered MIDI playback
- browser file picker in static viewers to audition another audio file and draw its waveform/signal preview
- heatmap with extracted MIDI note overlay
- hover/click inspection of time, pitch, velocity, and activation
- show/hide overlay
- horizontal zoom and fit-to-width
- live threshold/min-duration re-extraction from the loaded heatmap
- detected sequence panel with full-phrase minimap
- onset-grouped note/chord table
- clickable sequence rows/blocks that jump playback
- copy current visible/tuned sequence as CSV

## Local upload/re-analysis

Static `visualize` output cannot run Python in the browser, so selecting another file inside an already generated viewer changes playback/waveform preview only. To analyze newly selected files from a browser, run the local server:

```bash
notegrabber serve --out-dir out/server
```

Then open the printed `http://127.0.0.1:8765/` URL, upload an audio file, and the server will generate a fresh viewer with new MIDI, heatmap, waveform, notes, and a rendered MIDI-audio preview when TiMidity++ is available. Use `--no-render-midi` to skip rendering for faster/lighter large-file tests.

## Native standalone GUI

```bash
notegrabber-gui
notegrabber gui
```

The PySide6 GUI follows the standalone GUI plan in `docs/standalone-gui-plan/` and currently includes:

- open audio file action
- waveform overview
- Basic Pitch/CQT/simple backend selector
- NeuralNote-inspired transcription controls
- background analysis worker
- heatmap + MIDI rectangle piano-roll widget
- onset-grouped detected sequence table
- original/rendered-MIDI playback controls via Qt Multimedia
- seek both players by clicking the waveform, piano roll, or sequence rows
- playhead overlays on waveform and piano roll
- select/highlight note rectangles in the piano roll
- selected-note detail label with highlighted note name plus MIDI pitch/start/duration/velocity
- inspector fields for editing selected-note start, duration, pitch, and velocity
- piano-roll drag editing: move time/pitch and resize note boundaries from either edge
- delete selected notes with Delete/Backspace or the delete button
- sequence table and piano roll update after edits/deletion
- rendered MIDI WAV preview updates after inspector edits, deletes, CQT retunes, and committed piano-roll drags when TiMidity++ is available
- polished first-pass dark pro-DAW Qt theme with warm red/orange/yellow accents, rounded card panels, compact action pad, SVG button icons, and explanatory slider tooltips
- export current edited/tuned notes to MIDI

Install GUI dependencies with:

```bash
python3 -m pip install -e '.[gui]'
# or all standalone dependencies:
python3 -m pip install -e '.[standalone]'
```

## Current limitation

Browser retuning updates the overlay/table in memory but does not yet export a new tuned MIDI file. The native GUI can export analyzed/tuned/edited notes, compare original-vs-MIDI playback, perform basic selection/inspector/drag/resize/delete editing, and re-render the MIDI WAV preview after committed edits when TiMidity++ is available. Plugin formats such as VST/LV2/CLAP are not implemented yet.
