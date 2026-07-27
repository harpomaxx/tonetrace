# Issue 005 — Waveform paint slices Python lists and calls `min`/`max` per pixel column

**Impact:** Medium · **Effort:** Low–Medium · **Risk:** Low

## Problem

The waveform `paintEvent` loops over visible x-pixels and, for each column, does a
Python list slice plus two Python reductions:

```python
chunk = self.samples[start:end]   # new Python list per column
low = min(chunk)
high = max(chunk)
```

The preview is bounded to `MAX_WAVEFORM_PREVIEW_SAMPLES = 48_000`, so `step` is
small and each slice is cheap — but it is still ~`width` list allocations + 2
interpreted reductions per paint. The full canvas repaints on every selection drag
(`set_selection` → full `update()`) and on preview set, so this adds up during
range selection.

The pitch-overview strip drawn under the waveform has the **same** nested
Python-loop + per-cell accessor shape as Issue 001 (frames × ~60 bands, calling
`PitchOverview.activation()`), repainting on every waveform paint. It is capped at
1200 frames so it is lower priority, but shares the same fix.

## Files and functions involved

- `src/notegrabber/gui/widgets/waveform.py`
  - `paintEvent()` (line ~257) — the per-column slice + `min`/`max` loop (~lines 280–290).
  - `_draw_pitch_overview()` (line ~306) — nested frames × bands loop (~lines 317–329).
  - `downsample_waveform_preview()` (line ~375) — where the preview is built.
- `src/notegrabber/gui/overview.py`
  - `PitchOverview.activation()` (line ~33) — per-cell accessor used in the overview loop.

## Possible approach

1. Store the waveform preview as a **numpy array**. Precompute per-pixel `(min, max)`
   envelope arrays once when the width or preview changes (e.g. reshape +
   `np.min`/`np.max` along an axis, or `np.minimum.reduceat`/`np.maximum.reduceat`).
   Then the paint loop is a bare `drawLine` per column with no slicing or allocation.
2. For `_draw_pitch_overview`, store `PitchOverview.activations` as a numpy array and
   vectorize the per-column reduction, mirroring the Issue 001 approach (shared
   refactor).

## Notes

- No huge-MP3 regression (waveform data is already downsampled and bounded).
- Shares the "activations as numpy" refactor with [001](issue001.md) and
  [006](issue006.md); do together to amortize effort.
- Polish-tier — land after the high-impact issues.
