"""Smoke tests for the public notegrabber command-line interface."""

from __future__ import annotations

import subprocess

import pytest

from tests.helpers import notegrabber_command


@pytest.mark.cli
@pytest.mark.tier0
def test_notegrabber_help_lists_analyze_command() -> None:
    command = [*notegrabber_command(), "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "analyze" in combined_output
    assert "visualize" in combined_output
    assert "serve" in combined_output
    assert "gui" in combined_output
    assert "separate" in combined_output


@pytest.mark.cli
@pytest.mark.tier0
def test_analyze_help_documents_input_and_output_arguments() -> None:
    command = [*notegrabber_command(), "analyze", "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "--out" in combined_output
    assert "--heatmap" in combined_output
    assert "--backend" in combined_output
    assert "cqt" in combined_output
    assert "basic-pitch" in combined_output
    assert "--onset-threshold" in combined_output
    assert "--frame-threshold" in combined_output
    assert "--min-duration" in combined_output
    assert any(term in combined_output for term in ("input", "audio", "wav"))


@pytest.mark.cli
@pytest.mark.tier0
def test_gui_help_documents_standalone_options() -> None:
    command = [*notegrabber_command(), "gui", "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "basic-pitch" in combined_output
    assert "--backend" in combined_output
    assert "--no-render-midi" in combined_output
    assert "standalone" in combined_output


@pytest.mark.cli
@pytest.mark.tier0
def test_serve_help_documents_local_upload_options() -> None:
    command = [*notegrabber_command(), "serve", "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "--out-dir" in combined_output
    assert "--host" in combined_output
    assert "--port" in combined_output
    assert "--no-render-midi" in combined_output
    assert "basic-pitch" in combined_output
    assert "upload" in combined_output


@pytest.mark.cli
@pytest.mark.tier0
def test_separate_help_documents_stem_options() -> None:
    command = [*notegrabber_command(), "separate", "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "--out-dir" in combined_output
    assert "--model" in combined_output
    assert "--stems" in combined_output
    assert "--quiet" in combined_output
    assert "htdemucs" in combined_output
    assert any(term in combined_output for term in ("stem", "separate", "vocals"))
    assert any(term in combined_output for term in ("progress", "real-time"))


@pytest.mark.cli
@pytest.mark.tier0
def test_visualize_help_documents_basic_pitch_and_out_dir() -> None:
    command = [*notegrabber_command(), "visualize", "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "--out-dir" in combined_output
    assert "--backend" in combined_output
    assert "cqt" in combined_output
    assert "basic-pitch" in combined_output
    assert "--onset-threshold" in combined_output
    assert "--frame-threshold" in combined_output
    assert "--min-duration" in combined_output
    assert "timidity" in combined_output.lower()
