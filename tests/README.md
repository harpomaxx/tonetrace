# notegrabber CLI contract tests

This suite defines the expected contract for the current `notegrabber` audio-to-MIDI spike: CLI analysis, CLI stem separation, and optional PySide6 GUI model/widget behavior.

## Expected CLI

The executable is selected with `NOTEGRABBER_BIN`; when the variable is unset the tests use `notegrabber` from `PATH`.

```sh
$NOTEGRABBER_BIN analyze <input-audio.wav> --out <output.mid>
$NOTEGRABBER_BIN analyze <input-audio.wav> --out <output.mid> --heatmap <output.json>
$NOTEGRABBER_BIN analyze <input-audio.wav> --out <output.mid> --heatmap <output.json> --backend cqt
$NOTEGRABBER_BIN analyze <input-audio.wav> --out <output.mid> --heatmap <output.json> --backend basic-pitch
$NOTEGRABBER_BIN separate <input-audio.wav> --out-dir <stems-dir>
$NOTEGRABBER_BIN gui
notegrabber-gui
```

Contract expectations:

- `notegrabber --help` exits successfully and documents the `analyze` command.
- `notegrabber analyze --help` exits successfully and documents the input audio and `--out` MIDI output arguments.
- Successful analysis writes a readable Standard MIDI File at the requested output path.
- With `--heatmap`, successful analysis also writes heatmap JSON containing `midi_notes` and `frames` with per-note activations.
- `--backend simple` is the deterministic stdlib DSP baseline; `--backend cqt` uses librosa's Constant-Q Transform for a more music-aligned heatmap and baseline MIDI extraction; `--backend basic-pitch` uses Spotify Basic Pitch for ML transcription.
- `separate` splits a mix into per-instrument stem WAVs (vocals/drums/bass/other) via HT-Demucs ONNX; each stem can then be transcribed with `analyze`.
- `gui`/`notegrabber-gui` launches the optional PySide6 standalone app with waveform, analysis, playback, note editing, and MIDI export.
- A monophonic A4 sine wave produces MIDI note 69.
- Two sequential sine notes produce the expected pitches in order.
- A simple triad/chord produces the expected simultaneous pitches.
- Silence produces no MIDI note-on events.
- Missing or invalid input fails with a non-zero exit code and does not create the requested output file.

## Running

Install test dependencies:

```sh
python -m pip install -r requirements-dev.txt
```

Run the tests:

```sh
NOTEGRABBER_BIN=notegrabber pytest
```

Run incrementally while building the tool:

```sh
pytest -m tier0   # CLI shape only
pytest -m tier1   # single A4 sine -> MIDI note 69
pytest -m tier2   # two monophonic notes in order
pytest -m tier3    # simple polyphonic C-major chord
pytest -m edge     # silence and error handling
pytest -m heatmap  # A4 sine -> MIDI note 69 plus heatmap JSON contract
pytest -m cqt          # optional librosa/CQT backend checks
pytest -m basic_pitch  # optional Spotify Basic Pitch backend checks
pytest -m gui          # GUI model/editing tests, plus PySide6 widget smoke tests when installed
```

If the CLI is not installed, tests that execute the CLI are skipped with a message naming `NOTEGRABBER_BIN`. To run against a local build:

```sh
NOTEGRABBER_BIN=/path/to/notegrabber pytest -m cli
```

Audio fixtures are generated deterministically during each test with Python's standard `wave` and `math` modules; no binary fixture files are checked into the repository.
