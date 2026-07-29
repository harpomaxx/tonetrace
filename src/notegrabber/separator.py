"""Stem separation: split a mix into per-instrument WAV stems.

Wraps the ``demucs-onnx`` package, which runs HT-Demucs music source separation
with pure numpy + onnxruntime (no PyTorch) and auto-downloads the model on first
use. The separated stems are plain WAV files that can be fed straight into
``notegrabber analyze`` for per-stem transcription.

Kept intentionally thin and backend-swappable: ``SEPARATION_MODELS`` names the
supported models so a future backend (a smaller/faster model, or the 6-stem
guitar/piano variant) can be added without changing the CLI contract.
"""

from __future__ import annotations

import contextlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# Model name -> the stems it produces. htdemucs / htdemucs_ft give 4 stems;
# htdemucs_6s adds guitar and piano (heavier, and Meta notes piano is weak).
SEPARATION_MODELS: dict[str, tuple[str, ...]] = {
    "htdemucs": ("drums", "bass", "other", "vocals"),
    "htdemucs_ft": ("drums", "bass", "other", "vocals"),
    "htdemucs_6s": ("drums", "bass", "other", "vocals", "guitar", "piano"),
}
DEFAULT_SEPARATION_MODEL = "htdemucs"
PrecisionName = str  # "fp16" | "fp32"

# HT-Demucs processes audio in fixed windows of this many seconds; separation
# time scales with the number of windows. Used only to show a rough ETA.
SEPARATION_CHUNK_SECONDS = 7.8

# Longer inputs are separated in segments of this many seconds so peak memory
# stays bounded by the segment length, not the whole-song length (the model
# otherwise holds full-length arrays for every stem in RAM at once, which OOMs
# long files on low-memory machines). Files at or under this length run in one
# pass. A multiple of the model chunk length keeps segment boundaries aligned.
DEFAULT_SEGMENT_SECONDS = 39.0  # 5 x SEPARATION_CHUNK_SECONDS
# Rough per-chunk compute cost and a fixed model/session warmup, for the ETA.
# These are deliberately conservative CPU estimates; the display marks the value
# as approximate ("~") because the true rate varies with hardware and load. A
# GPU is far faster, so the estimate will over-predict there (harmless).
_ETA_PER_CHUNK_SECONDS = 10.0
_ETA_OVERHEAD_SECONDS = 4.0


def read_audio_duration(path: Path) -> float | None:
    """Return the audio duration in seconds, or None if it cannot be read cheaply."""

    try:
        import soundfile as sf  # type: ignore[import-not-found]

        return float(sf.info(str(path)).duration)
    except Exception:
        return None


def estimate_separation_seconds(duration_seconds: float | None) -> float | None:
    """Rough estimate of how long separation will take for the given audio length.

    Based on the chunk model (``overhead + n_chunks * per_chunk``). Returns None
    when the duration is unknown. This is an approximation, not a guarantee.
    """

    if duration_seconds is None or duration_seconds <= 0:
        return None

    n_chunks = max(1, math.ceil(duration_seconds / SEPARATION_CHUNK_SECONDS))
    return _ETA_OVERHEAD_SECONDS + n_chunks * _ETA_PER_CHUNK_SECONDS


@dataclass(frozen=True)
class SeparationResult:
    """Where the separated stems were written."""

    model: str
    output_dir: Path
    stem_paths: dict[str, Path]


def available_stems(model: str) -> tuple[str, ...]:
    """Return the stems a model produces, or raise for an unknown model."""

    try:
        return SEPARATION_MODELS[model]
    except KeyError as exc:
        known = ", ".join(sorted(SEPARATION_MODELS))
        raise ValueError(f"unknown separation model {model!r}; choose one of: {known}") from exc


def _run_demucs(input_path: Path, output_dir: Path, *, model, stems, precision, verbose) -> None:
    """Invoke demucs-onnx on a whole file, redirecting its stdout chatter to stderr."""

    import demucs_onnx  # type: ignore[import-not-found]

    # demucs-onnx prints its chunk-by-chunk progress to stdout; redirect that to
    # stderr so progress and the actual result stay on separate streams (stdout
    # remains clean for scripting the stem paths).
    with contextlib.redirect_stdout(sys.stderr):
        demucs_onnx.separate(
            str(input_path),
            str(output_dir),
            model=model,
            stems=list(stems) if stems is not None else None,
            precision=precision,
            verbose=verbose,
            progress=verbose,
            output_format="wav",
        )


def _separate_in_segments(
    input_audio: Path,
    output_dir: Path,
    stem_names: Sequence[str],
    *,
    model,
    stems,
    precision,
    verbose,
    segment_seconds: float,
) -> None:
    """Separate a long file segment by segment to keep peak memory bounded.

    Reads the input in fixed-length blocks, separates each block on its own, and
    streams the resulting stems straight to the final output WAVs. Never holds
    more than one segment of audio (input + stems) in memory, so total memory is
    independent of the song length.
    """

    import tempfile

    import soundfile as sf  # type: ignore[import-not-found]

    info = sf.info(str(input_audio))
    sample_rate = info.samplerate
    seg_frames = max(1, int(segment_seconds * sample_rate))

    # Open one streaming writer per stem for the whole output.
    writers: dict[str, object] = {}
    for name in stem_names:
        writers[name] = sf.SoundFile(
            str(output_dir / f"{name}.wav"),
            mode="w",
            samplerate=sample_rate,
            channels=2,
            subtype="PCM_16",
        )

    try:
        with sf.SoundFile(str(input_audio)) as reader:
            total = len(reader)
            done = 0
            seg_index = 0
            n_segments = max(1, math.ceil(total / seg_frames))
            while done < total:
                block = reader.read(frames=seg_frames, dtype="float32", always_2d=True)
                if block.shape[0] == 0:
                    break
                seg_index += 1
                if verbose:
                    print(f"  segment {seg_index}/{n_segments}…", file=sys.stderr, flush=True)
                with tempfile.TemporaryDirectory() as seg_dir_str:
                    seg_dir = Path(seg_dir_str)
                    seg_in = seg_dir / "segment.wav"
                    # Ensure stereo input for the model.
                    if block.shape[1] == 1:
                        block = block.repeat(2, axis=1)
                    sf.write(str(seg_in), block, sample_rate, subtype="PCM_16")
                    del block
                    seg_out = seg_dir / "out"
                    seg_out.mkdir()
                    _run_demucs(seg_in, seg_out, model=model, stems=stems, precision=precision, verbose=False)
                    for name in stem_names:
                        stem_wav = seg_out / f"{name}.wav"
                        data, _sr = sf.read(str(stem_wav), dtype="float32", always_2d=True)
                        writers[name].write(data)
                        del data
                done += seg_frames
    finally:
        for writer in writers.values():
            writer.close()


def separate_stems(
    input_audio: Path,
    output_dir: Path,
    *,
    model: str = DEFAULT_SEPARATION_MODEL,
    stems: Sequence[str] | None = None,
    precision: PrecisionName = "fp16",
    verbose: bool = False,
    segment_seconds: float | None = DEFAULT_SEGMENT_SECONDS,
) -> SeparationResult:
    """Separate ``input_audio`` into stems written under ``output_dir``.

    ``stems`` optionally restricts the output to a subset (e.g. ``["vocals"]``);
    ``None`` writes every stem the model produces. ``precision`` chooses fp16
    (smaller/faster download) or fp32 weights. ``verbose`` shows progress on
    stderr.

    ``segment_seconds`` bounds peak memory: inputs longer than this are separated
    in segments of that length and streamed to disk, so a full-length song does
    not have to fit in RAM all at once (which otherwise OOM-kills the process on
    low-memory machines). Pass ``None`` to force a single whole-file pass.
    """

    try:
        import demucs_onnx  # noqa: F401  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional environment
        raise RuntimeError(
            "Stem separation requires the optional separation dependencies; install with "
            "`python3 -m pip install -e '.[separate]'`."
        ) from exc

    produced = available_stems(model)
    if stems is not None:
        unknown = [s for s in stems if s not in produced]
        if unknown:
            raise ValueError(
                f"model {model!r} does not produce stem(s) {unknown}; it produces: {', '.join(produced)}"
            )

    if not input_audio.exists():
        raise FileNotFoundError(f"input audio not found: {input_audio}")
    output_dir.mkdir(parents=True, exist_ok=True)

    wanted_names = list(stems) if stems is not None else list(produced)
    duration = read_audio_duration(input_audio)
    use_segments = (
        segment_seconds is not None
        and duration is not None
        and duration > segment_seconds
    )

    if use_segments:
        _separate_in_segments(
            input_audio,
            output_dir,
            wanted_names,
            model=model,
            stems=stems,
            precision=precision,
            verbose=verbose,
            segment_seconds=segment_seconds,
        )
    else:
        _run_demucs(input_audio, output_dir, model=model, stems=stems, precision=precision, verbose=verbose)

    wanted = tuple(stems) if stems is not None else produced
    stem_paths = {name: output_dir / f"{name}.wav" for name in wanted}
    return SeparationResult(model=model, output_dir=output_dir, stem_paths=stem_paths)
