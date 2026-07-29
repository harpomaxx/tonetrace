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


def separate_stems(
    input_audio: Path,
    output_dir: Path,
    *,
    model: str = DEFAULT_SEPARATION_MODEL,
    stems: Sequence[str] | None = None,
    precision: PrecisionName = "fp16",
) -> SeparationResult:
    """Separate ``input_audio`` into stems written under ``output_dir``.

    ``stems`` optionally restricts the output to a subset (e.g. ``["vocals"]``);
    ``None`` writes every stem the model produces. ``precision`` chooses fp16
    (smaller/faster download) or fp32 weights.
    """

    try:
        import demucs_onnx  # type: ignore[import-not-found]
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

    demucs_onnx.separate(
        str(input_audio),
        str(output_dir),
        model=model,
        stems=list(stems) if stems is not None else None,
        precision=precision,
        progress=False,
        output_format="wav",
    )

    wanted = tuple(stems) if stems is not None else produced
    stem_paths = {name: output_dir / f"{name}.wav" for name in wanted}
    return SeparationResult(model=model, output_dir=output_dir, stem_paths=stem_paths)
