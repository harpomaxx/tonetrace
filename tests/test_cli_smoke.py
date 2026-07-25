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


@pytest.mark.cli
@pytest.mark.tier0
def test_analyze_help_documents_input_and_output_arguments() -> None:
    command = [*notegrabber_command(), "analyze", "--help"]

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert "--out" in combined_output
    assert any(term in combined_output for term in ("input", "audio", "wav"))
