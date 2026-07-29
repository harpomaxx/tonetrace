"""Command-line entry point for notegrabber."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyzer import (
    BASIC_PITCH_FRAME_THRESHOLD,
    BASIC_PITCH_MIN_DURATION_SECONDS,
    BASIC_PITCH_ONSET_THRESHOLD,
    CQT_THRESHOLD,
    analyze_wav_to_midi,
)
from .separator import DEFAULT_SEPARATION_MODEL, SEPARATION_MODELS, separate_stems
from .server import serve_upload_app
from .visualizer import create_visualization


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""

    parser = argparse.ArgumentParser(prog="notegrabber", description="Baseline WAV-to-MIDI transcription CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="analyze an input WAV audio file and write a MIDI file",
        description="Analyze an input audio WAV file and write detected notes to a MIDI file.",
    )
    analyze_parser.add_argument("input_audio", type=Path, help="input audio WAV file")
    analyze_parser.add_argument("--out", required=True, type=Path, help="output MIDI file path")
    analyze_parser.add_argument("--heatmap", type=Path, help="optional output heatmap JSON file path")
    analyze_parser.add_argument(
        "--backend",
        choices=("simple", "cqt", "basic-pitch"),
        default="simple",
        help="analysis backend to use: simple deterministic DSP, CQT/librosa, or Spotify Basic Pitch ML (default: simple)",
    )
    add_analysis_tuning_arguments(analyze_parser)
    analyze_parser.set_defaults(handler=_handle_analyze)

    visualize_parser = subparsers.add_parser(
        "visualize",
        help="create a browser heatmap viewer and optional rendered MIDI audio",
        description="Analyze an input audio WAV file with the Basic Pitch backend by default and write a local HTML viewer.",
    )
    visualize_parser.add_argument("input_audio", type=Path, help="input audio WAV file")
    visualize_parser.add_argument("--out-dir", required=True, type=Path, help="output directory for index.html and generated assets")
    visualize_parser.add_argument(
        "--backend",
        choices=("simple", "cqt", "basic-pitch"),
        default="basic-pitch",
        help="analysis backend to visualize: simple deterministic DSP, CQT/librosa, or Spotify Basic Pitch ML (default: basic-pitch)",
    )
    add_analysis_tuning_arguments(visualize_parser)
    visualize_parser.add_argument("--no-render-midi", action="store_true", help="do not invoke TiMidity++ to render MIDI to WAV")
    visualize_parser.set_defaults(handler=_handle_visualize)

    serve_parser = subparsers.add_parser(
        "serve",
        help="run a local upload web app that generates fresh viewers",
        description="Run a local-only HTTP server where selecting an audio file runs notegrabber analysis and opens a fresh viewer.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="host/interface to bind (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8765, help="port to bind (default: 8765)")
    serve_parser.add_argument("--out-dir", type=Path, default=Path("out/server"), help="directory for uploaded files and generated viewers")
    serve_parser.add_argument(
        "--backend",
        choices=("simple", "cqt", "basic-pitch"),
        default="basic-pitch",
        help="default backend selected in the upload form (default: basic-pitch)",
    )
    serve_parser.add_argument("--no-render-midi", action="store_true", help="do not render MIDI previews with TiMidity++ for uploaded analyses")
    serve_parser.set_defaults(handler=_handle_serve)

    gui_parser = subparsers.add_parser(
        "gui",
        help="launch the native Qt standalone GUI",
        description="Launch the native PySide6 standalone app. Install GUI dependencies with `python3 -m pip install -e '.[gui]'`.",
    )
    gui_parser.add_argument("audio", nargs="?", type=Path, help="optional audio file to open on startup")
    gui_parser.add_argument(
        "--backend",
        choices=("basic-pitch", "cqt", "simple"),
        default="basic-pitch",
        help="initial analysis backend (default: basic-pitch)",
    )
    gui_parser.add_argument("--no-render-midi", action="store_true", help="do not render MIDI previews with TiMidity++")
    gui_parser.set_defaults(handler=_handle_gui)

    separate_parser = subparsers.add_parser(
        "separate",
        help="split a mix into per-instrument stem WAVs (vocals/drums/bass/other)",
        description=(
            "Separate an audio file into stems using HT-Demucs (ONNX, no PyTorch). "
            "Each stem is a WAV you can transcribe with `notegrabber analyze`. "
            "Install with `python3 -m pip install -e '.[separate]'`."
        ),
    )
    separate_parser.add_argument("input_audio", type=Path, help="input audio file to separate")
    separate_parser.add_argument("--out-dir", required=True, type=Path, help="output directory for the stem WAV files")
    separate_parser.add_argument(
        "--model",
        choices=tuple(SEPARATION_MODELS),
        default=DEFAULT_SEPARATION_MODEL,
        help=f"separation model (default: {DEFAULT_SEPARATION_MODEL}); htdemucs_6s adds guitar/piano but is heavier",
    )
    separate_parser.add_argument(
        "--stems",
        help="comma-separated subset of stems to write (default: all stems the model produces)",
    )
    separate_parser.add_argument(
        "--precision",
        choices=("fp16", "fp32"),
        default="fp16",
        help="model weight precision; fp16 is smaller/faster to download (default: fp16)",
    )
    separate_parser.set_defaults(handler=_handle_separate)

    return parser


def add_analysis_tuning_arguments(parser: argparse.ArgumentParser) -> None:
    """Add backend tuning flags shared by analyze and visualize."""

    parser.add_argument(
        "--threshold",
        type=float,
        default=CQT_THRESHOLD,
        help=f"CQT note extraction activation threshold (default: {CQT_THRESHOLD})",
    )
    parser.add_argument(
        "--onset-threshold",
        type=float,
        default=BASIC_PITCH_ONSET_THRESHOLD,
        help=f"Basic Pitch onset threshold (default: {BASIC_PITCH_ONSET_THRESHOLD})",
    )
    parser.add_argument(
        "--frame-threshold",
        type=float,
        default=BASIC_PITCH_FRAME_THRESHOLD,
        help=f"Basic Pitch frame threshold (default: {BASIC_PITCH_FRAME_THRESHOLD})",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=BASIC_PITCH_MIN_DURATION_SECONDS,
        help=f"minimum extracted note duration in seconds for CQT/Basic Pitch (default: {BASIC_PITCH_MIN_DURATION_SECONDS})",
    )


def _handle_analyze(args: argparse.Namespace) -> int:
    input_audio: Path = args.input_audio
    output_midi: Path = args.out
    output_heatmap: Path | None = args.heatmap
    backend: str = args.backend

    if not input_audio.exists():
        print(f"notegrabber: input audio not found: {input_audio}", file=sys.stderr)
        return 2
    if not input_audio.is_file():
        print(f"notegrabber: input audio is not a file: {input_audio}", file=sys.stderr)
        return 2

    try:
        notes = analyze_wav_to_midi(
            input_audio,
            output_midi,
            heatmap_path=output_heatmap,
            backend=backend,
            threshold=args.threshold,
            onset_threshold=args.onset_threshold,
            frame_threshold=args.frame_threshold,
            min_duration_seconds=args.min_duration,
        )
    except Exception as exc:  # argparse-style CLI: report failure without a traceback.
        print(f"notegrabber: analyze failed: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {output_midi} ({len(notes)} note{'s' if len(notes) != 1 else ''})")
    return 0


def _handle_separate(args: argparse.Namespace) -> int:
    input_audio: Path = args.input_audio
    if not input_audio.exists():
        print(f"notegrabber: input audio not found: {input_audio}", file=sys.stderr)
        return 2
    if not input_audio.is_file():
        print(f"notegrabber: input audio is not a file: {input_audio}", file=sys.stderr)
        return 2

    stems = [s.strip() for s in args.stems.split(",") if s.strip()] if args.stems else None
    try:
        result = separate_stems(
            input_audio,
            args.out_dir,
            model=args.model,
            stems=stems,
            precision=args.precision,
        )
    except Exception as exc:  # argparse-style CLI: report failure without a traceback.
        print(f"notegrabber: separate failed: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {len(result.stem_paths)} stem(s) to {result.output_dir}:")
    for name, path in result.stem_paths.items():
        print(f"  {name}: {path}")
    return 0


def _handle_serve(args: argparse.Namespace) -> int:
    try:
        serve_upload_app(
            host=args.host,
            port=args.port,
            out_dir=args.out_dir,
            default_backend=args.backend,
            render_midi=not args.no_render_midi,
        )
    except KeyboardInterrupt:
        print("notegrabber: upload server stopped")
    except Exception as exc:
        print(f"notegrabber: serve failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _handle_gui(args: argparse.Namespace) -> int:
    try:
        from .gui.app import run_gui
    except Exception as exc:
        print(f"notegrabber: GUI startup failed: {exc}", file=sys.stderr)
        return 1
    return run_gui(audio=args.audio, backend=args.backend, render_midi=not args.no_render_midi)


def _handle_visualize(args: argparse.Namespace) -> int:
    input_audio: Path = args.input_audio
    out_dir: Path = args.out_dir
    backend: str = args.backend

    if not input_audio.exists():
        print(f"notegrabber: input audio not found: {input_audio}", file=sys.stderr)
        return 2
    if not input_audio.is_file():
        print(f"notegrabber: input audio is not a file: {input_audio}", file=sys.stderr)
        return 2

    try:
        outputs = create_visualization(
            input_audio,
            out_dir,
            backend=backend,
            render_midi=not args.no_render_midi,
            threshold=args.threshold,
            onset_threshold=args.onset_threshold,
            frame_threshold=args.frame_threshold,
            min_duration_seconds=args.min_duration,
        )
    except Exception as exc:
        print(f"notegrabber: visualize failed: {exc}", file=sys.stderr)
        return 1

    print(f"wrote visualization {outputs['html']}")
    if outputs.get("midi_audio") is None and not args.no_render_midi:
        print("notegrabber: MIDI WAV preview was not rendered; install/configure TiMidity++ for browser playback", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the notegrabber CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
