# ToneTrace

**Audio-to-MIDI transcription for Linux / free software.**

ToneTrace analyzes an audio sample, visualizes pitch salience as a piano-roll
heatmap, extracts candidate MIDI notes, and lets you compare the original audio
against the generated MIDI — then edit and export the result.

> ⚠️ **Work in progress.** This is an early Linux/free-software spike inspired by
> noteGRABBER-style workflows. It currently ships a CLI, a local browser viewer,
> a local upload server, and a native standalone GUI. There is **no VST / LV2 /
> CLAP plugin yet** — that is future work. Expect rough edges.
>
> The Python package and CLI are still named `notegrabber`; **ToneTrace** is the
> product name of the native GUI.

![ToneTrace GUI](docs/screenshot.png)

## Motivation

Transcribing a recorded phrase — a bass line, a vocal melody, a synth lead —
into editable MIDI usually means reaching for closed, Windows/macOS-first tools.
ToneTrace is an attempt to build that workflow on **Linux with free software and
open Python tooling**: load a sample, *see* the pitch content as a heatmap,
audition the original against the transcription, correct the notes by hand, and
export MIDI — all locally, no cloud, no proprietary plugin host required.

It is deliberately scoped as a spike: get the analyze → visualize → compare →
edit → export loop feeling good first, and move toward a real audio plugin later.

## Backends

Three transcription backends, selectable per run:

| Backend       | What it is | Best for |
|---------------|------------|----------|
| `basic-pitch` | [Spotify Basic Pitch](https://github.com/spotify/basic-pitch) / ONNX ML transcription | **Default and best for real audio.** Strong polyphonic note detection. |
| `cqt`         | [librosa](https://librosa.org/) Constant-Q Transform heatmap + heuristic extraction | Music-aligned heatmaps; supports instant in-GUI threshold retuning without rerunning a model. |
| `simple`      | Deterministic stdlib DSP baseline | Synthetic test fixtures; no ML/DSP dependencies. |

Tuning knobs (note sensitivity, split sensitivity, CQT threshold, minimum note
duration) are exposed on both the CLI and the GUI.

## Requirements

- **Linux** (developed and tested there; not tested on other platforms)
- **Python ≥ 3.10**
- Optional: **TiMidity++** on your `PATH` for rendering MIDI audio previews
  (`sudo apt install timidity` on Debian/Ubuntu)

## Installation

Using a virtual environment is recommended. **For the desktop app, `.[gui]` is
the extra to use** — it is a complete, ready-to-run GUI (audio decoding,
overview, and all backends included).

### Install from anywhere (no source checkout needed)

You can install straight from the Git repo into any environment, then run
`notegrabber-gui` from any directory:

```bash
python3 -m venv ~/.venvs/tonetrace && source ~/.venvs/tonetrace/bin/activate

# Install the GUI directly from GitHub:
pip install 'notegrabber[gui] @ git+https://github.com/harpomaxx/tonetrace.git'

notegrabber-gui          # launches from anywhere
```

Or build a wheel once and install that copy elsewhere (or on another machine):

```bash
git clone https://github.com/harpomaxx/tonetrace.git && cd tonetrace
python3 -m pip install build && python3 -m build      # writes dist/*.whl
# then, in any environment / on any machine:
pip install '/path/to/notegrabber-0.1.0-py3-none-any.whl[gui]'
```

### Editable install (for development)

```bash
git clone https://github.com/harpomaxx/tonetrace.git && cd tonetrace
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[gui]'
```

### Choosing extras

Any of the install commands above accepts a narrower extra in place of `gui`:

```
[gui]           # complete desktop app (GUI + audio decode + all backends)
[standalone]    # same set as [gui]; kept for scripts
[ml]            # CQT + Basic Pitch backends, no GUI
[basic-pitch]   # Basic Pitch backend only
[cqt]           # CQT backend only (librosa + numpy + soundfile)
[separate]      # stem separation (HT-Demucs ONNX, no PyTorch) — combine with others
(no extra)      # base package (simple backend, PCM WAV only)
```

## Usage

### Native GUI (ToneTrace)

```bash
notegrabber-gui                 # launch empty
notegrabber-gui input.mp3       # launch and open a file
notegrabber gui                 # equivalent via the CLI
```

Open an audio file, optionally drag a range on the waveform to analyze just a
section (handy for long files), pick a backend, and click **Analyze**. Then:

- Compare **Both** / **Audio** / **MIDI** playback with a synchronized playhead.
- Inspect the heatmap and MIDI note overlay; zoom (Ctrl+wheel time, Shift+wheel
  pitch) and scroll. Toggle **Notes only** to hide the heatmap and focus on the
  extracted notes.
- Read the stats strip under the waveform (note count, duration, estimated
  tempo, and detected key), which updates as you edit.
- Select, drag, resize, retune, and delete notes; edits re-render the MIDI
  preview.
- **Export** the edited notes as a Standard MIDI File.

### CLI

```bash
# Analyze to a MIDI file (+ optional heatmap JSON):
notegrabber analyze input.wav --out output.mid
notegrabber analyze input.wav --out output.mid --heatmap heatmap.json --backend basic-pitch

# Generate a self-contained browser viewer:
notegrabber visualize input.wav --out-dir viewer-dir

# Run a local upload/re-analysis web app:
notegrabber serve --out-dir out/server
```

### Stem separation

Split a mix into per-instrument stems, then transcribe the one you want. Uses
HT-Demucs via ONNX (pure numpy + onnxruntime, **no PyTorch**); the ~166 MB model
auto-downloads on first use.

```bash
pip install 'notegrabber[separate]'   # opt-in extra (not in [gui])

# Split into vocals / drums / bass / other:
notegrabber separate song.mp3 --out-dir stems/

# Only the stems you need, then transcribe one:
notegrabber separate song.mp3 --out-dir stems/ --stems bass,vocals
notegrabber analyze stems/song/bass.wav --out bass.mid --backend basic-pitch
```

`--model htdemucs_6s` adds `guitar` and `piano` stems (heavier; the piano stem is
weaker). Separation is high quality but not fast — expect roughly real-time on
CPU, so a 4-minute song takes a few minutes. A **live spinner with an elapsed
timer and a rough ETA** (on stderr) shows it is working (e.g.
`separating song.mp3   12.3s / ~40s`), followed by a tidy per-stem summary; the
stem file paths are printed on stdout (one per line) for scripting. The ETA is an
estimate from the audio length — it over-predicts on a GPU. Pass `--quiet` to
suppress the progress display. A GPU (`pip install onnxruntime-gpu`) makes it
dramatically faster.

Common tuning flags on `analyze` / `visualize`:

- `--threshold` — CQT heatmap-to-note activation threshold
- `--onset-threshold` / `--frame-threshold` — Basic Pitch thresholds
- `--min-duration` — minimum note length (seconds)

## Development

```bash
pip install -r requirements-dev.txt
pip install -e '.[standalone]'

# Run the test suite (GUI tests run offscreen when PySide6 is installed):
NOTEGRABBER_BIN=notegrabber python3 -m pytest -q
```

See [`AGENTS.md`](AGENTS.md) for the architecture and contribution notes,
[`FEATURES.md`](FEATURES.md) for the current feature list, and the
[GitHub issues](https://github.com/harpomaxx/tonetrace/issues) for the tracked
performance backlog.

## Status & roadmap

Implemented: CLI transcription, browser viewer, local server, and a native
PySide6 GUI with background analysis, waveform/heatmap views, playback
comparison, note editing, MIDI export, and a transcription stats strip
(note count, duration, estimated tempo, and detected key/scale).

Not yet: VST/LV2/CLAP plugin formats, project save/load, and undo/redo. The
near-term focus is UI polish, speed/responsiveness, and playback/zoom
refinement.

## License

Released under the [MIT License](LICENSE).
