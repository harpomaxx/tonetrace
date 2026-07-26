"""Command-line entry point for notegrabber."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyzer import analyze_wav_to_midi
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
    analyze_parser.set_defaults(handler=_handle_analyze)

    visualize_parser = subparsers.add_parser(
        "visualize",
        help="create a browser heatmap viewer and optional rendered MIDI audio",
        description="Analyze an input audio WAV file with the CQT backend by default and write a local HTML viewer.",
    )
    visualize_parser.add_argument("input_audio", type=Path, help="input audio WAV file")
    visualize_parser.add_argument("--out-dir", required=True, type=Path, help="output directory for index.html and generated assets")
    visualize_parser.add_argument(
        "--backend",
        choices=("simple", "cqt", "basic-pitch"),
        default="cqt",
        help="analysis backend to visualize: simple deterministic DSP, CQT/librosa, or Spotify Basic Pitch ML (default: cqt)",
    )
    visualize_parser.add_argument("--no-render-midi", action="store_true", help="do not invoke TiMidity++ to render MIDI to WAV")
    visualize_parser.set_defaults(handler=_handle_visualize)

    return parser


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
        notes = analyze_wav_to_midi(input_audio, output_midi, heatmap_path=output_heatmap, backend=backend)
    except Exception as exc:  # argparse-style CLI: report failure without a traceback.
        print(f"notegrabber: analyze failed: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {output_midi} ({len(notes)} note{'s' if len(notes) != 1 else ''})")
    return 0


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
        outputs = create_visualization(input_audio, out_dir, backend=backend, render_midi=not args.no_render_midi)
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
