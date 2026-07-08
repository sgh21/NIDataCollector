from __future__ import annotations

import argparse
from pathlib import Path
import sys

from data_analysis.vibration_features import analyze_segment
from data_analysis.vibration_io import VibrationPayloadError, read_vibration_segment, select_channels
from data_analysis.vibration_report import write_analysis_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a standard vibration .npz.xz segment.")
    parser.add_argument("input", type=Path, help="Path to a vibration .npz.xz segment")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for JSON, Markdown, and figures")
    parser.add_argument("--channel", default=None, help="Exact channel name to analyze")
    parser.add_argument("--rpm", type=float, default=None, help="Optional spindle speed in RPM")
    parser.add_argument("--top-peaks", type=int, default=10, help="Number of spectral peaks to report")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output_dir = args.output_dir or Path(f"{args.input.stem}_analysis")

    try:
        segment = read_vibration_segment(args.input)
        selected = select_channels(segment, args.channel)
        analysis = analyze_segment(selected, rpm=args.rpm, top_peaks=args.top_peaks)
        outputs = write_analysis_outputs(analysis, output_dir)
    except VibrationPayloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"JSON: {outputs['json']}")
    print(f"Markdown: {outputs['markdown']}")
    print(f"Figures: {outputs['figures_dir']}")
    return 0
