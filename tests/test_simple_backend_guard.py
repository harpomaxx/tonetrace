"""The quadratic 'simple' backend is guarded on long audio (issue #30).

The 'simple' backend is a pure-Python Goertzel correlation meant only for tiny
deterministic fixtures; on real audio it takes minutes and grows with the square
of duration. The CLI refuses it on input longer than a few seconds unless
--force is passed, and points at the cqt / basic-pitch backends instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from notegrabber.analyzer import wav_duration_seconds
from notegrabber.cli import SIMPLE_BACKEND_MAX_SECONDS, main
from tests.helpers import write_silence_wav, write_single_note_wav


def test_wav_duration_reads_header_without_decoding(tmp_path: Path) -> None:
    wav = write_silence_wav(tmp_path / "six.wav", duration_seconds=6.0)
    assert wav_duration_seconds(wav) == pytest.approx(6.0, abs=0.05)


def test_wav_duration_returns_zero_for_non_wav(tmp_path: Path) -> None:
    junk = tmp_path / "not.wav"
    junk.write_bytes(b"this is not a wav file")
    assert wav_duration_seconds(junk) == 0.0


def test_simple_backend_refused_on_long_audio(tmp_path: Path, capsys) -> None:
    wav = write_silence_wav(tmp_path / "long.wav", duration_seconds=SIMPLE_BACKEND_MAX_SECONDS + 2.0)
    out_mid = tmp_path / "long.mid"

    code = main(["analyze", str(wav), "--out", str(out_mid), "--backend", "simple"])

    assert code == 2
    assert not out_mid.exists()  # refused before running anything
    stderr = capsys.readouterr().err
    assert "simple" in stderr
    assert "--force" in stderr
    assert "basic-pitch" in stderr or "cqt" in stderr


def test_force_runs_simple_backend_on_long_audio(tmp_path: Path, monkeypatch) -> None:
    """--force bypasses the guard and reaches the 'simple' backend. The backend
    is stubbed so the test doesn't pay the quadratic cost it exists to warn about."""

    import notegrabber.cli as cli_module

    wav = write_silence_wav(tmp_path / "long.wav", duration_seconds=SIMPLE_BACKEND_MAX_SECONDS + 3.0)
    out_mid = tmp_path / "long.mid"

    reached: dict[str, str] = {}

    def fake_analyze(input_wav, output_midi, *, backend, **_kwargs):
        reached["backend"] = backend
        output_midi.write_bytes(b"")
        return []

    monkeypatch.setattr(cli_module, "analyze_wav_to_midi", fake_analyze)

    code = main(["analyze", str(wav), "--out", str(out_mid), "--backend", "simple", "--force"])

    assert code == 0
    assert reached["backend"] == "simple"  # guard bypassed, backend reached
    assert out_mid.exists()


def test_short_audio_runs_simple_backend_without_force(tmp_path: Path) -> None:
    wav = write_single_note_wav(tmp_path / "short.wav", note=69, duration_seconds=0.8)
    out_mid = tmp_path / "short.mid"

    code = main(["analyze", str(wav), "--out", str(out_mid), "--backend", "simple"])

    assert code == 0
    assert out_mid.exists()


def test_other_backends_not_gated_by_duration(tmp_path: Path, monkeypatch) -> None:
    """The guard is 'simple'-only: a long input with --backend cqt is not refused
    by the guard (it proceeds to the backend, which we stub to avoid heavy work)."""

    import notegrabber.cli as cli_module

    wav = write_silence_wav(tmp_path / "long.wav", duration_seconds=SIMPLE_BACKEND_MAX_SECONDS + 3.0)
    out_mid = tmp_path / "long.mid"

    called: dict[str, str] = {}

    def fake_analyze(input_wav, output_midi, *, backend, **_kwargs):
        called["backend"] = backend
        output_midi.write_bytes(b"")
        return []

    monkeypatch.setattr(cli_module, "analyze_wav_to_midi", fake_analyze)

    code = main(["analyze", str(wav), "--out", str(out_mid), "--backend", "cqt"])

    assert code == 0
    assert called["backend"] == "cqt"  # reached the backend, not refused
