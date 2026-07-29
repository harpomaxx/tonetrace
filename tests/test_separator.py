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

    def separate(input_path, output_dir, *, model, stems, precision, verbose, progress, output_format):
        recorder["call"] = dict(
            input=input_path, output_dir=output_dir, model=model,
            stems=stems, precision=precision, verbose=verbose, progress=progress,
            output_format=output_format,
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
    # Progress feedback is off unless requested.
    assert recorder["call"]["verbose"] is False


def test_separate_stems_verbose_is_threaded_through(monkeypatch, tmp_path) -> None:
    recorder: dict = {}
    _install_fake_demucs_onnx(monkeypatch, recorder)
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"RIFFfake")

    separate_stems(audio, tmp_path / "stems", verbose=True)

    assert recorder["call"]["verbose"] is True
    assert recorder["call"]["progress"] is True


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


def test_run_with_spinner_returns_result_non_tty() -> None:
    import io
    from notegrabber.cli import _run_with_spinner

    # A plain StringIO is not a TTY, so no animation is attempted.
    out = io.StringIO()
    result = _run_with_spinner(lambda: 42, label="working", stream=out)
    assert result == 42


def test_run_with_spinner_propagates_errors() -> None:
    import io
    from notegrabber.cli import _run_with_spinner

    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        _run_with_spinner(boom, label="working", stream=io.StringIO())


def test_estimate_separation_seconds_scales_with_duration() -> None:
    from notegrabber.separator import estimate_separation_seconds

    short = estimate_separation_seconds(5.0)     # 1 chunk
    long = estimate_separation_seconds(240.0)    # ~31 chunks
    assert short is not None and long is not None
    assert long > short
    # A ~4-minute song should estimate on the order of minutes, not seconds.
    assert long > 60


def test_estimate_separation_seconds_handles_unknown_and_zero() -> None:
    from notegrabber.separator import estimate_separation_seconds

    assert estimate_separation_seconds(None) is None
    assert estimate_separation_seconds(0.0) is None
    assert estimate_separation_seconds(-3.0) is None


def test_read_audio_duration_missing_file_returns_none(tmp_path) -> None:
    from notegrabber.separator import read_audio_duration

    assert read_audio_duration(tmp_path / "nope.wav") is None


def _install_fake_demucs_passthrough(monkeypatch) -> None:
    """Fake demucs that copies the input to each stem (so segments are verifiable)."""

    import soundfile as sf

    fake = types.ModuleType("demucs_onnx")

    def separate(input_path, output_dir, *, model, stems, precision, verbose, progress, output_format):
        data, sr = sf.read(str(input_path), dtype="float32", always_2d=True)
        names = stems if stems is not None else list(SEPARATION_MODELS[model])
        for name in names:
            sf.write(str(Path(output_dir) / f"{name}.wav"), data, sr, subtype="PCM_16")
        return {name: object() for name in names}

    fake.separate = separate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "demucs_onnx", fake)


def test_long_file_is_separated_in_segments_and_stitched(monkeypatch, tmp_path) -> None:
    import numpy as np
    import soundfile as sf

    _install_fake_demucs_passthrough(monkeypatch)

    # Build a ~5s stereo file and force 2s segments so several segments run.
    sr = 8000
    total_frames = sr * 5
    ramp = np.linspace(0.0, 1.0, total_frames, dtype="float32")
    audio = np.stack([ramp, ramp], axis=1)  # identifiable per-sample values
    src = tmp_path / "long.wav"
    sf.write(str(src), audio, sr, subtype="PCM_16")

    out = tmp_path / "stems"
    result = separate_stems(src, out, model="htdemucs", segment_seconds=2.0)

    assert set(result.stem_paths) == {"drums", "bass", "other", "vocals"}
    # The stitched output must have the same length as the input (segments joined).
    for path in result.stem_paths.values():
        stitched, _ = sf.read(str(path), dtype="float32", always_2d=True)
        assert stitched.shape[0] == total_frames, (path, stitched.shape[0], total_frames)
        # Passthrough fake copies input, so the ramp should be preserved end to end.
        assert stitched[0, 0] == pytest.approx(0.0, abs=1e-3)
        assert stitched[-1, 0] == pytest.approx(1.0, abs=1e-3)


def test_short_file_skips_segmenting(monkeypatch, tmp_path) -> None:
    import numpy as np
    import soundfile as sf

    recorder: dict = {"calls": 0}
    fake = types.ModuleType("demucs_onnx")

    def separate(input_path, output_dir, *, model, stems, precision, verbose, progress, output_format):
        recorder["calls"] += 1
        names = stems if stems is not None else list(SEPARATION_MODELS[model])
        for name in names:
            Path(output_dir, f"{name}.wav").write_bytes(b"RIFFfake")
        return {name: object() for name in names}

    fake.separate = separate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "demucs_onnx", fake)

    sr = 8000
    audio = np.zeros((sr, 2), dtype="float32")  # 1s < default segment
    src = tmp_path / "short.wav"
    sf.write(str(src), audio, sr, subtype="PCM_16")

    separate_stems(src, tmp_path / "stems", model="htdemucs", segment_seconds=39.0)
    # A short file is one whole-file pass, not multiple segment passes.
    assert recorder["calls"] == 1
