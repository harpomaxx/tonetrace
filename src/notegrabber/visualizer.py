"""Generate a small self-contained browser visualization for notegrabber output."""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from .analyzer import BackendName, analyze_wav_to_midi
from .midi import MidiNote, TICKS_PER_SECOND


def create_visualization(
    input_audio: Path,
    out_dir: Path,
    backend: BackendName = "cqt",
    render_midi: bool = True,
) -> dict[str, Path | None]:
    """Analyze audio and write an HTML heatmap/MIDI preview directory."""

    out_dir.mkdir(parents=True, exist_ok=True)
    midi_path = out_dir / "analysis.mid"
    heatmap_path = out_dir / "heatmap.json"
    original_audio_path = out_dir / input_audio.name
    midi_audio_path = out_dir / "analysis.wav"
    html_path = out_dir / "index.html"

    notes = analyze_wav_to_midi(input_audio, midi_path, heatmap_path=heatmap_path, backend=backend)
    if input_audio.resolve() != original_audio_path.resolve():
        shutil.copyfile(input_audio, original_audio_path)

    rendered_midi_path: Path | None = None
    render_error: str | None = None
    if render_midi:
        rendered_midi_path, render_error = render_midi_to_wav(midi_path, midi_audio_path)

    heatmap = json.loads(heatmap_path.read_text(encoding="utf-8"))
    html_path.write_text(
        build_html(
            title=f"notegrabber: {input_audio.name}",
            heatmap=heatmap,
            notes=notes,
            original_audio=original_audio_path.name,
            midi_file=midi_path.name,
            midi_audio=rendered_midi_path.name if rendered_midi_path is not None else None,
            render_error=render_error,
        ),
        encoding="utf-8",
    )

    return {
        "html": html_path,
        "midi": midi_path,
        "heatmap": heatmap_path,
        "original_audio": original_audio_path,
        "midi_audio": rendered_midi_path,
    }


def render_midi_to_wav(midi_path: Path, wav_path: Path) -> tuple[Path | None, str | None]:
    """Render MIDI to WAV with TiMidity++ when available."""

    timidity = shutil.which("timidity")
    if timidity is None:
        return None, "TiMidity++ was not found on PATH, so MIDI audio preview was not rendered."

    command = [timidity, str(midi_path), "-Ow", "-o", str(wav_path)]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode != 0 or not wav_path.exists():
        details = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        return None, f"TiMidity++ failed to render MIDI audio: {details or 'unknown error'}"
    return wav_path, None


def build_html(
    *,
    title: str,
    heatmap: dict[str, object],
    notes: list[MidiNote],
    original_audio: str,
    midi_file: str,
    midi_audio: str | None,
    render_error: str | None,
) -> str:
    """Return a self-contained HTML viewer with embedded heatmap/note data."""

    note_payload = [asdict(note) | {"start_seconds": note.start_tick / TICKS_PER_SECOND, "duration_seconds": note.duration_ticks / TICKS_PER_SECOND} for note in notes]
    heatmap_json = json.dumps(heatmap, separators=(",", ":"))
    notes_json = json.dumps(note_payload, separators=(",", ":"))
    title_html = html.escape(title)
    original_audio_attr = html.escape(original_audio, quote=True)
    midi_file_attr = html.escape(midi_file, quote=True)
    midi_audio_html = (
        f'<audio id="midiAudio" controls preload="metadata" src="{html.escape(midi_audio, quote=True)}"></audio>'
        if midi_audio is not None
        else '<p class="warning">MIDI WAV preview unavailable.</p>'
    )
    render_error_html = f'<p class="warning">{html.escape(render_error)}</p>' if render_error else ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_html}</title>
<style>
  body {{ margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; }}
  header, main {{ max-width: 1200px; margin: 0 auto; padding: 1rem; }}
  a {{ color: #8ec8ff; }}
  .panel {{ background: #1b1b1b; border: 1px solid #333; border-radius: 0.75rem; padding: 1rem; margin: 1rem 0; }}
  .transport {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  audio {{ width: 100%; }}
  .viewer {{ position: relative; width: 100%; overflow-x: auto; border: 1px solid #444; background: #050505; }}
  canvas {{ display: block; image-rendering: pixelated; }}
  #overlay {{ position: absolute; left: 0; top: 0; pointer-events: none; }}
  .tooltip {{ display: none; position: fixed; z-index: 10; max-width: 20rem; background: rgba(20, 20, 20, 0.95); border: 1px solid #666; border-radius: 0.5rem; padding: 0.55rem 0.7rem; color: #fff; box-shadow: 0 0.4rem 1.2rem rgba(0, 0, 0, 0.45); pointer-events: none; font-size: 0.9rem; }}
  .inspector-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 0.75rem; }}
  .readout {{ background: #101010; border: 1px solid #333; border-radius: 0.5rem; padding: 0.7rem; min-height: 5rem; }}
  .readout strong {{ display: block; color: #fff; margin-bottom: 0.35rem; }}
  button {{ background: #2d6cdf; color: white; border: 0; border-radius: 0.4rem; padding: 0.6rem 0.9rem; cursor: pointer; }}
  button:hover {{ background: #3d7cff; }}
  code {{ color: #ffd479; }}
  .warning {{ color: #ffb86b; }}
  .meta {{ color: #bbb; }}
</style>
</head>
<body>
<header>
  <h1>{title_html}</h1>
  <p class="meta">Backend: <code id="backend"></code>. Heatmap: <code id="dims"></code>. MIDI notes detected: <code>{len(notes)}</code>. <a href="{midi_file_attr}">Download MIDI</a>.</p>
</header>
<main>
  <section class="panel transport">
    <div>
      <h2>Original audio</h2>
      <audio id="originalAudio" controls preload="metadata" src="{original_audio_attr}"></audio>
    </div>
    <div>
      <h2>Rendered MIDI audio</h2>
      {midi_audio_html}
      {render_error_html}
    </div>
  </section>
  <section class="panel">
    <button id="playBoth">Play original + MIDI from current time</button>
    <button id="pauseBoth">Pause both</button>
  </section>
  <section class="panel">
    <h2>CQT heatmap / sample map</h2>
    <p class="meta">Higher pitches are at the top. Brighter colors mean stronger salience. Red rectangles are MIDI notes extracted from the heatmap.</p>
    <div id="viewer" class="viewer">
      <canvas id="heatmap"></canvas>
      <canvas id="overlay"></canvas>
      <div id="tooltip" class="tooltip" role="status"></div>
    </div>
    <div class="inspector-grid">
      <div id="cursorReadout" class="readout"><strong>Cursor</strong><span>Move over the heatmap to inspect time, pitch, and activation.</span></div>
      <div id="noteInspector" class="readout"><strong>Selected note</strong><span>Click a red MIDI note rectangle to inspect it and jump playback.</span></div>
    </div>
  </section>
</main>
<script id="heatmapData" type="application/json">{heatmap_json}</script>
<script id="noteData" type="application/json">{notes_json}</script>
<script>
const heatmap = JSON.parse(document.getElementById('heatmapData').textContent);
const notes = JSON.parse(document.getElementById('noteData').textContent);
const canvas = document.getElementById('heatmap');
const overlay = document.getElementById('overlay');
const viewer = document.getElementById('viewer');
const tooltip = document.getElementById('tooltip');
const cursorReadout = document.getElementById('cursorReadout');
const noteInspector = document.getElementById('noteInspector');
const ctx = canvas.getContext('2d');
const octx = overlay.getContext('2d');
const frames = heatmap.frames;
const midiNotes = heatmap.midi_notes;
const cellW = 5;
const cellH = 5;
const width = Math.max(1, frames.length * cellW);
const height = Math.max(1, midiNotes.length * cellH);
canvas.width = overlay.width = width;
canvas.height = overlay.height = height;
canvas.style.width = overlay.style.width = width + 'px';
canvas.style.height = overlay.style.height = height + 'px';
document.getElementById('backend').textContent = heatmap.backend || 'unknown';
document.getElementById('dims').textContent = `${{frames.length}} frames × ${{midiNotes.length}} notes`;

function color(value) {{
  value = Math.max(0, Math.min(1, value));
  const r = Math.round(255 * Math.min(1, value * 2.2));
  const g = Math.round(220 * Math.max(0, value - 0.25) * 1.25);
  const b = Math.round(120 * Math.max(0, 1 - value * 1.5));
  return `rgb(${{r}},${{g}},${{b}})`;
}}

function noteY(pitch) {{
  const index = midiNotes.indexOf(pitch);
  return (midiNotes.length - 1 - index) * cellH;
}}

function noteName(pitch) {{
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  return names[pitch % 12] + (Math.floor(pitch / 12) - 1);
}}

function formatSeconds(seconds) {{
  return `${{seconds.toFixed(3)}}s`;
}}

function noteRect(note) {{
  const secondsPerFrame = heatmap.hop_size / heatmap.sample_rate;
  return {{
    x: note.start_seconds / secondsPerFrame * cellW,
    y: noteY(note.pitch),
    w: Math.max(2, note.duration_seconds / secondsPerFrame * cellW),
    h: cellH,
  }};
}}

function activationAt(frameIndex, pitch) {{
  const noteIndex = midiNotes.indexOf(pitch);
  if (frameIndex < 0 || frameIndex >= frames.length || noteIndex < 0) return 0;
  return frames[frameIndex].activations[noteIndex] || 0;
}}

function heatmapPoint(event) {{
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(width - 1, (event.clientX - rect.left) * (canvas.width / rect.width)));
  const y = Math.max(0, Math.min(height - 1, (event.clientY - rect.top) * (canvas.height / rect.height)));
  const frameIndex = Math.max(0, Math.min(frames.length - 1, Math.floor(x / cellW)));
  const pitchIndexFromTop = Math.max(0, Math.min(midiNotes.length - 1, Math.floor(y / cellH)));
  const noteIndex = midiNotes.length - 1 - pitchIndexFromTop;
  const pitch = midiNotes[noteIndex];
  const timeSeconds = frameIndex * heatmap.hop_size / heatmap.sample_rate;
  return {{ x, y, frameIndex, pitch, timeSeconds, activation: activationAt(frameIndex, pitch) }};
}}

function noteAtPoint(point) {{
  for (const note of notes) {{
    const rect = noteRect(note);
    if (point.x >= rect.x && point.x <= rect.x + rect.w && point.y >= rect.y && point.y <= rect.y + rect.h) return note;
  }}
  return null;
}}

function notePeakActivation(note) {{
  const secondsPerFrame = heatmap.hop_size / heatmap.sample_rate;
  const startFrame = Math.max(0, Math.floor(note.start_seconds / secondsPerFrame));
  const endFrame = Math.min(frames.length, Math.ceil((note.start_seconds + note.duration_seconds) / secondsPerFrame));
  let peak = 0;
  for (let frameIndex = startFrame; frameIndex < endFrame; frameIndex++) {{
    peak = Math.max(peak, activationAt(frameIndex, note.pitch));
  }}
  return peak;
}}

function noteDetailsHtml(note) {{
  return `<strong>${{noteName(note.pitch)}} / MIDI ${{note.pitch}}</strong>` +
    `<div>start: ${{formatSeconds(note.start_seconds)}}</div>` +
    `<div>duration: ${{formatSeconds(note.duration_seconds)}}</div>` +
    `<div>velocity: ${{note.velocity}}</div>` +
    `<div>peak heatmap activation: ${{notePeakActivation(note).toFixed(3)}}</div>`;
}}

function drawHeatmap() {{
  ctx.clearRect(0, 0, width, height);
  for (let x = 0; x < frames.length; x++) {{
    const activations = frames[x].activations;
    for (let i = 0; i < midiNotes.length; i++) {{
      ctx.fillStyle = color(activations[i]);
      ctx.fillRect(x * cellW, (midiNotes.length - 1 - i) * cellH, cellW, cellH);
    }}
  }}
}}

let hoveredNote = null;
let selectedNote = null;

function drawOverlay(playheadSeconds = null) {{
  octx.clearRect(0, 0, width, height);
  octx.lineWidth = 1;
  for (const note of notes) {{
    const rect = noteRect(note);
    const isSelected = note === selectedNote;
    const isHovered = note === hoveredNote;
    octx.strokeStyle = isSelected ? 'rgba(120, 210, 255, 1)' : isHovered ? 'rgba(255, 230, 120, 1)' : 'rgba(255, 70, 70, 0.95)';
    octx.fillStyle = isSelected ? 'rgba(120, 210, 255, 0.24)' : isHovered ? 'rgba(255, 230, 120, 0.24)' : 'rgba(255, 70, 70, 0.15)';
    octx.fillRect(rect.x, rect.y, rect.w, rect.h);
    octx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  }}
  if (playheadSeconds !== null) {{
    const secondsPerFrame = heatmap.hop_size / heatmap.sample_rate;
    const x = playheadSeconds / secondsPerFrame * cellW;
    octx.strokeStyle = 'rgba(120, 210, 255, 0.95)';
    octx.beginPath();
    octx.moveTo(x, 0);
    octx.lineTo(x, height);
    octx.stroke();
  }}
}}

function updateCursorReadout(point, note) {{
  cursorReadout.innerHTML = '<strong>Cursor</strong>' +
    `<div>time: ${{formatSeconds(point.timeSeconds)}} (frame ${{point.frameIndex}})</div>` +
    `<div>pitch row: ${{noteName(point.pitch)}} / MIDI ${{point.pitch}}</div>` +
    `<div>activation: ${{point.activation.toFixed(3)}}</div>` +
    (note ? `<div>hover note: ${{noteName(note.pitch)}} / MIDI ${{note.pitch}}</div>` : '');
}}

function updateNoteInspector(note) {{
  if (!note) {{
    noteInspector.innerHTML = '<strong>Selected note</strong><span>Click a red MIDI note rectangle to inspect it and jump playback.</span>';
    return;
  }}
  noteInspector.innerHTML = '<strong>Selected note</strong>' + noteDetailsHtml(note) + '<div class="meta">Playback jumped to this note start.</div>';
}}

viewer.addEventListener('mousemove', (event) => {{
  const point = heatmapPoint(event);
  hoveredNote = noteAtPoint(point);
  updateCursorReadout(point, hoveredNote);
  tooltip.style.display = 'block';
  tooltip.style.left = (event.clientX + 14) + 'px';
  tooltip.style.top = (event.clientY + 14) + 'px';
  tooltip.innerHTML = hoveredNote
    ? noteDetailsHtml(hoveredNote) + `<div>cursor activation: ${{point.activation.toFixed(3)}}</div>`
    : `<strong>${{noteName(point.pitch)}} / MIDI ${{point.pitch}}</strong><div>time: ${{formatSeconds(point.timeSeconds)}}</div><div>activation: ${{point.activation.toFixed(3)}}</div>`;
  drawOverlay(original.currentTime || null);
}});

viewer.addEventListener('mouseleave', () => {{
  hoveredNote = null;
  tooltip.style.display = 'none';
  drawOverlay(original.currentTime || null);
}});

viewer.addEventListener('click', (event) => {{
  const note = noteAtPoint(heatmapPoint(event));
  selectedNote = note;
  updateNoteInspector(selectedNote);
  if (note) {{
    original.currentTime = note.start_seconds;
    if (midiAudio) midiAudio.currentTime = note.start_seconds;
  }}
  drawOverlay(original.currentTime || null);
}});

const original = document.getElementById('originalAudio');
const midiAudio = document.getElementById('midiAudio');
document.getElementById('playBoth').addEventListener('click', async () => {{
  const t = original.currentTime || 0;
  if (midiAudio) midiAudio.currentTime = t;
  await original.play();
  if (midiAudio) await midiAudio.play();
}});
document.getElementById('pauseBoth').addEventListener('click', () => {{
  original.pause();
  if (midiAudio) midiAudio.pause();
}});
original.addEventListener('timeupdate', () => drawOverlay(original.currentTime));

drawHeatmap();
drawOverlay();
</script>
</body>
</html>
"""
