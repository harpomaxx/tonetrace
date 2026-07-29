"""Tests for stem separation, with demucs_onnx mocked so CI stays light."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from notegrabber import separator
from notegrabber.separator import (
    DEFAULT_SEPARATION_MODEL,
    SEPARATION_MODELS,
    available_stems,
    separate_stems,
)


def test_available_stems_for_known_models() -> None:
    assert available_stems("htdemucs") == ("drums", "bass", "other", "vocals")
    assert "guitar" in available_stems("htdemucs_6s")
    assert "piano" in available_stems("htdemucs_6s")


def test_available_stems_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unknown separation model"):
        available_stems("not-a-model")


def test_default_model_is_a_known_4_stem_model() -> None:
    assert DEFAULT_SEPARATION_MODEL in SEPARATION_MODELS
    assert available_stems(DEFAULT_SEPARATION_MODEL) == ("drums", "bass", "other", "vocals")


def _install_fake_demucs_onnx(monkeypatch, recorder: dict) -> None:
    """Install a fake demucs_onnx module that records the call and writes stems."""

    fake = types.ModuleType("demucs_onnx")

    def separate(input_path, output_dir, *, model, stems, precision, progress, output_format):
        recorder["call"] = dict(
            input=input_path, output_dir=output_dir, model=model,
            stems=stems, precision=precision, output_format=output_format,
        )
        names = stems if stems is not None else list(SEPARATION_MODELS[model])
        for name in names:
            Path(output_dir, f"{name}.wav").write_bytes(b"RIFFfake")
        return {name: object() for name in names}

    fake.separate = separate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "demucs_onnx", fake)


def test_separate_stems_writes_all_stems(monkeypatch, tmp_path) -> None:
    recorder: dict = {}
    _install_fake_demucs_onnx(monkeypatch, recorder)
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFFfake")
    out = tmp_path / "stems"

    result = separate_stems(audio, out, model="htdemucs")

    assert set(result.stem_paths) == {"drums", "bass", "other", "vocals"}
    for path in result.stem_paths.values():
        assert path.exists()
    assert recorder["call"]["model"] == "htdemucs"
    assert recorder["call"]["precision"] == "fp16"


def test_separate_stems_subset(monkeypatch, tmp_path) -> None:
    recorder: dict = {}
    _install_fake_demucs_onnx(monkeypatch, recorder)
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFFfake")

    result = separate_stems(audio, tmp_path / "stems", stems=["vocals"])

    assert list(result.stem_paths) == ["vocals"]
    assert recorder["call"]["stems"] == ["vocals"]


def test_separate_stems_rejects_stem_not_in_model(monkeypatch, tmp_path) -> None:
    _install_fake_demucs_onnx(monkeypatch, {})
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFFfake")
    # htdemucs (4-stem) does not produce guitar.
    with pytest.raises(ValueError, match="does not produce stem"):
        separate_stems(audio, tmp_path / "stems", model="htdemucs", stems=["guitar"])


def test_separate_stems_missing_input(monkeypatch, tmp_path) -> None:
    _install_fake_demucs_onnx(monkeypatch, {})
    with pytest.raises(FileNotFoundError):
        separate_stems(tmp_path / "nope.wav", tmp_path / "stems")


def test_separate_stems_missing_dependency(monkeypatch, tmp_path) -> None:
    # Simulate demucs_onnx not being installed.
    monkeypatch.setitem(sys.modules, "demucs_onnx", None)
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFFfake")
    with pytest.raises(RuntimeError, match=r"\.\[separate\]"):
        separate_stems(audio, tmp_path / "stems")
