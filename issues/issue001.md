# Issue 001 — Heatmap paint is O(visible_pixels × note_count) of interpreted Python per repaint

**Impact:** High · **Effort:** Medium · **Risk:** Low–Medium

## Problem

The heatmap draw is the hottest paint path — it runs on **every** heatmap repaint:
zoom, scroll, pan, show/hide overlay, and hover. For a long or zoomed-out file the
column path runs, and for every visible x-pixel (up to viewport width, ~1000+) it
loops over every note row (`note_count`, ~88), calling
`self.heatmap.activation(frame, note)` inside a `max(...)` generator — typically 5
samples per cell.

Each `activation()` call does two Python-level bounds checks, a `list[list[float]]`
double index, a `float()`, and a `max/min` clamp. That is roughly
`1000 × 88 × 5 ≈ 440,000` fully-interpreted Python calls **per paint**, plus a
`QRectF` + `QColor` allocation per surviving cell. Because `activations` is a
`list[list[float]]`, no vectorization is possible today. The zoomed-in frame path
has the same shape (nested `range(note_count)` × visible frames, one `activation()`
call each).

This is the dominant cost governing zoom/scroll/pan smoothness.

## Files and functions involved

- `src/notegrabber/gui/widgets/piano_roll.py`
  - `_draw_heatmap_columns` (line ~346) — per-pixel-column path (long/zoomed-out files).
  - `_draw_heatmap_frames` (line ~328) — per-frame path (zoomed-in/short files).
  - `_draw_heatmap` (line ~308) — dispatches between the two.
- `src/notegrabber/gui/state.py`
  - `GuiHeatmap.activations: list[list[float]]` (line ~49) — the data model.
  - `GuiHeatmap.activation()` (line ~70) — per-cell accessor called in the inner loops.
  - `heatmap_from_document()` (line ~109) — builds the activations from JSON.
  - `heatmap_to_document()` (line ~201) — reads `heatmap.activations` back out for
    export/retune (correctness risk for the refactor).

## Possible approach

1. In `GuiHeatmap`, add a precomputed **numpy `float32` array** `activations_np` of
   shape `(frame_count, note_count)`, built once in `heatmap_from_document` (which
   already constructs the list-of-lists — convert there). Keep the list form (or
   rebuild it from the array) so `heatmap_to_document`, JSON export, and CQT retune
   keep working unchanged.
2. Keep `activation()` as a thin wrapper over the array so existing callers/tests
   are unaffected.
3. In `_draw_heatmap_columns`, replace the per-cell generator-max with a single
   vectorized column reduction:
   `col_max = activations_np[start_frame:end_frame:stride].max(axis=0)` — one
   C-level call returns all 88 note rows for that pixel column. Then iterate the
   resulting 1-D array and only call `_heat_color`/`fillRect` for entries
   `> 0.005`.
4. In `_draw_heatmap_frames`, index `activations_np[frame_index]` (a numpy row) and
   threshold it (e.g. `np.nonzero(row > 0.005)`) to skip empty cells.
5. (Optional, larger) cache a per-pixel-column downsample per `(zoom, scroll)` state
   rather than recomputing every paint. The vectorized reduction alone removes the
   bulk of the cost.

## Notes

- Does **not** regress the huge-MP3 case (canvas width is already viewport-bounded);
  it helps most there.
- Main correctness risk is the `heatmap_to_document` round-trip — cover with the
  existing tests plus a round-trip assertion.
- Shares the "activations as numpy" refactor with [005](issue005.md) and
  [006](issue006.md).
