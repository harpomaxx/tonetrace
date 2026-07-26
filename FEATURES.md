# notegrabber feature summary

`notegrabber` is currently a CLI and local browser-viewer spike for noteGRABBER-style audio-to-MIDI workflows on Linux/free software.

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
```

The viewer defaults to `basic-pitch` and writes:

- `index.html`
- `heatmap.json`
- `analysis.mid`
- optional rendered `analysis.wav` via TiMidity++
- a copy of the original audio

Viewer capabilities:

- original-vs-rendered MIDI playback
- heatmap with extracted MIDI note overlay
- hover/click inspection of time, pitch, velocity, and activation
- show/hide overlay
- horizontal zoom and fit-to-width
- live threshold/min-duration re-extraction from the loaded heatmap
- detected sequence panel with full-phrase minimap
- onset-grouped note/chord table
- clickable sequence rows/blocks that jump playback
- copy current visible/tuned sequence as CSV

## Current limitation

Browser retuning updates the overlay/table in memory but does not yet export a new tuned MIDI file. Plugin formats such as VST/LV2/CLAP are not implemented yet.
