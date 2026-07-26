# UI specification: NeuralNote-inspired standalone app

## Reference layout observations

The supplied NeuralNote screenshot has these major zones:

1. **Top brand/transport bar**
   - logo/title
   - record/reset/trash-ish actions
   - previous/play/next style transport
   - settings and volume
2. **Top waveform strip**
   - small metadata block: MIDI/file/tempo
   - waveform display
   - file/tempo context
3. **Left control column**
   - `Transcription` section with three large knobs:
     - note sensitivity
     - split sensitivity
     - minimum note duration
   - pitch-bend selector
   - disabled/optional scale quantize section
   - disabled/optional time quantize section
4. **Main note map**
   - piano keyboard on the left
   - grid/heatmap background
   - colored MIDI note rectangles
   - horizontal/vertical scrolling
   - drag MIDI/export bar above the piano roll
5. **Gradient visual style**
   - translucent panels
   - rounded controls
   - blue/green/purple background

## Proposed notegrabber standalone layout

```text
┌────────────────────────────────────────────────────────────────────┐
│ notegrabber        [Open] [Analyze] [Export MIDI]   ◀ ▶ ⚙ 🔊       │
├───────────────┬────────────────────────────────────────────────────┤
│ Controls      │ Waveform / file strip                              │
│               ├────────────────────────────────────────────────────┤
│ Transcription │ MIDI drag/export strip + backend/status             │
│ knobs/sliders ├────────────────────────────────────────────────────┤
│               │ Piano keyboard │ heatmap + MIDI notes              │
│ Quantize      │                │                                    │
│ controls      │                │                                    │
│               ├────────────────────────────────────────────────────┤
│ Sequence      │ minimap + detected chords/table                    │
└───────────────┴────────────────────────────────────────────────────┘
```

## Feature mapping from current web viewer

| Current browser feature | Standalone widget |
|---|---|
| file upload/server | `QFileDialog`, drag/drop on main window |
| waveform canvas | custom `WaveformWidget` or pyqtgraph `PlotWidget` |
| heatmap canvas | `PianoRollView` using `QGraphicsView` or custom `QWidget.paintEvent` |
| MIDI overlay | painted rectangles; selection/delete, drag move, pitch drag, and edge resize implemented |
| hover/click note inspector | selected-note label plus start/duration/pitch/velocity editor implemented |
| threshold/min-duration controls | left-column sliders/dials |
| sequence table | `QTableWidget` onset/chord table implemented; richer model/minimap pending |
| minimap | small custom QWidget above/below table |
| original audio playback | `QMediaPlayer` + `QAudioOutput` implemented |
| rendered MIDI playback | generated WAV via TiMidity + `QMediaPlayer` implemented; committed GUI edits re-render preview |
| CSV copy | browser viewer implemented; native GUI pending |
| export MIDI | button writes current analyzed/tuned/edited/deleted notes |

## First standalone screen details

### Top bar

- **Open audio** button
- **Analyze** button
- backend selector: `Basic Pitch`, `CQT`, `Simple`
- play/pause original
- play/pause MIDI preview
- edit selected note start/duration/pitch/velocity
- delete selected note
- export tuned/edited MIDI
- settings button for external renderer/soundfont paths

### Left controls

Use real Qt widgets first, custom knob styling later:

- `Note sensitivity` → Basic Pitch `frame_threshold` or CQT `threshold`
- `Split sensitivity` → Basic Pitch `onset_threshold`
- `Min note duration` → `min_duration`
- `Pitch bend` → initially `No Pitch Bend` only; reserve UI
- `Scale quantize` → initially disabled/stub
- `Time quantize` → initially disabled/stub

### Waveform strip

- show selected audio filename, sample rate, duration
- waveform overview with playhead
- click to seek

### Piano-roll/heatmap

- y-axis: MIDI notes 21–108 with piano keyboard
- x-axis: seconds/frames
- heatmap as image raster
- MIDI notes as rectangles
- selected-note label: MIDI pitch, time, duration, velocity
- inspector fields: start, duration, pitch, velocity
- click: select/highlight note and seek playback
- Delete/Backspace or left-panel button deletes selected note
- drag note body horizontally/vertically to move time/pitch
- drag note edges to resize start/end boundaries
- later: visual handles/cursors, keyboard nudging, undo/redo

### Sequence panel

- onset-grouped chord rows from current tuned notes
- columns: time, notes, duration, velocities
- click row to seek/select
- copy visible sequence as CSV

## Visual style

For the first milestone, prioritize function over exact polish. Still approximate the screenshot with:

- dark main background
- gradient accent header/background
- translucent grouped panels
- cyan/blue heatmap palette with purple/blue note rectangles
- rounded buttons and cards using Qt stylesheets

Avoid copying NeuralNote branding or assets.
