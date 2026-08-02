"""Visual theme system for the native Qt GUI.

A :class:`Theme` is a named palette. Two surfaces read from it:

* The Qt **stylesheet** (window chrome: backgrounds, buttons, panels, inputs) is
  produced by :func:`build_stylesheet`, which interpolates the theme's colors
  into the CSS. The ``"default"`` theme reproduces the original look exactly.
* The custom-**painted** widgets (piano roll, waveform, knob) read their colors
  from :func:`active_theme` rather than from hardcoded literals, so a theme
  change reskins the canvases too.

Themes live in the :data:`THEMES` registry keyed by id; add an entry there to
ship a new theme. The active theme is process-global (set with
:func:`set_active_theme`) so the painted widgets can reach it without threading
a reference through every constructor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QAbstractButton

_ICON_DIR = Path(__file__).with_name("resources") / "icons"

# An RGBA color as a plain tuple, so palettes stay declarative and importable
# without constructing QColors at module load. Alpha defaults to opaque.
RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


def _hex(rgb: RGB) -> str:
    """Format an (r, g, b) tuple as a CSS hex string like ``#a1b2c3``."""

    return "#{:02x}{:02x}{:02x}".format(*rgb)


def qcolor(color: RGB | RGBA) -> QColor:
    """Build a QColor from a 3- or 4-tuple (alpha optional)."""

    if len(color) == 4:
        return QColor(color[0], color[1], color[2], color[3])
    return QColor(color[0], color[1], color[2])


@dataclass(frozen=True)
class HeatRamp:
    """The heatmap activation color ramp, as low (silent) and high (loud) RGBA.

    The piano-roll heatmap maps an activation in [0, 1] linearly between ``lo``
    and ``hi`` per channel, matching the original ``45 + 210*v`` style formula.
    Storing the two endpoints keeps the ramp themeable while the widget derives
    the per-channel offset/slope from them.
    """

    lo: RGBA
    hi: RGBA

    def channels(self) -> tuple[tuple[int, int], ...]:
        """Return ((offset, slope), ...) for R, G, B, A so value v -> offset+slope*v."""

        return tuple((self.lo[i], self.hi[i] - self.lo[i]) for i in range(4))


@dataclass(frozen=True)
class Theme:
    """A named palette driving both the Qt stylesheet and the painted canvases."""

    id: str
    name: str

    # --- Stylesheet (window chrome) roles ---
    bg: RGB            # window background
    bg_deep: RGB       # status bar / deepest surfaces
    panel_hi: RGB      # panel gradient top
    panel_lo: RGB      # panel gradient bottom
    text: RGB          # primary text
    text_dim: RGB      # secondary/label text
    border: RGB        # default border
    accent: RGB        # primary accent (buttons, highlights, slider)
    accent_hi: RGB     # brighter accent (hover, focus rings)
    accent_deep: RGB   # deep accent (primary button gradient bottom)
    danger: RGB        # destructive action accent
    input_bg: RGB      # combo/spin background
    lcd: RGB           # small numeric readouts (knob value, stats)

    # --- Painted-canvas roles ---
    canvas_bg: RGBA        # piano-roll background
    keyboard_bg: RGBA      # keyboard strip background
    key_white: RGBA        # white key fill
    key_black: RGBA        # black key fill
    key_active: RGBA       # sounding key highlight
    grid: RGBA             # piano-roll grid lines
    note_fill: RGBA        # note rectangle fill
    note_border: RGBA      # note rectangle border
    note_selected: RGBA    # selected note border
    playhead: RGBA         # playhead line
    bend_curve: RGBA       # pitch-bend polyline
    heat: HeatRamp         # heatmap activation ramp

    waveform: RGBA         # waveform trace
    waveform_bg: RGBA      # waveform background
    selection: RGBA        # waveform range selection fill

    # --- Knob roles (fall back to accent-based defaults) ---
    knob_arc_lo: RGBA
    knob_arc_hi: RGBA
    knob_rim: RGBA

    # Optional verbatim stylesheet. The default theme sets this to the original
    # hand-tuned CSS so its chrome is preserved exactly; new themes leave it None
    # and get build_stylesheet's role-interpolated template instead.
    stylesheet: str | None = None

    # Text drawn over colored fills (primary/danger buttons, the accent slider).
    # Needs contrast against the accent, not against the panel -- important for a
    # light chrome where the plain text color is dark. Defaults to a near-white.
    text_on_accent: RGB | None = None

    # Background for small LCD-style readouts (transport status, stats strip,
    # knob value). On a light chrome these should stay dark like a real LED
    # display so the amber/green lcd text reads. Defaults to bg_deep.
    readout_bg: RGB | None = None


# The original, hand-tuned default chrome stylesheet, kept verbatim so the
# default theme's window looks exactly as before. New themes are generated from
# color roles instead (build_stylesheet + _STYLESHEET_TEMPLATE).
_DEFAULT_STYLESHEET = """
QMainWindow, QWidget {
    background: #07090d;
    color: #e6edf7;
    font-family: Inter, Noto Sans, DejaVu Sans, sans-serif;
    font-size: 13px;
}
QStatusBar {
    background: #05070a;
    color: #8f9bb0;
    border-top: 1px solid #1a2230;
}
QToolTip {
    color: #f7e7c5;
    background: #120b07;
    border: 1px solid #9b5423;
    border-radius: 7px;
    padding: 8px;
}
QLabel#brandLabel {
    color: #eaf2ff;
    font-size: 23px;
    font-weight: 900;
    letter-spacing: 1.4px;
    padding: 13px 12px;
    border-radius: 14px;
    border: 1px solid #29364a;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #151b25,
        stop:0.48 #0e131c,
        stop:1 #111924);
}
QLabel#brandLabel::first-letter {
    color: #ffb23f;
}
QLabel#fileLabel {
    color: #b8c4d6;
    background: #0d121a;
    border: 1px solid #202b3c;
    border-radius: 9px;
    padding: 7px 10px;
}
QLabel#transportStatus {
    color: #ffe6b0;
    font-size: 13px;
    font-weight: 800;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2a1a10,
        stop:1 #1c130c);
    border: 1px solid #7a4a1e;
    border-left: 4px solid #ffb33f;
    border-radius: 7px;
    padding: 5px 12px;
}
QLabel#selectedNoteLabel {
    color: #cdd8e8;
    background: #0a0e14;
    border: 1px solid #1b2534;
    border-radius: 8px;
    padding: 6px 9px;
    font-weight: 700;
}
QLabel#inlineFieldLabel {
    color: #7f8da3;
    font-size: 11px;
    font-weight: 800;
    text-transform: uppercase;
}
QLabel#sectionTitle {
    color: #dbe6f6;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.6px;
    padding: 2px 0;
    text-transform: uppercase;
}
QGroupBox {
    border: 1px solid #263244;
    border-radius: 12px;
    margin-top: 1.2em;
    padding: 12px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #121925,
        stop:1 #0b1018);
}
QGroupBox[panel="accent"] {
    border: 1px solid #8f4b24;
    border-top: 2px solid #ffb33f;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #132231,
        stop:0.5 #101923,
        stop:1 #0b1119);
}
QGroupBox[panel="accent"] QLabel, QGroupBox[panel="accent"] QCheckBox {
    color: #dceaff;
    font-weight: 650;
}
QLabel#knobValueLabel {
    color: #ffd66b;
    font-weight: 800;
}
QLabel#statsStrip {
    color: #ffd66b;
    font-weight: 700;
    letter-spacing: 0.3px;
    background: #12161f;
    border: 1px solid #273040;
    border-radius: 7px;
    padding: 6px 12px;
}
QGroupBox[panel="muted"] {
    border-color: #273040;
    background: #0d1118;
}
QWidget#noteInspector {
    border: 1px solid #243247;
    border-radius: 10px;
    background: #0b1018;
}
QToolButton#collapsibleHeader {
    text-align: left;
    padding: 6px 10px;
    border: 1px solid #263244;
    border-radius: 8px;
    background: #0d1118;
    color: #dbe6f6;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
QToolButton#collapsibleHeader:hover {
    border-color: #8a5a38;
}
QToolButton#collapsibleHeader:checked {
    border-color: #ffb33f;
    color: #ffc44d;
}
QLabel#emptyState {
    color: #8f9bb0;
    font-size: 13px;
    padding: 24px 16px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 7px;
    color: #9aa8bd;
    font-weight: 800;
    letter-spacing: 0.7px;
    text-transform: uppercase;
}
QGroupBox[panel="accent"]::title {
    color: #ffc44d;
}
QPushButton, QToolButton {
    min-height: 34px;
    border: 1px solid #2b384b;
    border-radius: 9px;
    padding: 7px 13px;
    color: #edf4ff;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1d2634,
        stop:1 #111722);
    font-weight: 800;
}
QPushButton:hover:!disabled, QToolButton:hover:!disabled {
    border-color: #8a5a38;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #273449,
        stop:1 #151e2d);
}
QPushButton:pressed:!disabled, QToolButton:pressed:!disabled {
    background: #0d121b;
    padding-top: 8px;
    padding-bottom: 6px;
}
QPushButton:disabled, QToolButton:disabled {
    background: #141923;
    color: #626e80;
    border-color: #222a36;
}
QPushButton[role="primary"], QToolButton[role="primary"] {
    border: 1px solid #ffb13d;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #b85820,
        stop:0.55 #823819,
        stop:1 #4a2113);
    color: #f6fdff;
}
QPushButton[role="primary"]:hover:!disabled, QToolButton[role="primary"]:hover:!disabled {
    border-color: #ffd46a;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #d86d22,
        stop:0.55 #a94a1c,
        stop:1 #652b14);
}
QPushButton[role="danger"], QToolButton[role="danger"] {
    border-color: #713344;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3a1d27,
        stop:1 #211018);
    color: #ffd9e0;
}
QPushButton[role="danger"]:hover:!disabled, QToolButton[role="danger"]:hover:!disabled {
    border-color: #b44a61;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #512435,
        stop:1 #301520);
}
QPushButton[role="transport"] {
    border-radius: 17px;
    min-width: 74px;
    padding-left: 12px;
    padding-right: 12px;
}
/* Round, icon-only media-player transport buttons. */
QPushButton[transportIcon="secondary"] {
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
    padding: 0;
    border-radius: 20px;
    border: 1px solid #2b384b;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1d2634,
        stop:1 #10151f);
}
QPushButton[transportIcon="secondary"]:hover:!disabled {
    border-color: #8a5a38;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #273449,
        stop:1 #151e2d);
}
QPushButton[transportIcon="primary"] {
    min-width: 52px;
    max-width: 52px;
    min-height: 52px;
    max-height: 52px;
    padding: 0;
    border-radius: 26px;
    border: 2px solid #ffb13d;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #d86d22,
        stop:0.55 #a94a1c,
        stop:1 #652b14);
}
QPushButton[transportIcon="primary"]:hover:!disabled {
    border-color: #ffd46a;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #f07d28,
        stop:0.55 #c25620,
        stop:1 #7a3417);
}
QPushButton[transportIcon="primary"]:pressed:!disabled,
QPushButton[transportIcon="secondary"]:pressed:!disabled {
    background: #0d121b;
}
QPushButton[transportIcon="primary"]:disabled,
QPushButton[transportIcon="secondary"]:disabled {
    background: #141923;
    border-color: #222a36;
}
QToolButton[compact="true"] {
    min-width: 64px;
    max-width: 76px;
    min-height: 56px;
    max-height: 62px;
    padding: 5px 6px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 900;
}
QToolButton[compact="true"][role="primary"] {
    border-color: #ffb13d;
}
QToolButton[compact="true"][role="danger"] {
    border-color: #874052;
}
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #0b111a;
    border: 1px solid #303d51;
    border-radius: 8px;
    padding: 6px 8px;
    color: #e7effb;
    selection-background-color: #a64a1c;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: #8a5a38;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #ffb33f;
}
QTableWidget, QScrollArea {
    background: #090e15;
    border: 1px solid #202a3a;
    border-radius: 10px;
    alternate-background-color: #0d131c;
    gridline-color: #202838;
}
QHeaderView::section {
    background: #111a27;
    color: #bfcce0;
    border: none;
    border-right: 1px solid #283348;
    padding: 7px;
    font-weight: 800;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #232b39;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #d44822,
        stop:1 #ffd64f);
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #e8f7ff;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    border: 2px solid #ffb13d;
}
QCheckBox {
    spacing: 8px;
    color: #d3deed;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #4a5a72;
    background: #0b111a;
}
QCheckBox::indicator:checked {
    background: #ffae2f;
    border-color: #ffd66b;
}
QSplitter::handle {
    background: #0a0f16;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #090e15;
    border: none;
    margin: 2px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #293449;
    border-radius: 5px;
    min-height: 28px;
    min-width: 28px;
}
QScrollBar::handle:hover {
    background: #3b4d6a;
}
"""


# The original app look, reproduced exactly so switching to "default" is a no-op
# visually. The chrome uses the verbatim _DEFAULT_STYLESHEET; canvas colors
# mirror the previous hardcoded QColors in the painted widgets.
DEFAULT_THEME = Theme(
    id="default",
    name="Midnight (default)",
    bg=(7, 9, 13),
    bg_deep=(5, 7, 10),
    panel_hi=(18, 25, 37),
    panel_lo=(11, 16, 24),
    text=(230, 237, 247),
    text_dim=(143, 155, 176),
    border=(38, 50, 68),
    accent=(255, 179, 63),
    accent_hi=(255, 214, 107),
    accent_deep=(74, 33, 19),
    danger=(180, 74, 97),
    input_bg=(11, 17, 26),
    lcd=(255, 214, 107),
    canvas_bg=(9, 10, 18, 255),
    keyboard_bg=(20, 24, 34, 255),
    key_white=(52, 57, 70, 255),
    key_black=(32, 36, 48, 255),
    key_active=(255, 196, 84, 255),
    grid=(255, 255, 255, 36),
    note_fill=(210, 72, 34, 135),
    note_border=(255, 170, 66, 255),
    note_selected=(255, 226, 88, 255),
    playhead=(255, 230, 120, 255),
    bend_curve=(120, 220, 255, 230),
    heat=HeatRamp(lo=(45, 18, 30, 42), hi=(255, 168, 8, 247)),
    waveform=(255, 170, 72, 255),
    waveform_bg=(16, 19, 28, 255),
    selection=(255, 126, 24, 55),
    knob_arc_lo=(216, 109, 34, 255),
    knob_arc_hi=(255, 214, 79, 255),
    knob_rim=(255, 179, 63, 255),
    stylesheet=_DEFAULT_STYLESHEET,
)


# ReBirth RB-338 (Propellerhead, 1996): brushed dark-graphite metal panels, the
# TB-303's amber/orange LEDs and dial, red step accents, and pale-green LCD
# readouts. Warmer, more "hardware" than the default's cool midnight blue.
REBIRTH_THEME = Theme(
    id="rebirth",
    name="ReBirth RB-338",
    # The real RB-338 is a cold brushed-aluminium rack unit: silver metal panels
    # with dark engraved text, the TB-303's muted maroon-red bodies, black LCD
    # areas, amber step LEDs, and small green status lamps. So the chrome is a
    # *light* silver skin (dark text on grey metal) with a red accent, while the
    # piano-roll/waveform canvases stay black like the unit's displays.
    bg=(168, 170, 172),        # brushed aluminium panel
    bg_deep=(120, 122, 124),   # status bar / recessed metal
    panel_hi=(214, 216, 218),  # raised metal (gradient top)
    panel_lo=(150, 152, 155),  # metal shadow (gradient bottom)
    text=(28, 28, 30),         # dark engraved lettering
    text_dim=(78, 78, 82),     # secondary engraving
    border=(96, 98, 100),      # panel seams / screw lines
    accent=(150, 34, 34),      # TB-303 maroon red
    accent_hi=(196, 60, 52),   # brighter red (hover/focus)
    accent_deep=(84, 18, 18),  # deep red (pressed / primary base)
    danger=(120, 24, 24),      # darker red for destructive actions
    input_bg=(226, 227, 228),  # inset light readout field
    lcd=(224, 128, 26),        # amber LED value readouts
    # Canvases are the unit's black displays with amber LEDs.
    canvas_bg=(14, 13, 12, 255),
    keyboard_bg=(30, 28, 26, 255),
    key_white=(64, 60, 56, 255),
    key_black=(34, 32, 30, 255),
    key_active=(232, 138, 26, 255),   # amber step LED
    grid=(255, 200, 120, 30),
    note_fill=(150, 34, 34, 170),     # 303-red notes
    note_border=(232, 138, 26, 255),  # amber outline
    note_selected=(255, 196, 70, 255),
    playhead=(232, 138, 26, 255),     # amber
    bend_curve=(120, 220, 255, 230),
    heat=HeatRamp(lo=(30, 16, 12, 42), hi=(255, 150, 26, 247)),  # black -> amber
    waveform=(232, 138, 26, 255),     # amber trace
    waveform_bg=(16, 15, 14, 255),
    selection=(232, 138, 26, 60),
    knob_arc_lo=(150, 34, 34, 255),   # red -> amber sweep
    knob_arc_hi=(232, 138, 26, 255),
    knob_rim=(60, 58, 56, 255),       # dark metal rim
    text_on_accent=(246, 236, 224),   # light text on the maroon buttons
    readout_bg=(16, 14, 12),          # black LED-display panel for lcd readouts
)


# Amber Rack: a warm brushed-graphite hardware skin (dark panels with a
# brown/tan tint, amber-orange accent, green LCD readouts). Not the literal
# RB-338 -- a warm dark synth-rack look in its own right.
AMBER_THEME = Theme(
    id="amber",
    name="Amber Rack",
    bg=(30, 27, 23),
    bg_deep=(19, 16, 13),
    panel_hi=(74, 66, 55),
    panel_lo=(44, 38, 31),
    text=(238, 228, 210),
    text_dim=(166, 150, 128),
    border=(96, 84, 66),
    accent=(240, 138, 30),
    accent_hi=(255, 182, 82),
    accent_deep=(122, 46, 12),
    danger=(206, 62, 46),
    input_bg=(26, 22, 18),
    lcd=(128, 232, 132),
    canvas_bg=(22, 19, 15, 255),
    keyboard_bg=(48, 42, 34, 255),
    key_white=(92, 82, 68, 255),
    key_black=(50, 44, 36, 255),
    key_active=(240, 138, 30, 255),
    grid=(255, 226, 170, 34),
    note_fill=(228, 92, 20, 155),
    note_border=(255, 182, 82, 255),
    note_selected=(255, 224, 120, 255),
    playhead=(128, 232, 132, 255),
    bend_curve=(128, 232, 132, 230),
    heat=HeatRamp(lo=(38, 28, 20, 42), hi=(255, 146, 22, 247)),
    waveform=(240, 138, 30, 255),
    waveform_bg=(26, 22, 17, 255),
    selection=(240, 138, 30, 64),
    knob_arc_lo=(196, 68, 14, 255),
    knob_arc_hi=(255, 182, 82, 255),
    knob_rim=(240, 138, 30, 255),
)


# Registry of shippable themes, keyed by id. Insertion order is the menu order;
# the default comes first.
THEMES: dict[str, Theme] = {t.id: t for t in (DEFAULT_THEME, AMBER_THEME, REBIRTH_THEME)}


# Process-global active theme so the painted widgets (which are not rebuilt on a
# theme change) can read the current palette. main_window sets this before it
# applies the stylesheet and triggers repaints.
_active_theme: Theme = DEFAULT_THEME


def active_theme() -> Theme:
    """Return the currently active theme."""

    return _active_theme


def set_active_theme(theme: Theme | str) -> Theme:
    """Set and return the active theme; accepts a Theme or a registered id."""

    global _active_theme
    resolved = THEMES[theme] if isinstance(theme, str) else theme
    _active_theme = resolved
    return resolved


def icon(name: str) -> QIcon:
    """Return a small SVG icon from the packaged GUI resources."""

    return QIcon(str(_ICON_DIR / f"{name}.svg"))


def polish_button(button: QAbstractButton, *, role: str = "secondary", icon_name: str | None = None) -> None:
    """Apply consistent button metadata, cursor, size, and optional icon."""

    button.setProperty("role", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(36)
    if icon_name:
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(18, 18))


def build_stylesheet(theme: Theme) -> str:
    """Build the Qt stylesheet for ``theme`` by interpolating its color roles.

    A theme may carry a verbatim ``stylesheet`` (the default does, to preserve
    its original hand-tuned look exactly); that is returned as-is. Otherwise the
    theme's color roles are interpolated into the shared template.
    """

    if theme.stylesheet is not None:
        return theme.stylesheet

    c = {
        "bg": _hex(theme.bg),
        "bg_deep": _hex(theme.bg_deep),
        "panel_hi": _hex(theme.panel_hi),
        "panel_lo": _hex(theme.panel_lo),
        "text": _hex(theme.text),
        "text_dim": _hex(theme.text_dim),
        "border": _hex(theme.border),
        "accent": _hex(theme.accent),
        "accent_hi": _hex(theme.accent_hi),
        "accent_deep": _hex(theme.accent_deep),
        "danger": _hex(theme.danger),
        "input_bg": _hex(theme.input_bg),
        "lcd": _hex(theme.lcd),
        # Contrast-preserving fallbacks: light text over colored fills, and a
        # dark background for LED-style readouts.
        "text_on_accent": _hex(theme.text_on_accent or (246, 244, 240)),
        "readout_bg": _hex(theme.readout_bg or theme.bg_deep),
    }
    return _STYLESHEET_TEMPLATE.format(**c)


# The stylesheet template. Metrics are fixed; every color is a {role} slot filled
# by build_stylesheet. Literal CSS braces are doubled for str.format.
_STYLESHEET_TEMPLATE = """
QMainWindow, QWidget {{
    background: {bg};
    color: {text};
    font-family: Inter, Noto Sans, DejaVu Sans, sans-serif;
    font-size: 13px;
}}
QStatusBar {{
    background: {bg_deep};
    color: {text_dim};
    border-top: 1px solid {border};
}}
QToolTip {{
    color: {text};
    background: {bg_deep};
    border: 1px solid {accent_deep};
    border-radius: 7px;
    padding: 8px;
}}
QLabel#brandLabel {{
    color: {text};
    font-size: 23px;
    font-weight: 900;
    letter-spacing: 1.4px;
    padding: 13px 12px;
    border-radius: 14px;
    border: 1px solid {border};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {panel_hi},
        stop:0.48 {panel_lo},
        stop:1 {panel_hi});
}}
QLabel#brandLabel::first-letter {{
    color: {accent};
}}
QLabel#fileLabel {{
    color: {text_dim};
    background: {panel_lo};
    border: 1px solid {border};
    border-radius: 9px;
    padding: 7px 10px;
}}
QLabel#transportStatus {{
    color: {lcd};
    font-size: 13px;
    font-weight: 800;
    background: {readout_bg};
    border: 1px solid {accent_deep};
    border-left: 4px solid {accent};
    border-radius: 7px;
    padding: 5px 12px;
}}
QLabel#selectedNoteLabel {{
    color: {text};
    background: {panel_lo};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 9px;
    font-weight: 700;
}}
QLabel#inlineFieldLabel {{
    color: {text_dim};
    font-size: 11px;
    font-weight: 800;
    text-transform: uppercase;
}}
QLabel#sectionTitle {{
    color: {text};
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.6px;
    padding: 2px 0;
    text-transform: uppercase;
}}
QGroupBox {{
    border: 1px solid {border};
    border-radius: 12px;
    margin-top: 1.2em;
    padding: 12px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
}}
QGroupBox[panel="accent"] {{
    border: 1px solid {accent_deep};
    border-top: 2px solid {accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:0.5 {panel_lo},
        stop:1 {panel_lo});
}}
QGroupBox[panel="accent"] QLabel, QGroupBox[panel="accent"] QCheckBox {{
    color: {text};
    font-weight: 650;
}}
QLabel#knobValueLabel {{
    color: {lcd};
    font-weight: 800;
}}
QLabel#statsStrip {{
    color: {lcd};
    font-weight: 700;
    letter-spacing: 0.3px;
    background: {readout_bg};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 6px 12px;
}}
QGroupBox[panel="muted"] {{
    border-color: {border};
    background: {panel_lo};
}}
QWidget#noteInspector {{
    border: 1px solid {border};
    border-radius: 10px;
    background: {panel_lo};
}}
QToolButton#collapsibleHeader {{
    text-align: left;
    padding: 6px 10px;
    border: 1px solid {border};
    border-radius: 8px;
    background: {panel_lo};
    color: {text};
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}}
QToolButton#collapsibleHeader:hover {{
    border-color: {accent};
}}
QToolButton#collapsibleHeader:checked {{
    border-color: {accent};
    color: {accent};
}}
QLabel#emptyState {{
    color: {text_dim};
    font-size: 13px;
    padding: 24px 16px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 7px;
    color: {text_dim};
    font-weight: 800;
    letter-spacing: 0.7px;
    text-transform: uppercase;
}}
QGroupBox[panel="accent"]::title {{
    color: {accent};
}}
QPushButton, QToolButton {{
    min-height: 34px;
    border: 1px solid {border};
    border-radius: 9px;
    padding: 7px 13px;
    color: {text};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
    font-weight: 800;
}}
QPushButton:hover:!disabled, QToolButton:hover:!disabled {{
    border-color: {accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
}}
QPushButton:pressed:!disabled, QToolButton:pressed:!disabled {{
    background: {bg_deep};
    padding-top: 8px;
    padding-bottom: 6px;
}}
QPushButton:disabled, QToolButton:disabled {{
    background: {panel_lo};
    color: {text_dim};
    border-color: {border};
}}
QPushButton[role="primary"], QToolButton[role="primary"] {{
    border: 1px solid {accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent},
        stop:0.55 {accent_deep},
        stop:1 {accent_deep});
    color: {text_on_accent};
}}
QPushButton[role="primary"]:hover:!disabled, QToolButton[role="primary"]:hover:!disabled {{
    border-color: {accent_hi};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent_hi},
        stop:0.55 {accent},
        stop:1 {accent_deep});
}}
QPushButton[role="danger"], QToolButton[role="danger"] {{
    border-color: {danger};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
    color: {text};
}}
QPushButton[role="danger"]:hover:!disabled, QToolButton[role="danger"]:hover:!disabled {{
    border-color: {danger};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
}}
QPushButton[role="transport"] {{
    border-radius: 17px;
    min-width: 74px;
    padding-left: 12px;
    padding-right: 12px;
}}
/* Round, icon-only media-player transport buttons. */
QPushButton[transportIcon="secondary"] {{
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
    padding: 0;
    border-radius: 20px;
    border: 1px solid {border};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
}}
QPushButton[transportIcon="secondary"]:hover:!disabled {{
    border-color: {accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {panel_hi},
        stop:1 {panel_lo});
}}
QPushButton[transportIcon="primary"] {{
    min-width: 52px;
    max-width: 52px;
    min-height: 52px;
    max-height: 52px;
    padding: 0;
    border-radius: 26px;
    border: 2px solid {accent};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent},
        stop:0.55 {accent_deep},
        stop:1 {accent_deep});
}}
QPushButton[transportIcon="primary"]:hover:!disabled {{
    border-color: {accent_hi};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent_hi},
        stop:0.55 {accent},
        stop:1 {accent_deep});
}}
QPushButton[transportIcon="primary"]:pressed:!disabled,
QPushButton[transportIcon="secondary"]:pressed:!disabled {{
    background: {bg_deep};
}}
QPushButton[transportIcon="primary"]:disabled,
QPushButton[transportIcon="secondary"]:disabled {{
    background: {panel_lo};
    border-color: {border};
}}
QToolButton[compact="true"] {{
    min-width: 64px;
    max-width: 76px;
    min-height: 56px;
    max-height: 62px;
    padding: 5px 6px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 900;
}}
QToolButton[compact="true"][role="primary"] {{
    border-color: {accent};
}}
QToolButton[compact="true"][role="danger"] {{
    border-color: {danger};
}}
QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {input_bg};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 8px;
    color: {text};
    selection-background-color: {accent_deep};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {accent};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {accent};
}}
QTableWidget, QScrollArea {{
    background: {bg};
    border: 1px solid {border};
    border-radius: 10px;
    alternate-background-color: {panel_lo};
    gridline-color: {border};
}}
QHeaderView::section {{
    background: {panel_hi};
    color: {text_dim};
    border: none;
    border-right: 1px solid {border};
    padding: 7px;
    font-weight: 800;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {panel_hi};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent_deep},
        stop:1 {accent_hi});
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {text};
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    border: 2px solid {accent};
}}
QCheckBox {{
    spacing: 8px;
    color: {text};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {border};
    background: {input_bg};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent_hi};
}}
QSplitter::handle {{
    background: {bg_deep};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {bg};
    border: none;
    margin: 2px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {panel_hi};
    border-radius: 5px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{
    background: {accent_deep};
}}
"""


# Backwards-compatible constant: the default theme's stylesheet. main_window now
# calls build_stylesheet with the active theme, but this keeps the old import
# working.
APP_STYLESHEET = build_stylesheet(DEFAULT_THEME)
