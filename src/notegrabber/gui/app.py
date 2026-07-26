"""QApplication entry point for the native notegrabber GUI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from notegrabber.analyzer import BackendName


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone GUI launcher parser without importing Qt."""

    parser = argparse.ArgumentParser(prog="notegrabber-gui", description="Launch the native notegrabber desktop GUI")
    parser.add_argument("audio", nargs="?", type=Path, help="optional audio file to open on startup")
    parser.add_argument(
        "--backend",
        choices=("basic-pitch", "cqt", "simple"),
        default="basic-pitch",
        help="initial analysis backend (default: basic-pitch)",
    )
    parser.add_argument("--no-render-midi", action="store_true", help="do not render MIDI preview WAVs with TiMidity++")
    return parser


def run_gui(audio: Path | None = None, backend: BackendName = "basic-pitch", render_midi: bool = True) -> int:
    """Launch the Qt GUI, returning a process exit code."""

    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as exc:
        print(
            "notegrabber-gui requires PySide6. Install it with `python3 -m pip install -e '.[gui]'` "
            "or `python3 -m pip install -e '.[standalone]'`.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    from .main_window import MainWindow

    # This helps smoke tests and headless manual checks when users set QT_QPA_PLATFORM=offscreen.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = MainWindow(initial_backend=backend, render_midi=render_midi)
    if audio is not None:
        window.load_audio(audio)
    window.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for `notegrabber-gui`."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return run_gui(audio=args.audio, backend=args.backend, render_midi=not args.no_render_midi)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
