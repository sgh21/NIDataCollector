from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from data_analysis.displacement_response import analyze_displacement_response
from data_analysis.vibration_io import VibrationPayloadError


def _optional_float(value: str) -> float | None:
    if value.strip().lower() in {"none", "off", "no"}:
        return None
    return float(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze acceleration-derived displacement response by spindle speed.")
    parser.add_argument("--data-root", type=Path, default=Path("data/runs"), help="Run-directory root")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("analysis_out/displacement_response"),
        help="Output directory for CSV, JSON, PNG, and displacement .npz.xz files",
    )
    parser.add_argument("--analysis-duration-s", type=float, default=60.0, help="Stable-window duration per run")
    parser.add_argument("--highpass-hz", type=float, default=10.0, help="Displacement integration high-pass cutoff")
    parser.add_argument(
        "--lowpass-hz",
        type=_optional_float,
        default=1000.0,
        help="Displacement integration low-pass cutoff; use 'none' to disable",
    )
    parser.add_argument(
        "--stable-tolerance-rpm",
        type=float,
        default=None,
        help="Stable-speed tolerance; default is max(10 rpm, 0.2 percent of target)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        analysis = analyze_displacement_response(
            data_root=args.data_root,
            output_dir=args.output_dir,
            analysis_duration_s=args.analysis_duration_s,
            highpass_hz=args.highpass_hz,
            lowpass_hz=args.lowpass_hz,
            stable_tolerance_rpm=args.stable_tolerance_rpm,
        )
    except (VibrationPayloadError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Analyzed channels: {len(analysis['channels'])}")
    print(f"Skipped runs: {len(analysis['skipped'])}")
    print(f"Output: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
