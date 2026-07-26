# Implementation plan: native Linux standalone app

## Summary recommendation

Implement a Python/Qt standalone app first:

```text
src/notegrabber/gui/
├── app.py              # QApplication entry point
├── main_window.py      # shell, menus, toolbar, layout
├── state.py            # project state/dataclasses
├── analysis_worker.py  # QThread/QRunnable wrapper around analyzer.py
├── widgets/
│   ├── waveform.py     # waveform overview + seek/playhead
│   ├── piano_roll.py   # heatmap + note overlay + keyboard axis
│   ├── controls.py     # NeuralNote-like controls
│   ├── sequence.py     # onset-grouped table/minimap
│   └── transport.py    # playback buttons/volume
└── resources/
```

Expose it as:

```bash
notegrabber-gui
notegrabber gui  # optional CLI subcommand alias
```

## Data flow

```text
Open audio
  -> create ProjectState(audio_path)
  -> draw waveform preview
  -> user clicks Analyze
  -> AnalysisWorker calls analyzer.analyze_basic_pitch/build heatmap
  -> state receives Heatmap + MidiNote[] + output paths
  -> render MIDI preview WAV when possible
  -> update piano roll, sequence table, minimap
  -> user tunes thresholds
  -> re-extract notes from heatmap in memory
  -> preview/export tuned MIDI
```

## Core models

Create explicit GUI-facing models rather than passing raw JSON everywhere:

```python
@dataclass
class GuiMidiNote:
    pitch: int
    start_seconds: float
    duration_seconds: float
    velocity: int
    source: str = "basic-pitch"

@dataclass
class GuiHeatmap:
    backend: str
    midi_notes: list[int]
    frame_times: list[float]
    activations: np.ndarray  # shape: frames x notes

@dataclass
class ProjectState:
    audio_path: Path | None
    rendered_midi_wav: Path | None
    heatmap: GuiHeatmap | None
    extracted_notes: list[GuiMidiNote]
    tuned_notes: list[GuiMidiNote] | None  # None means use extracted notes; [] means intentionally no notes
    backend: str
    threshold: float
    onset_threshold: float
    frame_threshold: float
    min_duration: float
```

Conversion helpers can live in `state.py`:

- heatmap JSON dict -> `GuiHeatmap`
- `MidiNote` ticks -> seconds
- tuned GUI notes -> `MidiNote` ticks for export
- note-list editing helpers such as delete/update that return edited copies

## Milestone 0: dependency and skeleton (implemented)

Add optional GUI dependencies:

```toml
[project.optional-dependencies]
gui = [
  "PySide6>=6.7",
  "pyqtgraph>=0.13",
]
standalone = [
  "notegrabber[ml,gui]"
]
```

Implementation:

- add `src/notegrabber/gui/app.py`
- add `notegrabber-gui` script entry
- show empty main window with NeuralNote-like panels
- add smoke test that imports/constructs widgets in offscreen mode when PySide6 is installed

Acceptance:

- `notegrabber-gui --help` or import entry works
- no analysis required yet

## Milestone 1: static standalone viewer parity (implemented)

Implement:

- open file action
- waveform widget
- analyze button runs Basic Pitch in worker thread
- progress/status text
- heatmap widget displays current heatmap
- note overlay shows extracted notes
- sequence table lists grouped notes/chords
- export MIDI writes current notes

Use current analyzer functions; do not port Basic Pitch.

Acceptance:

- open `oxi.wav`
- analyze with Basic Pitch
- display heatmap and notes
- export MIDI with correct timing
- tests for conversion/model code

## Milestone 2: NeuralNote-style controls (implemented in basic form)

Implement left panel:

- Note sensitivity -> Basic Pitch frame threshold or CQT threshold
- Split sensitivity -> Basic Pitch onset threshold
- Min note duration -> shared min duration
- Pitch bend selector -> disabled/No Pitch Bend initially
- Scale quantize -> disabled initially
- Time quantize -> disabled initially

Behavior:

- for Basic Pitch, changing thresholds may either:
  1. rerun Basic Pitch, or
  2. retune from probability heatmap where possible
- for CQT, changing threshold/min duration can re-extract from heatmap immediately

Acceptance:

- changing controls updates overlay/table
- export uses tuned notes, not stale notes

## Milestone 3: playback comparison (implemented in initial form)

Implemented:

- original audio playback via `QMediaPlayer`
- rendered MIDI WAV playback via existing renderer
- play/pause both from same time
- clickable heatmap/note/table seeks both players
- playhead line over waveform and piano roll

Acceptance:

- original and MIDI start together
- clicking the waveform, piano roll, or sequence rows jumps playback
- missing TiMidity shows useful warning but app remains usable

## Milestone 4: editing/export (implemented in basic form)

Implemented note editing:

- select note by clicking a piano-roll rectangle
- highlight selected note and show pitch/start/duration/velocity details
- delete selected note with Delete/Backspace or the delete button
- edit selected note start, duration, pitch, and velocity in the inspector
- drag selected/hovered notes horizontally to move them in time
- drag note left/right edges to resize start/end boundaries
- drag note bodies vertically to change pitch to the target piano-roll row
- edited/deleted notes are reflected in the piano roll, sequence table, exported MIDI, and re-rendered MIDI WAV preview

Still pending polish:

- visual resize handles/cursors/ghost preview before commit
- keyboard nudging and undo/redo
- off-main-thread/debounced MIDI preview rendering if synchronous rendering becomes slow

Acceptance:

- edited/deleted notes are reflected in sequence table and exported MIDI

## Milestone 5: packaging

Development package:

```bash
python3 -m pip install -e '.[ml,gui]'
notegrabber-gui
```

Binary packaging:

1. PyInstaller one-folder build.
2. AppImage wrapper.
3. Flatpak later.

Packaging risks:

- PySide6 Qt plugins must be bundled correctly.
- ONNX Runtime and Basic Pitch model files must be included.
- SoundFont/TiMidity/FluidSynth choice affects whether MIDI preview works out of the box.

## Suggested tests

Headless/unit tests:

- heatmap JSON -> GUI model conversion
- GUI model notes -> MIDI export conversion
- threshold retuning produces deterministic note count on synthetic fixtures
- app entry import does not fail when PySide6 is installed

Manual tests:

- open `oxi.wav`
- analyze with Basic Pitch
- compare original and MIDI preview
- adjust min duration/threshold
- export tuned MIDI and inspect length/pitches with `mido`

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Basic Pitch blocks UI | Always run analysis in `QThread`/`QRunnable`. |
| Large heatmaps are slow to draw | Use raster image for heatmap, vector/painted overlay for notes; downsample when zoomed out. |
| Qt audio backend differences across distros | Keep rendered WAV playback and surface warnings; test on target distro. |
| Bundling ONNX/Basic Pitch is heavy | Keep source install first; package after GUI works. |
| Exact NeuralNote look could imply copying | Use original styling inspired by layout only; avoid names/assets. |

## Current implementation checkpoint

The project has advanced beyond the original first implementation target. Current native GUI support includes Milestones 0–4 in basic form:

- `notegrabber-gui` / `notegrabber gui` launch the PySide6 app.
- file picker or startup argument loads audio and draws waveform.
- Analyze runs Basic Pitch/CQT/simple in a `QThread` worker.
- heatmap and MIDI note rectangles are displayed in a custom piano-roll widget.
- sequence table groups notes/chords by onset.
- Qt Multimedia compares original audio and rendered MIDI WAV.
- waveform, piano roll, and sequence rows seek playback/playhead.
- CQT threshold/min-duration retuning updates notes in memory.
- clicking a note selects/highlights it and shows details.
- Delete/Backspace/delete button removes selected notes from the tuned note list.
- selected-note inspector edits start, duration, pitch, and velocity.
- piano-roll drag moves notes in time/pitch and edge-drag resizes start/end boundaries.
- committed edits/deletes/CQT retunes re-render the MIDI WAV preview when rendering is enabled.
- Export MIDI writes current analyzed/tuned/edited/deleted notes.

Next implementation target: add editing polish such as visual handles/cursors, undo/redo, debounced/background MIDI-preview rendering if needed, and project/session persistence.
