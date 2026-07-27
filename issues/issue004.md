# Issue 004 — MIDI preview render runs TiMidity synchronously on the UI thread every committed edit

**Impact:** High · **Effort:** Medium–High · **Risk:** Medium

## Problem

Every committed edit — drag release, inspector **Apply**, **Delete**, and CQT
**retune** — calls `_refresh_midi_preview`, which writes a MIDI file and then calls
`render_midi_to_wav`, which runs **TiMidity via a synchronous `subprocess.run`** on
the GUI thread:

```python
result = subprocess.run(command, text=True, stdout=..., stderr=..., timeout=60)
```

This blocks the Qt event loop until TiMidity finishes rendering the whole preview
WAV — the UI **freezes** (no repaint, no playhead movement, unresponsive controls)
for the full render on every edit. During rapid editing this stutter is constant.

Secondary problem: `_refresh_midi_preview` allocates a fresh `tempfile.mkdtemp`
directory **per call** and never cleans it up, leaking temp dirs across an editing
session.

## Files and functions involved

- `src/notegrabber/gui/main_window.py`
  - `_refresh_midi_preview()` (line ~545) — synchronous render on the UI thread;
    per-call `mkdtemp`; position-preservation logic near the end that must move to a
    completion handler.
  - Called from the committed-edit paths: inspector Apply, delete, retune, and
    drag-release (`_edit_note` with `update_preview=True`).
- `src/notegrabber/visualizer.py`
  - `render_midi_to_wav()` (line ~76) — the blocking `subprocess.run` at line ~84.
- Existing worker pattern to copy:
  `src/notegrabber/gui/analysis_worker.py`, `overview_worker.py`,
  `waveform_worker.py` (QThread + signals).

## Possible approach

1. Move the render into a **`QThread` worker** following the existing
   `AnalysisWorker`/`WaveformWorker`/`OverviewWorker` pattern (moveToThread,
   `finished`/`failed` signals).
2. **Debounce**: on each edit, (re)start a short single-shot `QTimer`
   (~200–300 ms). Only kick the render worker when the timer fires, so a burst of
   rapid edits collapses into a single render.
3. **Supersede**: cancel or ignore any in-flight/older render when a newer edit
   arrives, so only the latest note set is rendered and swapped in.
4. When the worker signals done, swap the `QMediaPlayer` source and re-apply the
   playback-position preservation currently done inline in `_refresh_midi_preview`
   (move that logic into the completion handler).
5. **Reuse one temp dir** (or delete the previous render's dir) instead of leaking a
   new `mkdtemp` per edit.
6. Keep a synchronous fallback path if any test depends on immediate render
   behavior.

## Notes

- No huge-MP3 regression (this is the edit path, not load/analyze).
- Likely touches `tests/test_gui_app.py` (preview-refresh assertions); the
  position-preservation move is the main correctness risk.
- Most involved of the high-impact issues — do after 001/002/003.
