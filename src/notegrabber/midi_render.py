"""Render MIDI to WAV audio with TiMidity++ when it is available on PATH.

Small, dependency-free helper shared by the GUI (for its edited-MIDI preview) and
the CLI. Returns ``(wav_path, None)`` on success or ``(None, message)`` when
TiMidity++ is missing or the render fails, so callers can degrade gracefully.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


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
