# Issue 002 — `_note_rect` rebuilds a `{pitch: index}` dict on every call

**Impact:** High · **Effort:** Low · **Risk:** Very low

## Problem

`_note_rect` builds the entire pitch→row lookup dict **every single call**:

```python
note_to_index = {pitch: index for index, pitch in enumerate(self.heatmap.midi_notes)}
```

`_note_rect` is called:

- once **per note on every repaint** (from `_draw_notes`), so drawing N notes
  rebuilds the dict N times → O(N × note_count) dict-builds per paint; and
- once **per note on every `mouseMoveEvent`** (from `_note_hit_at`). Hover tracking
  is enabled (`setMouseTracking(True)`), so simply moving the mouse across the piano
  roll — even while idle — costs O(N × note_count) dict rebuilds per mouse event.

This directly degrades hover feedback and drag responsiveness, and adds avoidable
cost to every note-heavy repaint.

## Files and functions involved

- `src/notegrabber/gui/widgets/piano_roll.py`
  - `_note_rect()` (line ~445) — rebuilds `note_to_index` at line ~447.
  - `_draw_notes()` (line ~410) — calls `_note_rect` once per note per paint.
  - `_note_hit_at()` (line ~460) — calls `_note_rect` once per note per mouse-move.
  - `_pitch_at_y()` (line ~527) — related pitch↔row mapping that can reuse the map.
  - `set_data()` (line ~53) — already tracks `heatmap_changed`, the natural place to
    build the map once.

## Possible approach

1. Build the pitch→row map **once** in `set_data` and store it as
   `self._pitch_to_row` (a plain dict).
2. Rebuild it only when the heatmap actually changes — the `heatmap_changed` flag
   already exists in `set_data`.
3. Reuse `self._pitch_to_row` in `_note_rect`, `_note_hit_at`, and `_pitch_at_y`
   instead of building a local dict.
4. Initialize it to an empty dict in `__init__` and clear/rebuild it when the
   heatmap is set to `None`.

## Notes

- No huge-MP3 or existing-test regression.
- Pairs naturally with [003](issue003.md); both target hover/drag jank.
