"""Audio-based tempo and beat estimation (issue #14).

The note-onset heuristic measures the median gap between *transcribed notes*,
which is a note rate rather than a tempo: on real material it reports 161 BPM
for a clip librosa tracks at 106, because fast passages inflate the gap
statistic. Beat tracking reads the audio's own onset envelope instead.

These tests cover the pure helpers and the confidence policy. The librosa call
itself is integration-tested (and skipped when librosa is missing), per the
issue's scope note.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from notegrabber.beat_tempo import (
    MAX_TEMPO_BPM,
    MIN_BEATS_FOR_CONFIDENCE,
    MIN_TEMPO_BPM,
    BeatEstimate,
    assess_beats,
    beat_interval_spread,
    estimate_beats_from_audio,
    fold_into_tempo_range,
)


# --- tempo folding ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (120.0, 120.0),
        (240.0, 120.0),  # double-time lock
        (60.0, 60.0),
        (55.0, 110.0),  # half-time lock
        # Doubling stops as soon as the value is in range: 30 -> 60, not 120.
        (30.0, 60.0),
        (300.0, 150.0),
    ],
)
def test_fold_brings_tempos_into_the_musical_range(raw, expected):
    assert fold_into_tempo_range(raw) == pytest.approx(expected)


def test_folded_tempos_are_always_in_range():
    for raw in (20.0, 45.0, 90.0, 190.0, 400.0, 1000.0):
        folded = fold_into_tempo_range(raw)
        assert MIN_TEMPO_BPM <= folded <= MAX_TEMPO_BPM


def test_fold_leaves_non_positive_alone():
    assert fold_into_tempo_range(0.0) == 0.0
    assert fold_into_tempo_range(-5.0) == -5.0


# --- steadiness -------------------------------------------------------------


def test_spread_is_near_zero_for_a_perfect_grid():
    beats = [i * 0.5 for i in range(10)]
    assert beat_interval_spread(beats) == pytest.approx(0.0, abs=1e-9)


def test_spread_grows_with_irregularity():
    steady = [i * 0.5 for i in range(10)]
    ragged = [0.0, 0.5, 1.4, 1.6, 2.9, 3.0, 4.4, 5.5]
    assert beat_interval_spread(ragged) > beat_interval_spread(steady)


def test_spread_needs_enough_beats():
    assert beat_interval_spread([]) is None
    assert beat_interval_spread([1.0]) is None
    assert beat_interval_spread([1.0, 2.0]) is None


# --- confidence policy ------------------------------------------------------


def test_a_steady_grid_is_confident():
    beats = [i * 0.5 for i in range(12)]
    assert assess_beats(120.0, beats) is True


def test_a_ragged_grid_is_not_confident():
    beats = [0.0, 0.5, 1.6, 1.7, 3.2, 3.3, 5.0, 5.1, 7.4, 7.5]
    assert assess_beats(120.0, beats) is False


def test_too_few_beats_is_not_confident():
    assert assess_beats(120.0, [0.0, 0.5]) is False


def test_a_short_clip_with_a_steady_grid_is_still_confident():
    """A 2.8s sample yields ~5 beats; that is short, not untracked.

    An earlier threshold of 8 beats rejected a real sample whose beats were
    near-perfectly even (spread 0.008), so the count requirement was lowered to
    the minimum that makes the spread measure meaningful.
    """

    beats = [0.03, 0.60, 1.16, 1.72, 2.28]
    assert len(beats) >= MIN_BEATS_FOR_CONFIDENCE
    assert assess_beats(106.1, beats) is True


def test_missing_or_absurd_tempo_is_not_confident():
    beats = [i * 0.5 for i in range(12)]
    assert assess_beats(None, beats) is False
    assert assess_beats(0.0, beats) is False
    assert assess_beats(-120.0, beats) is False


# --- estimate shape ---------------------------------------------------------


def test_estimate_exposes_beat_count_and_phase():
    estimate = BeatEstimate(tempo_bpm=120.0, beat_times=(0.25, 0.75, 1.25))
    assert estimate.beat_count == 3
    assert estimate.first_beat_seconds == pytest.approx(0.25)


def test_empty_estimate_has_no_phase():
    estimate = BeatEstimate(tempo_bpm=None)
    assert estimate.beat_count == 0
    assert estimate.first_beat_seconds is None


def test_a_missing_file_reports_an_error_rather_than_raising():
    """Tempo is advisory: a failure must degrade, never break the analysis."""

    estimate = estimate_beats_from_audio(Path("/nonexistent/definitely-not-here.wav"))
    assert estimate.tempo_bpm is None
    assert estimate.is_confident is False
    assert estimate.error is not None


# --- integration ------------------------------------------------------------


@pytest.mark.gui
def test_beat_tracking_a_real_sample():
    """Runs the real librosa path when a local sample and librosa are present."""

    pytest.importorskip("librosa")
    sample = Path("oxi.wav")
    if not sample.exists():
        pytest.skip("local sample oxi.wav not available")

    estimate = estimate_beats_from_audio(sample)

    assert estimate.error is None
    assert estimate.tempo_bpm is not None
    assert MIN_TEMPO_BPM <= estimate.tempo_bpm <= MAX_TEMPO_BPM
    assert estimate.beat_count > 0
    # Beats must be strictly increasing to be usable as a grid.
    assert list(estimate.beat_times) == sorted(estimate.beat_times)


@pytest.mark.gui
def test_tracked_tempo_agrees_with_its_own_beat_spacing():
    """A self-consistency check that does not depend on knowing the true tempo."""

    pytest.importorskip("librosa")
    sample = Path("oxi.wav")
    if not sample.exists():
        pytest.skip("local sample oxi.wav not available")

    estimate = estimate_beats_from_audio(sample)
    if estimate.beat_count < 3:
        pytest.skip("too few beats to cross-check")

    times = estimate.beat_times
    mean_gap = (times[-1] - times[0]) / (len(times) - 1)
    implied = fold_into_tempo_range(60.0 / mean_gap)
    assert implied == pytest.approx(estimate.tempo_bpm, rel=0.05)


# --- stats policy -----------------------------------------------------------


def _note(pitch, start, duration=0.4, velocity=90):
    from notegrabber.gui.state import GuiMidiNote

    return GuiMidiNote(
        pitch=pitch, start_seconds=start, duration_seconds=duration, velocity=velocity
    )


def _steady_notes():
    """Notes on a clean 0.5s grid, which the onset heuristic reads as 120 BPM."""

    return [_note(60 + (i % 5), i * 0.5) for i in range(12)]


def test_audio_tempo_is_preferred_over_the_note_heuristic():
    from notegrabber.gui.transcription_stats import compute_stats

    stats = compute_stats(_steady_notes(), duration_seconds=6.0, audio_tempo_bpm=93.5)
    assert stats.tempo_bpm == pytest.approx(93.5)


def test_note_heuristic_is_used_when_no_audio_tempo_is_available():
    from notegrabber.gui.transcription_stats import compute_stats

    stats = compute_stats(_steady_notes(), duration_seconds=6.0)
    assert stats.tempo_bpm == pytest.approx(120.0, rel=0.05)


def test_a_selection_ignores_the_audio_tempo():
    """The audio tempo describes the analysed audio, not a dragged window."""

    from notegrabber.gui.transcription_stats import compute_stats

    stats = compute_stats(
        _steady_notes(),
        duration_seconds=3.0,
        start_seconds=0.0,
        end_seconds=3.0,
        is_selection=True,
        audio_tempo_bpm=93.5,
    )
    assert stats.tempo_bpm != pytest.approx(93.5)


def test_analysis_result_defaults_keep_older_callers_working():
    from notegrabber.gui.analysis_worker import AnalysisResult
    from notegrabber.gui.state import GuiHeatmap

    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60],
        frame_times=[0.0],
        activations=[[0.0]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    result = AnalysisResult(
        audio_path=Path("/a.wav"),
        backend="basic-pitch",
        midi_path=Path("/a.mid"),
        heatmap_path=None,
        rendered_midi_wav=None,
        render_error=None,
        notes=[],
        heatmap=heatmap,
    )
    assert result.audio_tempo_bpm is None
    assert result.beat_times == ()


def test_analysis_result_survives_the_process_boundary():
    """Results are pickled between the child runner and the GUI."""

    import pickle

    from notegrabber.gui.analysis_worker import AnalysisResult
    from notegrabber.gui.state import GuiHeatmap

    heatmap = GuiHeatmap(
        backend="basic-pitch",
        midi_notes=[60],
        frame_times=[0.0],
        activations=[[0.0]],
        sample_rate=10,
        hop_size=1,
        window_size=1,
    )
    result = AnalysisResult(
        audio_path=Path("/a.wav"),
        backend="basic-pitch",
        midi_path=Path("/a.mid"),
        heatmap_path=None,
        rendered_midi_wav=None,
        render_error=None,
        notes=[],
        heatmap=heatmap,
        audio_tempo_bpm=138.5,
        beat_times=(0.5, 1.0, 1.5),
    )
    restored = pickle.loads(pickle.dumps(result))

    assert restored.audio_tempo_bpm == pytest.approx(138.5)
    assert restored.beat_times == (0.5, 1.0, 1.5)
