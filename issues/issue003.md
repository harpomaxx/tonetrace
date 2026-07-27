# Issue 003 — Every intermediate drag move does a full `set_data` + canvas resize + full repaint

**Impact:** High · **Effort:** Low–Medium · **Risk:** Low

## Problem

During a note drag, the piano roll's `mouseMoveEvent` emits `note_edited(..., committed=False)`
on **every move**. The main window handler still runs the full display refresh on
every intermediate (uncommitted) move rather than only on release.

The chain per mouse-move is:

`_edit_note_from_piano_roll` → `_edit_note(update_preview=False)` →
`_set_display_notes(...)` → `piano_roll.set_data(...)`.

`_edit_note` first calls `update_gui_note`, which **copies the whole notes list**.
Then `set_data` calls:

- `_update_canvas_size()` → `self.resize(self.minimumSize())` (a canvas re-layout), **and**
- `self.update()` — a **full-canvas heatmap repaint** (i.e. re-incurs Issue 001), **and**
- `sequence.set_notes(notes)` — rebuilds the entire detected-sequence table.

So a single drag gesture re-lays-out the canvas, full-repaints the heatmap, and
rebuilds the note table **dozens of times per second**. This is the "editing feels
janky" path.

## Files and functions involved

- `src/notegrabber/gui/main_window.py`
  - `_edit_note_from_piano_roll()` (line ~495) — receives `committed` flag.
  - `_edit_note()` (line ~514) — runs `_set_display_notes` even when
    `update_preview` / `committed` is `False`.
  - `_set_display_notes()` (line ~453) — full `set_data` + `sequence.set_notes`.
- `src/notegrabber/gui/widgets/piano_roll.py`
  - `mouseMoveEvent` (emits `note_edited(..., committed=False)` during drag).
  - `set_data()` (line ~53) and `_update_canvas_size()` — the resize + full repaint.
  - `_note_rect()` (line ~445) — used to compute per-note rects for partial repaint.

## Possible approach

1. Add a lightweight "preview note override" path to `PianoRollWidget` for
   uncommitted drags: update only the dragged note's geometry in `self.notes` and
   call `self.update(old_rect.united(new_rect))` (a **partial** repaint using
   `_note_rect` for the two rects) instead of `set_data` + full `update()`.
2. Since the heatmap and canvas size do not change during a drag, **skip**
   `_update_canvas_size()`/`resize()` for uncommitted moves.
3. **Skip** `sequence.set_notes(...)` during uncommitted moves entirely; only rebuild
   the sequence table on the committed release.
4. On the committed release (`committed=True`), run the existing full
   `_set_display_notes` path once so the sequence table and export state are correct.

## Notes

- No huge-MP3 regression.
- Check `tests/test_gui_app.py` for any test asserting that `set_data` /
  `sequence.set_notes` is called on **intermediate** moves; the committed-release
  behavior must remain unchanged.
- Reduces repeated triggering of Issue 001; best done after or alongside it.
