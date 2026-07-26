"""Browser visualization contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("basic_pitch")
pytest.importorskip("onnxruntime")

from tests.helpers import notegrabber_command, write_single_note_wav  # noqa: E402


@pytest.mark.cli
@pytest.mark.basic_pitch
def test_visualize_creates_basic_pitch_html_heatmap_and_midi_assets_by_default(tmp_path: Path) -> None:
    import subprocess

    input_wav = write_single_note_wav(tmp_path / "a4.wav", note=69)
    out_dir = tmp_path / "viewer"
    command = [*notegrabber_command(), "visualize", str(input_wav), "--out-dir", str(out_dir), "--no-render-midi"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert (out_dir / "index.html").exists()
    assert (out_dir / "analysis.mid").exists()
    assert (out_dir / "heatmap.json").exists()
    assert (out_dir / "a4.wav").exists()
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "basic-pitch heatmap" in html
    assert "Download original MIDI" in html
    assert "id=\"tooltip\"" in html
    assert "id=\"cursorReadout\"" in html
    assert "id=\"noteInspector\"" in html
    assert "function noteAtPoint" in html
    assert "cursor activation" in html
    assert "peak heatmap activation" in html
    assert "id=\"sensitivityRange\"" in html
    assert "id=\"zoomRange\"" in html
    assert "id=\"showOverlay\"" in html
    assert "id=\"sequenceOverview\"" in html
    assert "id=\"sequenceBody\"" in html
    assert "Detected sequence" in html
    assert "Copy sequence CSV" in html
    assert "function extractNotesFromHeatmap" in html
    assert "function groupNotesByOnset" in html
    assert "function drawSequenceOverview" in html
    assert r"return lines.join('\n');" in html
    assert "Reset extracted notes" in html
    heatmap = json.loads((out_dir / "heatmap.json").read_text(encoding="utf-8"))
    assert heatmap["backend"] == "basic-pitch"
