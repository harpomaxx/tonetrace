"""Visual theme helpers for the native Qt GUI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractButton

_ICON_DIR = Path(__file__).with_name("resources") / "icons"


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


APP_STYLESHEET = """
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
QLabel#fileLabel, QLabel#transportStatus {
    color: #b8c4d6;
    background: #0d121a;
    border: 1px solid #202b3c;
    border-radius: 9px;
    padding: 7px 10px;
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
QGroupBox[panel="muted"] {
    border-color: #273040;
    background: #0d1118;
}
QWidget#noteInspector {
    border: 1px solid #243247;
    border-radius: 10px;
    background: #0b1018;
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
