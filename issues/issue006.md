# Issue 006 — Overview build max-pools in pure-Python nested loops

**Impact:** Low (background thread) · **Effort:** Low · **Risk:** Low

## Problem

When building the low-resolution pitch overview, the code builds `normalized` as a
**numpy array**, then converts it to a `list[list[float]]` and max-pools it with a
**pure-Python double loop** (`_max_pool_rows`). This needlessly discards numpy and
runs O(frames × ~60 bands) in interpreted Python before the 1200-frame cap.

This runs on the **background** `OverviewWorker` thread, so it does **not** block the
UI or hurt playback/zoom smoothness. It only adds to the load/analyze latency for a
huge (e.g. hour-long) MP3 — which is why it is low priority.

## Files and functions involved

- `src/notegrabber/gui/overview.py`
  - `downsample_overview_frames()` (line ~91) — builds `normalized` (numpy) then
    converts to lists.
  - `_max_pool_rows()` (line ~115) — the pure-Python nested-loop max-pool.
  - `PitchOverview.activation()` (line ~33) — accessor (ties into the numpy refactor).
- Runs on: `src/notegrabber/gui/overview_worker.py` (background thread).

## Possible approach

1. Do the downsample/pool with numpy **before** converting to lists: reshape and
   `np.maximum.reduceat` (or reshape-and-`max(axis=...)`) on `normalized`, then a
   single `.tolist()` if a list form is still needed.
2. Better: keep `PitchOverview.activations` as a **numpy array** end-to-end, which
   also feeds the vectorized overview drawing in [005](issue005.md).

## Notes

- No UI-smoothness impact; affects load latency for very long files only.
- Shares the "activations as numpy" refactor with [001](issue001.md) and
  [005](issue005.md).
- Do last, as polish, alongside the other numpy work.
