# ToneTrace keyboard & mouse cheatsheet

Every shortcut in the standalone GUI (`notegrabber-gui`).

Two things that explain most "why isn't this working?" moments:

- **Click the piano roll first.** It needs keyboard focus before any editing key reaches it.
- **Most editing keys need a selection.** With nothing selected the arrows scroll the view instead of nudging, which is deliberate.

---

## Transport

| Key | Action |
| --- | --- |
| `Space` | Pause if playing, otherwise resume the last mode from the playhead |
| `1` | Play original audio |
| `2` | Play rendered MIDI |
| `3` | Play both (original + MIDI) |
| `0` or `Esc` | Stop |

Switching between `1`/`2`/`3` while playing changes the source **without restarting** — that is the A/B check loop.

`2` and `3` fall back to the original when no MIDI preview has been rendered yet, and say so in the status line.

These keys are ignored while a spin box or dropdown has focus, so typing a velocity of `100` never starts playback.

---

## Note editing

Requires at least one selected note. Every row below is a **single undo step**, however many notes it moves.

| Key | Action |
| --- | --- |
| `←` `→` | Nudge time by 0.05 s |
| `Shift` + `←` `→` | Fine nudge — 0.01 s |
| `Ctrl` + `←` `→` | Coarse nudge — 0.25 s |
| `↑` `↓` | Transpose ±1 semitone |
| `Ctrl` + `↑` `↓` | Transpose ±1 octave |
| `+` / `-` | Velocity ±1 (`=` also works, since `+` is `Shift`+`=`) |
| `Shift` + `+` / `-` | Velocity ±10 |
| `M` | Mute / unmute the selection |
| `Delete` / `Backspace` | Delete the selection |

**Mute is non-destructive.** Muted notes stay on the roll — drawn hollow with a dashed outline — and remain selectable, draggable and editable. They are excluded from the MIDI preview and from export, and appear in parentheses in the sequence table. That makes "which notes do I keep?" an audition loop instead of a delete/undo loop.

A group nudged into a boundary clamps as a group: it keeps its spacing and its intervals rather than collapsing.

---

## Clipboard

| Key | Action |
| --- | --- |
| `Ctrl` + `C` | Copy the selection |
| `Ctrl` + `V` | Paste at the **mouse pointer** |
| `Ctrl` + `D` | Duplicate beside the source (+0.25 s) |

Paste anchors on the pointer: the x position sets the time and the row under the pointer sets the pitch, so the pattern **transposes** to where it lands while keeping its rhythm and its intervals. Copy a phrase in C, hover over G, paste — you get the same phrase in G.

With the pointer outside the roll there is no pitch to read, so paste falls back to the playhead at the original pitch.

---

## History

| Key | Action |
| --- | --- |
| `Ctrl` + `Z` | Undo |
| `Ctrl` + `Y` or `Ctrl` + `Shift` + `Z` | Redo |

A whole group drag, a multi-note paste, a batch delete and a mute all undo in one step.

---

## Mouse — selection

| Gesture | Action |
| --- | --- |
| Click a note | Select it, and audition it on its own |
| `Shift` + click a note | Add / remove that note from the selection |
| Drag across empty space | Rubber-band select everything the rectangle touches |
| `Shift` + drag empty space | Add the swept notes to the current selection |
| Click empty space | Seek the playhead |
| Double-click empty space | Create a note there (0.25 s, velocity 90) |

Empty-space gestures are decided by movement: move past a few pixels and it is a rubber band; release without moving and it is a plain click, which seeks.

Clicking a note that is **already part of a multi-selection** keeps the whole selection, so you can drag the group. Clicking an unselected note replaces the selection with just that note.

Audition can be switched off with the **"Audition on select"** checkbox in the left panel.

---

## Mouse — editing

| Gesture | Action |
| --- | --- |
| Drag a note body | Move it in time and pitch |
| Drag a note's left/right edge | Resize that boundary |
| Drag any note of a multi-selection | Move or resize **the whole group** |

---

## Zoom & navigation

| Gesture | Action |
| --- | --- |
| `Ctrl` + wheel | Zoom time, anchored on the cursor |
| `Shift` + wheel | Zoom pitch, anchored on the cursor |
| **Fit** button | Zoom to the selected notes (time *and* pitch) |
| **Reset** button | Back to the whole song |

Zoom is cursor-anchored: whatever is under the pointer stays under the pointer. **Fit** falls back to the dragged analysis range when nothing is selected, and to the whole song when there is no range either; the status line says which it used.

At the very top or left edge, zooming out cannot hold the anchor — that would need a negative scroll offset — so the view clamps and the anchored point drifts for those last clicks.

---

## Quick reference

```
TRANSPORT     Space pause/resume    1 original   2 MIDI   3 both   0/Esc stop
EDIT          ←→ time  ↑↓ pitch  +/- velocity  M mute  Del delete
              Shift = finer/larger    Ctrl = coarser/octave
CLIPBOARD     Ctrl+C copy   Ctrl+V paste at pointer   Ctrl+D duplicate
HISTORY       Ctrl+Z undo   Ctrl+Y / Ctrl+Shift+Z redo
MOUSE         click select+audition   shift+click toggle   drag empty = rubber band
              double-click empty = new note      drag note/edge = move/resize
ZOOM          Ctrl+wheel time   Shift+wheel pitch   Fit / Reset buttons
```
