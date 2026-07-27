# Issues

Performance and smoothness backlog for the ToneTrace (`notegrabber`) GUI, from a
read-only performance analysis. Ranked by impact-to-effort; do them roughly in
order. Issues 001, 005, and 006 share a common "store activations as numpy"
refactor, so sequencing them together amortizes the work.

| Issue | Title | Impact | Effort | Status |
|-------|-------|--------|--------|--------|
| [001](issue001.md) | Heatmap paint is O(pixels × notes) of interpreted Python per repaint | High | Medium | ✅ Done |
| [002](issue002.md) | `_note_rect` rebuilds a pitch→row dict on every call | High | Low | ✅ Done |
| [003](issue003.md) | Every intermediate drag move does a full `set_data` + resize + repaint | High | Low–Medium | ✅ Done |
| [004](issue004.md) | MIDI preview render runs TiMidity synchronously on the UI thread | High | Medium–High | Open |
| [005](issue005.md) | Waveform paint slices Python lists per pixel column | Medium | Low–Medium | Open |
| [006](issue006.md) | Overview build max-pools in pure-Python nested loops | Low | Low | Open |

**Done (001–003):** the vectorized numpy heatmap paint (~2.2× faster on the
full-canvas worst case, identical drawn output with a numpy-optional fallback), the
cached pitch→row map, and the lightweight uncommitted-drag path. Covered by tests in
`test_gui_app.py` / `test_gui_state.py` (62 passing).

## Verified NOT problems (do not chase)

- Playhead strip-repaint (`set_playhead` unions two ~7px rects) — correct and cheap.
- The 60fps `_sync_playback_tick` loop — light arithmetic, occasional resync.
- Grid-line loop — already clipped to the visible region.
- Heatmap column / note draw — already clip to `clipBoundingRect`.
- `_follow_playhead_in_piano_scroll` — margin band avoids per-frame scrollbar writes.
- Canvas-width bounding — full-song fit keeps width ~viewport-bounded (max ~32× at max zoom).
- Background analysis/overview/waveform workers correctly run off-thread.
