"""The theme system: registry, default fidelity, and stylesheet generation.

These tests are import-only (no Qt widgets) except where a QApplication is
needed, so they run fast. They pin two invariants: the default theme reproduces
the original chrome exactly, and every registered theme builds a stylesheet and
a heat ramp without error.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_registry_has_expected_themes():
    from notegrabber.gui import theme as t

    for theme_id in ("default", "amber", "rebirth"):
        assert theme_id in t.THEMES
    # Default is first (menu order) and is the initial active theme.
    assert next(iter(t.THEMES)) == "default"
    assert t.active_theme().id == "default"


def test_default_theme_stylesheet_is_verbatim_original():
    """build_stylesheet(default) must equal the theme's verbatim stylesheet."""

    from notegrabber.gui import theme as t

    assert t.DEFAULT_THEME.stylesheet is not None
    assert t.build_stylesheet(t.DEFAULT_THEME) == t.DEFAULT_THEME.stylesheet
    # And the back-compat constant matches too.
    assert t.APP_STYLESHEET == t.DEFAULT_THEME.stylesheet


def test_every_theme_builds_a_stylesheet():
    from notegrabber.gui import theme as t

    for theme in t.THEMES.values():
        ss = t.build_stylesheet(theme)
        assert isinstance(ss, str) and "QMainWindow" in ss and "{" not in _strip_valid_css(ss)


def _strip_valid_css(ss: str) -> str:
    # A leftover unfilled {slot} would appear as a lone brace pair with a bare
    # word; the real CSS uses braces for rules. We only guard against str.format
    # leaving a placeholder, which shows up as e.g. "{accent}". Detect those.
    import re

    return "".join(re.findall(r"\{[a-z_]+\}", ss))


def test_nondefault_stylesheets_differ_from_default_and_use_their_accent():
    from notegrabber.gui import theme as t

    default_ss = t.build_stylesheet(t.DEFAULT_THEME)
    # Amber uses its amber accent; ReBirth uses its 303-red accent.
    amber = t.build_stylesheet(t.AMBER_THEME)
    assert amber != default_ss
    assert "#f08a1e" in amber  # amber accent
    rebirth = t.build_stylesheet(t.REBIRTH_THEME)
    assert rebirth != default_ss
    assert rebirth != amber
    assert "#962222" in rebirth  # 303 maroon-red accent


def test_set_active_theme_accepts_id_and_object():
    from notegrabber.gui import theme as t

    try:
        assert t.set_active_theme("rebirth").id == "rebirth"
        assert t.active_theme().id == "rebirth"
        assert t.set_active_theme(t.DEFAULT_THEME).id == "default"
    finally:
        t.set_active_theme("default")


def test_heat_ramp_channels_reproduce_default_formula():
    """Default heat ramp must match the original 45+210v / 18+150v / 8+22(1-v) / 42+205v."""

    from notegrabber.gui import theme as t

    (r0, rs), (g0, gs), (b0, bs), (a0, as_) = t.DEFAULT_THEME.heat.channels()
    assert (r0, rs) == (45, 210)
    assert (g0, gs) == (18, 150)
    assert (b0, bs) == (30, -22)  # 8 + 22*(1-v) == 30 - 22*v
    assert (a0, as_) == (42, 205)


def test_theme_switch_recolors_piano_roll_and_persists_lut():
    """Switching themes changes the rendered canvas and rebuilds the heat LUT."""

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui import theme as t
    from notegrabber.gui.state import GuiHeatmap, GuiMidiNote
    from notegrabber.gui.widgets.piano_roll import PianoRollWidget

    QApplication.instance() or QApplication([])
    pitches = list(range(48, 72))
    frames = [i * 0.05 for i in range(40)]
    acts = [[0.9 if (i + p) % 4 == 0 else 0.05 for p in range(len(pitches))] for i in range(len(frames))]
    hm = GuiHeatmap(backend="cqt", midi_notes=pitches, frame_times=frames, activations=acts, sample_rate=100, hop_size=1, window_size=1)
    note = GuiMidiNote(pitch=60, start_seconds=0.5, duration_seconds=1.0, velocity=90)

    def avg_color() -> tuple[int, int, int]:
        roll = PianoRollWidget()
        roll.resize(400, 260)
        roll.set_data(hm, [note], full_duration_seconds=3.0)
        roll.set_playhead(1.0)
        img = QImage(roll.size(), QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        roll.render(img, QPoint(0, 0))
        rs = gs = bs = n = 0
        for x in range(60, 380, 6):
            for y in range(0, 260, 6):
                c = img.pixelColor(x, y)
                rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
        return rs // n, gs // n, bs // n

    try:
        t.set_active_theme("default")
        # Reset the shared LUT caches so this test is order-independent.
        PianoRollWidget._HEAT_LUT = None
        PianoRollWidget._HEAT_RGBA_LUT = None
        default_avg = avg_color()

        t.set_active_theme("rebirth")
        rebirth_avg = avg_color()
        # The heat LUT that was actually built for this render is tagged rebirth
        # (either the QColor path or the numpy-blit path, depending on zoom regime).
        built_tags = {PianoRollWidget._HEAT_LUT_THEME, PianoRollWidget._HEAT_RGBA_LUT_THEME}
        assert "rebirth" in built_tags
        assert default_avg != rebirth_avg
    finally:
        t.set_active_theme("default")
        PianoRollWidget._HEAT_LUT = None
        PianoRollWidget._HEAT_RGBA_LUT = None


def test_waveform_overview_color_uses_theme_keyed_lut():
    """WaveformWidget._overview_color indexes a cached LUT that matches the ramp
    exactly and rebuilds when the active theme changes (issue #20)."""

    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from notegrabber.gui import theme as t
    from notegrabber.gui.widgets.waveform import WaveformWidget

    QApplication.instance() or QApplication([])

    def exact(value: float) -> tuple[int, int, int, int]:
        value = max(0.0, min(1.0, value))
        (r0, rs), (g0, gs), (b0, bs), (a0, as_) = t.active_theme().heat.channels()
        return QColor(int(r0 + rs * value), int(g0 + gs * value), int(b0 + bs * value), int(a0 + as_ * value)).getRgb()

    try:
        t.set_active_theme("default")
        WaveformWidget._HEAT_LUT = None
        # Every quantised entry matches the exact per-channel ramp.
        for i in range(256):
            v = i / 255.0
            assert WaveformWidget._overview_color(v).getRgb() == exact(v)
        assert WaveformWidget._HEAT_LUT_THEME == "default"

        # Switching themes rebuilds the LUT and yields different colors.
        default_top = WaveformWidget._overview_color(1.0).getRgb()
        t.set_active_theme("rebirth")
        assert WaveformWidget._overview_color(1.0).getRgb() == exact(1.0)
        assert WaveformWidget._HEAT_LUT_THEME == "rebirth"
        assert WaveformWidget._overview_color(1.0).getRgb() != default_top
    finally:
        t.set_active_theme("default")
        WaveformWidget._HEAT_LUT = None
