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
- `basic-pitch` — Spotify Basic Pitch/ONNX ML transcription; current best backend for real samples. Defaults and note-creation controls (`infer_onsets`, frequency bounds) match NeuralNote's Basic Pitch usage for cleaner results.

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
- background waveform overview loading for WAV plus MP3/FLAC/OGG fallback previews through standalone/librosa dependencies
- background low-resolution full-song pitch overview for finding sample/chop regions before detailed analysis
- waveform drag selection with draggable edge/body handles that sync analysis-range start/duration controls for section-based transcription
- Basic Pitch/CQT/simple backend selector
- NeuralNote-inspired transcription controls plus analysis range controls
- background analysis worker with optional range-only analysis for long files, offset back onto the full-song timeline
- heatmap + MIDI rectangle piano-roll widget with Ctrl+wheel horizontal time zoom, Shift+wheel vertical pitch zoom, scrolling, and pixel-aggregated drawing for large/long analyses; the drawing uses a cached numpy activation matrix (with a pure-Python fallback when numpy is absent) to reduce whole columns at once
- piano roll spans the full-song timeline so the waveform and heatmap playheads share one time-to-pixel scale even during range analysis, while the canvas width stays viewport-bounded for huge files
- onset-grouped detected sequence table
- original/rendered-MIDI playback controls via Qt Multimedia
- seek both players by clicking the waveform, piano roll, or sequence rows
- smooth interpolated playhead overlays on waveform and piano roll, with drift resync that tolerates coarse backend position reporting and freezes while a player is buffering/stalled
- auto-follow scrolling that keeps the moving playhead visible when zoomed in past the viewport
- select/highlight note rectangles in the piano roll
- selected-note detail label with highlighted note name plus MIDI pitch/start/duration/velocity
- inspector fields for editing selected-note start, duration, pitch, and velocity
- piano-roll hover/selection handles and cursor feedback for moving notes or resizing either edge; in-progress drags repaint only the affected note rectangles and defer the full refresh/MIDI re-render to release
- delete selected notes with Delete/Backspace or the delete button
- sequence table and piano roll update after edits/deletion
- rendered MIDI WAV preview updates after inspector edits, deletes, CQT retunes, and committed piano-roll drags when TiMidity++ is available
- "Notes only (hide heatmap)" toggle to view just the extracted MIDI notes without the pitch-salience heatmap
- Open/Analyze/Delete/Export in a horizontal action bar above the waveform (not the left panel), keeping the transcription controls visible
- always-visible stats strip under the waveform: note count, duration, estimated tempo (BPM), and detected musical key/scale, refreshed on every note edit
- polished first-pass dark pro-DAW Qt theme with warm red/orange/yellow accents, rounded card panels, compact action pad, SVG button icons, and explanatory slider tooltips
- export current edited/tuned notes to MIDI

Install GUI dependencies with:

```bash
python3 -m pip install -e '.[gui]'
# or all standalone dependencies:
python3 -m pip install -e '.[standalone]'
```

## Current limitation / next focus

Browser retuning updates the overlay/table in memory but does not yet export a new tuned MIDI file. The native GUI can export analyzed/tuned/edited notes, compare original-vs-MIDI playback, perform basic selection/inspector/drag/resize/delete editing, and re-render the MIDI WAV preview after committed edits when TiMidity++ is available.

Near-term native GUI focus:

- continue playback/playhead sync edge cases (edited MIDI preview, Qt backend stall/buffering behavior on large files); smooth interpolated sync, full-song timeline mapping, and follow-scroll are implemented
- improve heatmap zoom/navigation, especially zoom-out behavior, cursor-centered Ctrl+wheel zoom, and fit-to-selection/full-song actions
- improve speed/responsiveness for large files: the heatmap, waveform, and overview paints are now numpy-vectorized, the per-drag repaint is partial, and the MIDI-preview render runs off the UI thread; still pending are cancel/progress/LOD caching (see the [GitHub issues](https://github.com/harpomaxx/tonetrace/issues))
- continue UI polish for the sampler workflow and selected-region editing

Plugin formats such as VST/LV2/CLAP are not implemented yet.
