# notegrabber CLI contract tests

This suite defines the expected command-line contract for a future `notegrabber` audio-to-MIDI tool. It does **not** include or implement the production tool.

## Expected CLI

The executable is selected with `NOTEGRABBER_BIN`; when the variable is unset the tests use `notegrabber` from `PATH`.

```sh
$NOTEGRABBER_BIN analyze <input-audio.wav> --out <output.mid>
```

Contract expectations:

- `notegrabber --help` exits successfully and documents the `analyze` command.
- `notegrabber analyze --help` exits successfully and documents the input audio and `--out` MIDI output arguments.
- Successful analysis writes a readable Standard MIDI File at the requested output path.
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
pytest
```

Run incrementally while building the tool:

```sh
pytest -m tier0   # CLI shape only
pytest -m tier1   # single A4 sine -> MIDI note 69
pytest -m tier2   # two monophonic notes in order
pytest -m tier3   # simple polyphonic C-major chord
pytest -m edge    # silence and error handling
```

If the CLI is not installed, tests that execute the CLI are skipped with a message naming `NOTEGRABBER_BIN`. To run against a local build:

```sh
NOTEGRABBER_BIN=/path/to/notegrabber pytest -m cli
```

Audio fixtures are generated deterministically during each test with Python's standard `wave` and `math` modules; no binary fixture files are checked into the repository.
