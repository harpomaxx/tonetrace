"""Render MIDI to WAV audio for the GUI's playback preview.

Two backends, selected by the ``NOTEGRABBER_MIDI_SYNTH`` environment variable:

- ``native`` (default): a pure-Python numpy synth (see :mod:`notegrabber.native_synth`).
  No external tools, no soundfont -- works identically on Windows, Linux, and macOS.
  Sounds like a synth, which is fine for a transcription preview.
- ``timidity``: render with TiMidity++ if it is on ``PATH`` (nicer, sampled sound).
  Falls back to the native synth when TiMidity++ is not installed.

Both return ``(wav_path, None)`` on success or ``(None, message)`` on failure, so
callers can degrade gracefully without changing their code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .native_synth import render_midi_to_wav_native

# Environment variable that chooses the synth backend; unset means the default.
SYNTH_ENV_VAR = "NOTEGRABBER_MIDI_SYNTH"
DEFAULT_SYNTH = "native"


def _selected_backend() -> str:
    """Return the configured synth backend name, defaulting to native."""

    return (os.environ.get(SYNTH_ENV_VAR) or DEFAULT_SYNTH).strip().lower()


def _render_with_timidity(midi_path: Path, wav_path: Path) -> tuple[Path | None, str | None]:
    """Render MIDI to WAV with TiMidity++ when it is available on PATH."""

    timidity = shutil.which("timidity")
    if timidity is None:
        return None, "TiMidity++ was not found on PATH."

    command = [timidity, str(midi_path), "-Ow", "-o", str(wav_path)]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode != 0 or not wav_path.exists():
        details = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        return None, f"TiMidity++ failed to render MIDI audio: {details or 'unknown error'}"
    return wav_path, None


def render_midi_to_wav(midi_path: Path, wav_path: Path) -> tuple[Path | None, str | None]:
    """Render ``midi_path`` to ``wav_path`` using the configured synth backend.

    Defaults to the pure-Python native synth. Set ``NOTEGRABBER_MIDI_SYNTH=timidity``
    to prefer TiMidity++ (falls back to native if TiMidity++ is not installed).
    """

    if _selected_backend() == "timidity":
        wav, error = _render_with_timidity(midi_path, wav_path)
        if wav is not None:
            return wav, None
        # TiMidity was requested but unavailable/failed: fall back to native so
        # the preview still works, rather than leaving the user with no audio.
        return render_midi_to_wav_native(midi_path, wav_path)

    return render_midi_to_wav_native(midi_path, wav_path)
