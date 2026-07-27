"""Generate a small self-contained browser visualization for notegrabber output."""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from .analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    BackendName,
    analyze_wav_to_midi,
)
from .midi import MidiNote, TICKS_PER_SECOND


def create_visualization(
    input_audio: Path,
    out_dir: Path,
    backend: BackendName = "basic-pitch",
    render_midi: bool = True,
    threshold: float = 0.45,
    onset_threshold: float = BASIC_PITCH_ONSET_THRESHOLD,
    frame_threshold: float = BASIC_PITCH_FRAME_THRESHOLD,
    min_duration_seconds: float = BASIC_PITCH_MIN_DURATION_SECONDS,
) -> dict[str, Path | None]:
    """Analyze audio and write an HTML heatmap/MIDI preview directory."""

    out_dir.mkdir(parents=True, exist_ok=True)
    midi_path = out_dir / "analysis.mid"
    heatmap_path = out_dir / "heatmap.json"
    original_audio_path = out_dir / input_audio.name
    midi_audio_path = out_dir / "analysis.wav"
    html_path = out_dir / "index.html"

    notes = analyze_wav_to_midi(
        input_audio,
        midi_path,
        heatmap_path=heatmap_path,
        backend=backend,
        threshold=threshold,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        min_duration_seconds=min_duration_seconds,
    )
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
    backend_label = str(heatmap.get("backend", "unknown"))
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
  .file-row {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin: 0.65rem 0; }}
  input[type="file"] {{ color: #ddd; max-width: 100%; }}
  .waveform-wrap {{ margin-top: 0.75rem; border: 1px solid #333; border-radius: 0.55rem; overflow: hidden; background: linear-gradient(110deg, #667492 0%, #73819d 45%, #7b8a96 72%, #b8d58e 100%); }}
  #waveformCanvas {{ display: block; width: 100%; height: 96px; image-rendering: auto; }}
  .viewer {{ position: relative; width: 100%; overflow-x: auto; border: 1px solid #444; background: #050505; }}
  canvas {{ display: block; image-rendering: pixelated; }}
  #overlay {{ position: absolute; left: 0; top: 0; pointer-events: none; }}
  .tooltip {{ display: none; position: fixed; z-index: 10; max-width: 20rem; background: rgba(20, 20, 20, 0.95); border: 1px solid #666; border-radius: 0.5rem; padding: 0.55rem 0.7rem; color: #fff; box-shadow: 0 0.4rem 1.2rem rgba(0, 0, 0, 0.45); pointer-events: none; font-size: 0.9rem; }}
  .inspector-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 0.75rem; }}
  .readout {{ background: #101010; border: 1px solid #333; border-radius: 0.5rem; padding: 0.7rem; min-height: 5rem; }}
  .readout strong {{ display: block; color: #fff; margin-bottom: 0.35rem; }}
  .sequence-toolbar {{ display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; justify-content: space-between; margin-bottom: 0.75rem; }}
  .sequence-summary {{ color: #ddd; }}
  .overview-wrap {{ border: 1px solid #333; border-radius: 0.5rem; background: #080808; overflow: hidden; margin-bottom: 0.85rem; }}
  #sequenceOverview {{ display: block; width: 100%; height: 160px; image-rendering: auto; cursor: pointer; }}
  .sequence-table-wrap {{ max-height: 18rem; overflow: auto; border: 1px solid #333; border-radius: 0.5rem; }}
  .sequence-table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  .sequence-table th, .sequence-table td {{ padding: 0.45rem 0.55rem; border-bottom: 1px solid #292929; text-align: left; vertical-align: top; }}
  .sequence-table th {{ position: sticky; top: 0; z-index: 1; background: #181818; color: #fff; }}
  .sequence-table tr {{ cursor: pointer; }}
  .sequence-table tr:hover, .sequence-table tr.selected {{ background: rgba(120, 210, 255, 0.12); }}
  .note-chip {{ display: inline-block; margin: 0 0.25rem 0.25rem 0; padding: 0.12rem 0.4rem; border: 1px solid #555; border-radius: 999px; background: #222; color: #fff; white-space: nowrap; }}
  .empty-state {{ color: #aaa; padding: 0.75rem; }}
  .controls {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 0.85rem; align-items: end; }}
  .control {{ display: grid; gap: 0.35rem; color: #ddd; }}
  .control input[type="range"] {{ width: 100%; }}
  .inline-control {{ display: flex; gap: 0.5rem; align-items: center; }}
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
  <p class="meta">Backend: <code id="backend"></code>. Heatmap: <code id="dims"></code>. MIDI notes shown: <code id="notesDetected">{len(notes)}</code>. <a href="{midi_file_attr}">Download original MIDI</a>.</p>
</header>
<main>
  <section class="panel transport">
    <div>
      <h2>Original audio</h2>
      <audio id="originalAudio" controls preload="metadata" src="{original_audio_attr}"></audio>
      <div class="file-row">
        <label for="audioFileInput">Select another audio file for preview:</label>
        <input id="audioFileInput" type="file" accept="audio/*,.wav,.mp3,.flac,.ogg,.aiff,.aif">
      </div>
      <p id="waveformStatus" class="meta">Waveform shows the currently loaded original-audio preview. Selecting a file changes playback/waveform only; analysis data remains the generated sample until you rerun <code>notegrabber visualize</code>.</p>
      <div class="waveform-wrap"><canvas id="waveformCanvas" aria-label="Audio waveform preview"></canvas></div>
    </div>
    <div>
      <h2>Rendered MIDI audio</h2>
      {midi_audio_html}
      {render_error_html}
    </div>
  </section>
  <section class="panel">
    <div class="controls">
      <div class="control">
        <label for="sensitivityRange">Live extraction threshold: <output id="sensitivityValue">0.50</output></label>
        <input id="sensitivityRange" type="range" min="0.05" max="0.95" step="0.01" value="0.50">
      </div>
      <div class="control">
        <label for="minDurationRange">Minimum note duration: <output id="minDurationValue">0.05s</output></label>
        <input id="minDurationRange" type="range" min="0.00" max="0.50" step="0.01" value="0.05">
      </div>
      <div class="control">
        <label for="zoomRange">Horizontal zoom: <output id="zoomValue">1.0×</output></label>
        <input id="zoomRange" type="range" min="0.5" max="8" step="0.1" value="1">
      </div>
      <div class="control">
        <label class="inline-control"><input id="showOverlay" type="checkbox" checked> Show MIDI note overlay</label>
        <div>
          <button id="fitWidth" type="button">Fit width</button>
          <button id="resetNotes" type="button">Reset extracted notes</button>
        </div>
      </div>
    </div>
    <p class="meta">The threshold/min-duration controls re-extract note rectangles from the loaded heatmap in your browser. They do not rewrite the downloaded MIDI file yet.</p>
    <button id="playBoth">Play original + MIDI from current time</button>
    <button id="pauseBoth">Pause both</button>
  </section>
  <section class="panel">
    <h2>{html.escape(backend_label)} heatmap / sample map</h2>
    <p class="meta">Higher pitches are at the top. Brighter colors mean stronger salience. Red rectangles are MIDI notes extracted by the selected backend.</p>
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
  <section class="panel" aria-labelledby="sequenceHeading">
    <div class="sequence-toolbar">
      <div>
        <h2 id="sequenceHeading">Detected sequence</h2>
        <p id="sequenceSummary" class="sequence-summary">Extracted notes grouped by onset time.</p>
      </div>
      <button id="copySequence" type="button">Copy sequence CSV</button>
    </div>
    <p class="meta">The overview shows the whole extracted MIDI phrase at once. Click a block or table row to jump playback and inspect that note/chord.</p>
    <div class="overview-wrap"><canvas id="sequenceOverview" aria-label="Full detected note sequence overview"></canvas></div>
    <div class="sequence-table-wrap">
      <table class="sequence-table">
        <thead><tr><th>Time</th><th>Notes / chord</th><th>Duration</th><th>Velocity</th><th>Peak activation</th></tr></thead>
        <tbody id="sequenceBody"></tbody>
      </table>
      <div id="sequenceEmpty" class="empty-state" hidden>No notes at this threshold. Lower the threshold or minimum duration.</div>
    </div>
  </section>
</main>
<script id="heatmapData" type="application/json">{heatmap_json}</script>
<script id="noteData" type="application/json">{notes_json}</script>
<script>
const heatmap = JSON.parse(document.getElementById('heatmapData').textContent);
const originalNotes = JSON.parse(document.getElementById('noteData').textContent);
let notes = originalNotes.slice();
const canvas = document.getElementById('heatmap');
const overlay = document.getElementById('overlay');
const viewer = document.getElementById('viewer');
const tooltip = document.getElementById('tooltip');
const cursorReadout = document.getElementById('cursorReadout');
const noteInspector = document.getElementById('noteInspector');
const sequenceOverview = document.getElementById('sequenceOverview');
const sequenceBody = document.getElementById('sequenceBody');
const sequenceSummary = document.getElementById('sequenceSummary');
const sequenceEmpty = document.getElementById('sequenceEmpty');
const notesDetected = document.getElementById('notesDetected');
const sensitivityRange = document.getElementById('sensitivityRange');
const sensitivityValue = document.getElementById('sensitivityValue');
const minDurationRange = document.getElementById('minDurationRange');
const minDurationValue = document.getElementById('minDurationValue');
const zoomRange = document.getElementById('zoomRange');
const zoomValue = document.getElementById('zoomValue');
const showOverlay = document.getElementById('showOverlay');
const audioFileInput = document.getElementById('audioFileInput');
const waveformCanvas = document.getElementById('waveformCanvas');
const waveformStatus = document.getElementById('waveformStatus');
const ctx = canvas.getContext('2d');
const octx = overlay.getContext('2d');
const waveformCtx = waveformCanvas.getContext('2d');
const overviewCtx = sequenceOverview.getContext('2d');
const frames = heatmap.frames;
const midiNotes = heatmap.midi_notes;
const baseCellW = 5;
const cellH = 5;
const maxHeatmapCanvasWidth = 30000;
const width = Math.max(1, Math.min(maxHeatmapCanvasWidth, frames.length * baseCellW));
const height = Math.max(1, midiNotes.length * cellH);
const xScale = width / Math.max(1, frames.length);
canvas.width = overlay.width = width;
canvas.height = overlay.height = height;
canvas.style.width = overlay.style.width = width + 'px';
canvas.style.height = overlay.style.height = height + 'px';
document.getElementById('backend').textContent = heatmap.backend || 'unknown';
document.getElementById('dims').textContent = `${{frames.length}} frames × ${{midiNotes.length}} notes${{frames.length * baseCellW > maxHeatmapCanvasWidth ? ' · compressed for long audio' : ''}}`;

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

let lastWaveformBuffer = null;

function resizeWaveformCanvas() {{
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = Math.max(320, waveformCanvas.parentElement.clientWidth || 640);
  const cssHeight = 96;
  waveformCanvas.width = Math.round(cssWidth * dpr);
  waveformCanvas.height = Math.round(cssHeight * dpr);
  waveformCanvas.style.height = cssHeight + 'px';
  waveformCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return {{ width: cssWidth, height: cssHeight }};
}}

function drawWaveformPlaceholder(message) {{
  const {{ width, height }} = resizeWaveformCanvas();
  waveformCtx.clearRect(0, 0, width, height);
  waveformCtx.fillStyle = 'rgba(255,255,255,0.82)';
  waveformCtx.font = '13px system-ui, sans-serif';
  waveformCtx.fillText(message, 14, height / 2 + 4);
}}

function drawWaveformFromBuffer(audioBuffer, label) {{
  lastWaveformBuffer = audioBuffer;
  const {{ width, height }} = resizeWaveformCanvas();
  const channelCount = Math.max(1, audioBuffer.numberOfChannels);
  const length = audioBuffer.length;
  const samplesPerPixel = Math.max(1, Math.floor(length / width));
  const mid = height / 2;
  const amp = height * 0.42;

  waveformCtx.clearRect(0, 0, width, height);
  waveformCtx.fillStyle = 'rgba(255,255,255,0.78)';
  for (let x = 0; x < width; x++) {{
    const start = x * samplesPerPixel;
    const end = Math.min(length, start + samplesPerPixel);
    let min = 1;
    let max = -1;
    for (let channel = 0; channel < channelCount; channel++) {{
      const data = audioBuffer.getChannelData(channel);
      for (let index = start; index < end; index++) {{
        const value = data[index] || 0;
        if (value < min) min = value;
        if (value > max) max = value;
      }}
    }}
    const y1 = mid - max * amp;
    const y2 = mid - min * amp;
    waveformCtx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }}
  waveformCtx.strokeStyle = 'rgba(255,255,255,0.35)';
  waveformCtx.beginPath();
  waveformCtx.moveTo(0, mid);
  waveformCtx.lineTo(width, mid);
  waveformCtx.stroke();
  waveformStatus.innerHTML = `Waveform preview: <strong>${{label}}</strong> · ${{audioBuffer.numberOfChannels}} channel${{audioBuffer.numberOfChannels === 1 ? '' : 's'}} · ${{Math.round(audioBuffer.sampleRate)}} Hz · ${{formatSeconds(audioBuffer.duration)}}`;
}}

async function decodeAndDrawWaveform(arrayBuffer, label) {{
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {{
    drawWaveformPlaceholder('Web Audio decoding is not available in this browser.');
    return;
  }}
  try {{
    const audioContext = new AudioContextClass();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
    drawWaveformFromBuffer(audioBuffer, label);
    await audioContext.close();
  }} catch (error) {{
    console.error(error);
    drawWaveformPlaceholder('Could not decode this audio file for waveform preview.');
  }}
}}

async function loadWaveformFromUrl(url, label) {{
  try {{
    drawWaveformPlaceholder('Loading waveform…');
    const response = await fetch(url);
    const arrayBuffer = await response.arrayBuffer();
    await decodeAndDrawWaveform(arrayBuffer, label);
  }} catch (error) {{
    console.warn('Initial waveform fetch failed; select a file to draw its waveform.', error);
    drawWaveformPlaceholder('Select an audio file to draw its waveform preview.');
  }}
}}

function noteRect(note) {{
  const secondsPerFrame = heatmap.hop_size / heatmap.sample_rate;
  return {{
    x: note.start_seconds / secondsPerFrame * xScale,
    y: noteY(note.pitch),
    w: Math.max(2, note.duration_seconds / secondsPerFrame * xScale),
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
  const frameIndex = Math.max(0, Math.min(frames.length - 1, Math.floor(x / xScale)));
  const pitchIndexFromTop = Math.max(0, Math.min(midiNotes.length - 1, Math.floor(y / cellH)));
  const noteIndex = midiNotes.length - 1 - pitchIndexFromTop;
  const pitch = midiNotes[noteIndex];
  const timeSeconds = frameIndex * heatmap.hop_size / heatmap.sample_rate;
  return {{ x, y, frameIndex, pitch, timeSeconds, activation: activationAt(frameIndex, pitch) }};
}}

function noteAtPoint(point) {{
  if (!showOverlay.checked) return null;
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
      ctx.fillRect(x * xScale, (midiNotes.length - 1 - i) * cellH, Math.max(1, Math.ceil(xScale)), cellH);
    }}
  }}
}}

let hoveredNote = null;
let selectedNote = null;
let selectedSequenceGroupIndex = null;
let overviewHitRects = [];

function drawOverlay(playheadSeconds = null) {{
  octx.clearRect(0, 0, width, height);
  octx.lineWidth = 1;
  if (showOverlay.checked) for (const note of notes) {{
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
    const x = playheadSeconds / secondsPerFrame * xScale;
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

function groupNotesByOnset(noteList, toleranceSeconds = 0.05) {{
  const sorted = noteList.slice().sort((a, b) => a.start_seconds - b.start_seconds || a.pitch - b.pitch);
  const groups = [];
  for (const note of sorted) {{
    const last = groups[groups.length - 1];
    if (last && Math.abs(note.start_seconds - last.time) <= toleranceSeconds) {{
      last.notes.push(note);
      last.time = Math.min(last.time, note.start_seconds);
      last.end = Math.max(last.end, note.start_seconds + note.duration_seconds);
    }} else {{
      groups.push({{ time: note.start_seconds, end: note.start_seconds + note.duration_seconds, notes: [note] }});
    }}
  }}
  for (const group of groups) {{
    group.notes.sort((a, b) => a.pitch - b.pitch);
    group.end = Math.max(...group.notes.map(note => note.start_seconds + note.duration_seconds));
  }}
  return groups;
}}

function groupLabel(group) {{
  return group.notes.map(note => `${{noteName(note.pitch)}} (${{note.pitch}})`).join(' + ');
}}

function groupDuration(group) {{
  return Math.max(0, group.end - group.time);
}}

function groupVelocity(group) {{
  if (!group.notes.length) return 0;
  return Math.round(group.notes.reduce((sum, note) => sum + note.velocity, 0) / group.notes.length);
}}

function groupPeak(group) {{
  return group.notes.reduce((peak, note) => Math.max(peak, notePeakActivation(note)), 0);
}}

function renderSequence() {{
  const groups = groupNotesByOnset(notes);
  sequenceBody.innerHTML = '';
  sequenceEmpty.hidden = groups.length > 0;
  sequenceSummary.textContent = groups.length
    ? `${{notes.length}} notes in ${{groups.length}} onset group${{groups.length === 1 ? '' : 's'}}.`
    : 'No notes at this threshold.';

  groups.forEach((group, index) => {{
    const row = document.createElement('tr');
    if (index === selectedSequenceGroupIndex) row.classList.add('selected');
    row.tabIndex = 0;
    row.innerHTML =
      `<td>${{formatSeconds(group.time)}}</td>` +
      `<td>${{group.notes.map(note => `<span class="note-chip">${{noteName(note.pitch)}} <span class="meta">${{note.pitch}}</span></span>`).join('')}}</td>` +
      `<td>${{formatSeconds(groupDuration(group))}}</td>` +
      `<td>${{groupVelocity(group)}}</td>` +
      `<td>${{groupPeak(group).toFixed(3)}}</td>`;
    row.addEventListener('click', () => selectSequenceGroup(index, group));
    row.addEventListener('keydown', event => {{
      if (event.key === 'Enter' || event.key === ' ') {{
        event.preventDefault();
        selectSequenceGroup(index, group);
      }}
    }});
    sequenceBody.appendChild(row);
  }});
  drawSequenceOverview(groups);
}}

function selectSequenceGroup(index, group) {{
  selectedSequenceGroupIndex = index;
  selectedNote = group.notes[0] || null;
  updateNoteInspector(selectedNote);
  if (selectedNote) {{
    original.currentTime = group.time;
    if (midiAudio) midiAudio.currentTime = group.time;
  }}
  renderSequence();
  drawOverlay(original.currentTime || null);
}}

function drawSequenceOverview(groups = groupNotesByOnset(notes)) {{
  const cssWidth = Math.max(640, sequenceOverview.parentElement.clientWidth || 640);
  const cssHeight = 160;
  const dpr = window.devicePixelRatio || 1;
  sequenceOverview.width = Math.round(cssWidth * dpr);
  sequenceOverview.height = Math.round(cssHeight * dpr);
  sequenceOverview.style.height = cssHeight + 'px';
  overviewCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  overviewCtx.clearRect(0, 0, cssWidth, cssHeight);
  overviewCtx.fillStyle = '#080808';
  overviewCtx.fillRect(0, 0, cssWidth, cssHeight);
  overviewHitRects = [];

  const durationSeconds = Math.max(0.001, frames.length * heatmap.hop_size / heatmap.sample_rate);
  const minPitch = midiNotes[0];
  const maxPitch = midiNotes[midiNotes.length - 1];
  const pad = 12;
  const plotW = cssWidth - pad * 2;
  const plotH = cssHeight - pad * 2;

  overviewCtx.strokeStyle = 'rgba(255,255,255,0.12)';
  overviewCtx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {{
    const x = pad + (plotW * i / 4);
    overviewCtx.beginPath();
    overviewCtx.moveTo(x, pad);
    overviewCtx.lineTo(x, cssHeight - pad);
    overviewCtx.stroke();
  }}
  for (let i = 0; i <= 3; i++) {{
    const y = pad + (plotH * i / 3);
    overviewCtx.beginPath();
    overviewCtx.moveTo(pad, y);
    overviewCtx.lineTo(cssWidth - pad, y);
    overviewCtx.stroke();
  }}

  notes.forEach(note => {{
    const x = pad + (note.start_seconds / durationSeconds) * plotW;
    const w = Math.max(3, (note.duration_seconds / durationSeconds) * plotW);
    const y = pad + (1 - ((note.pitch - minPitch) / (maxPitch - minPitch))) * plotH;
    const h = 6;
    const selected = note === selectedNote;
    overviewCtx.fillStyle = selected ? 'rgba(120, 210, 255, 0.9)' : 'rgba(255, 80, 80, 0.78)';
    overviewCtx.fillRect(x, y - h / 2, w, h);
    overviewHitRects.push({{ x, y: y - h / 2, w, h, note }});
  }});

  overviewCtx.fillStyle = 'rgba(230,230,230,0.8)';
  overviewCtx.font = '12px system-ui, sans-serif';
  overviewCtx.fillText('0s', pad, cssHeight - 3);
  overviewCtx.fillText(durationSeconds.toFixed(2) + 's', cssWidth - pad - 42, cssHeight - 3);
}}

function sequenceCsv() {{
  const lines = ['time_seconds,notes,duration_seconds,avg_velocity,peak_activation'];
  for (const group of groupNotesByOnset(notes)) {{
    lines.push([
      group.time.toFixed(3),
      '"' + groupLabel(group).replaceAll('"', '""') + '"',
      groupDuration(group).toFixed(3),
      groupVelocity(group),
      groupPeak(group).toFixed(3),
    ].join(','));
  }}
  return lines.join('\\n');
}}

function setZoom(value) {{
  const zoom = Number(value);
  canvas.style.width = overlay.style.width = (width * zoom) + 'px';
  canvas.style.height = overlay.style.height = height + 'px';
  zoomRange.value = String(zoom);
  zoomValue.textContent = `${{zoom.toFixed(1)}}×`;
}}

function updateNoteCount() {{
  notesDetected.textContent = String(notes.length);
}}

function localPeak(activations, index) {{
  const value = activations[index] || 0;
  const left = index > 0 ? activations[index - 1] || 0 : -1;
  const right = index + 1 < activations.length ? activations[index + 1] || 0 : -1;
  return value >= left && value >= right;
}}

function extractNotesFromHeatmap(threshold, minDurationSeconds) {{
  const secondsPerFrame = heatmap.hop_size / heatmap.sample_rate;
  const minFrames = Math.max(1, Math.ceil(minDurationSeconds / secondsPerFrame));
  const extracted = [];
  for (let noteIndex = 0; noteIndex < midiNotes.length; noteIndex++) {{
    let activeStart = null;
    let peak = 0;
    for (let frameIndex = 0; frameIndex < frames.length; frameIndex++) {{
      const activation = frames[frameIndex].activations[noteIndex] || 0;
      const active = activation >= threshold && localPeak(frames[frameIndex].activations, noteIndex);
      if (active) {{
        if (activeStart === null) {{
          activeStart = frameIndex;
          peak = activation;
        }} else {{
          peak = Math.max(peak, activation);
        }}
      }} else if (activeStart !== null) {{
        appendExtractedNote(extracted, noteIndex, activeStart, frameIndex, minFrames, secondsPerFrame, peak);
        activeStart = null;
        peak = 0;
      }}
    }}
    if (activeStart !== null) {{
      appendExtractedNote(extracted, noteIndex, activeStart, frames.length, minFrames, secondsPerFrame, peak);
    }}
  }}
  extracted.sort((a, b) => a.start_seconds - b.start_seconds || a.pitch - b.pitch);
  return extracted;
}}

function appendExtractedNote(extracted, noteIndex, startFrame, endFrame, minFrames, secondsPerFrame, peak) {{
  if (endFrame - startFrame < minFrames) return;
  const startSeconds = startFrame * secondsPerFrame;
  const durationSeconds = Math.max(secondsPerFrame, (endFrame - startFrame) * secondsPerFrame);
  extracted.push({{
    pitch: midiNotes[noteIndex],
    start_tick: Math.round(startSeconds * 960),
    duration_ticks: Math.max(1, Math.round(durationSeconds * 960)),
    velocity: Math.max(1, Math.min(127, Math.round(peak * 127))),
    start_seconds: startSeconds,
    duration_seconds: durationSeconds,
  }});
}}

function applyLiveExtraction() {{
  const threshold = Number(sensitivityRange.value);
  const minDuration = Number(minDurationRange.value);
  sensitivityValue.textContent = threshold.toFixed(2);
  minDurationValue.textContent = `${{minDuration.toFixed(2)}}s`;
  notes = extractNotesFromHeatmap(threshold, minDuration);
  selectedNote = null;
  hoveredNote = null;
  updateNoteInspector(null);
  updateNoteCount();
  renderSequence();
  drawOverlay(original.currentTime || null);
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
let selectedAudioObjectUrl = null;
audioFileInput.addEventListener('change', async () => {{
  const file = audioFileInput.files && audioFileInput.files[0];
  if (!file) return;
  if (selectedAudioObjectUrl) URL.revokeObjectURL(selectedAudioObjectUrl);
  selectedAudioObjectUrl = URL.createObjectURL(file);
  original.src = selectedAudioObjectUrl;
  original.load();
  drawWaveformPlaceholder('Decoding selected file…');
  await decodeAndDrawWaveform(await file.arrayBuffer(), file.name);
}});
sensitivityRange.addEventListener('input', applyLiveExtraction);
minDurationRange.addEventListener('input', applyLiveExtraction);
zoomRange.addEventListener('input', () => setZoom(zoomRange.value));
showOverlay.addEventListener('change', () => drawOverlay(original.currentTime || null));
sequenceOverview.addEventListener('click', event => {{
  const rect = sequenceOverview.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const hit = overviewHitRects.find(rect => x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h);
  if (hit) {{
    selectedNote = hit.note;
    selectedSequenceGroupIndex = null;
    updateNoteInspector(selectedNote);
    original.currentTime = selectedNote.start_seconds;
    if (midiAudio) midiAudio.currentTime = selectedNote.start_seconds;
    renderSequence();
    drawOverlay(original.currentTime || null);
  }}
}});
document.getElementById('copySequence').addEventListener('click', async () => {{
  const csv = sequenceCsv();
  try {{
    await navigator.clipboard.writeText(csv);
    sequenceSummary.textContent = 'Copied current sequence CSV to clipboard.';
  }} catch (_error) {{
    sequenceSummary.textContent = 'Clipboard unavailable. Select and copy from developer tools if needed.';
  }}
}});
window.addEventListener('resize', () => {{
  renderSequence();
  if (lastWaveformBuffer) drawWaveformFromBuffer(lastWaveformBuffer, 'current audio');
  else drawWaveformPlaceholder('Select an audio file to draw its waveform preview.');
}});
document.getElementById('fitWidth').addEventListener('click', () => {{
  const fittedZoom = Math.max(0.5, Math.min(8, viewer.clientWidth / width));
  setZoom(Math.round(fittedZoom * 10) / 10);
}});
document.getElementById('resetNotes').addEventListener('click', () => {{
  notes = originalNotes.slice();
  selectedNote = null;
  hoveredNote = null;
  selectedSequenceGroupIndex = null;
  updateNoteInspector(null);
  updateNoteCount();
  renderSequence();
  drawOverlay(original.currentTime || null);
}});
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

setZoom(1);
updateNoteCount();
renderSequence();
drawHeatmap();
drawOverlay();
drawWaveformPlaceholder('Loading waveform…');
loadWaveformFromUrl(original.getAttribute('src'), original.getAttribute('src'));
</script>
</body>
</html>
"""
