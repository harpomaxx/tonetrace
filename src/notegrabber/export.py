"""Export transcribed notes to MIDI or rendered audio (WAV / MP3 / FLAC / OGG).

MIDI is written directly with :func:`notegrabber.midi.write_midi`. Audio formats
are produced by synthesizing the MIDI to WAV with the configured synth (the same
native numpy synth used for playback preview) and then, for non-WAV targets,
transcoding the WAV with ``soundfile`` (libsndfile), which handles MP3/FLAC/OGG
without any external encoder.

Every function returns ``(path, None)`` on success or ``(None, message)`` on
failure so GUI/CLI callers can surface a message instead of raising.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .midi import MidiNote, write_midi
from .midi_render import render_midi_to_wav

# Target formats keyed by lowercase file extension (without the dot). "mid" is the
# raw note data; the rest are rendered audio. The value is a human label used in
# file-dialog filters and status messages.
EXPORT_FORMATS: dict[str, str] = {
    "mid": "MIDI",
    "wav": "WAV audio",
    "mp3": "MP3 audio",
    "flac": "FLAC audio",
    "ogg": "OGG audio",
}

# Audio formats (everything except raw MIDI) that go through the synth + soundfile.
AUDIO_EXPORT_EXTENSIONS = tuple(ext for ext in EXPORT_FORMATS if ext != "mid")


def format_for_path(path: Path) -> str | None:
    """Return the export format key for a path's extension, or None if unsupported."""

    ext = path.suffix.lower().lstrip(".")
    if ext in ("midi",):
        return "mid"
    return ext if ext in EXPORT_FORMATS else None


def export_notes(notes: list[MidiNote], path: Path) -> tuple[Path | None, str | None]:
    """Export ``notes`` to ``path``, choosing MIDI or audio by the file extension.

    Supported extensions: .mid/.midi, .wav, .mp3, .flac, .ogg. Returns
    ``(path, None)`` on success or ``(None, message)`` on failure.
    """

    fmt = format_for_path(path)
    if fmt is None:
        return None, f"Unsupported export format: {path.suffix or '(none)'}"

    if fmt == "mid":
        try:
            write_midi(path, notes)
        except Exception as exc:  # noqa: BLE001 - surface any writer failure as a message
            return None, f"Could not write MIDI to {path}: {exc}"
        return path, None

    return _export_audio(notes, path, fmt)


def _export_audio(notes: list[MidiNote], path: Path, fmt: str) -> tuple[Path | None, str | None]:
    """Synthesize ``notes`` to audio at ``path`` in the given format."""

    with tempfile.TemporaryDirectory(prefix="notegrabber-export-") as tmp:
        tmp_dir = Path(tmp)
        midi_path = tmp_dir / "export.mid"
        wav_path = tmp_dir / "export.wav"
        try:
            write_midi(midi_path, notes)
        except Exception as exc:  # noqa: BLE001
            return None, f"Could not build MIDI for audio export: {exc}"

        rendered, error = render_midi_to_wav(midi_path, wav_path)
        if rendered is None:
            return None, error or "MIDI could not be synthesized to audio."

        if fmt == "wav":
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(rendered.read_bytes())
            except Exception as exc:  # noqa: BLE001
                return None, f"Could not write WAV to {path}: {exc}"
            return path, None

        return _transcode_wav(rendered, path, fmt)


def _transcode_wav(wav_path: Path, path: Path, fmt: str) -> tuple[Path | None, str | None]:
    """Transcode a WAV to MP3/FLAC/OGG at ``path`` using soundfile."""

    try:
        import soundfile as sf  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return None, (
            f"Exporting {fmt.upper()} needs soundfile; install the GUI extras "
            "with `python3 -m pip install -e '.[gui]'`."
        )

    try:
        data, sample_rate = sf.read(str(wav_path))
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not read synthesized audio: {exc}"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), data, sample_rate, format=fmt.upper())
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not encode {fmt.upper()} to {path}: {exc}"
    return path, None
