from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nidata_collector.acquisition import (
    AcquisitionConfig,
    acquire_acceleration,
    generate_simulated_acceleration,
)
from nidata_collector.analysis import compute_channel_stats
from nidata_collector.devices import find_ai_channels, reserve_network_devices, unreserve_network_devices
from nidata_collector.output import build_output_paths, save_csv, save_metadata, save_waveform_plot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire vibration data from an NI 9234 module.")
    parser.add_argument(
        "--channel",
        action="append",
        dest="channels",
        help="Physical channel to acquire. Can be passed more than once.",
    )
    parser.add_argument(
        "--all-channels",
        action="store_true",
        help="Acquire all detected NI 9234 AI channels instead of the first channel.",
    )
    parser.add_argument("--sample-rate", type=float, default=5120.0, help="Sample rate in Hz.")
    parser.add_argument("--duration", type=float, default=2.0, help="Acquisition duration in seconds.")
    parser.add_argument(
        "--sensor-sensitivity-mv-per-g",
        type=float,
        default=100.0,
        help="Accelerometer sensitivity in mV/g.",
    )
    parser.add_argument("--min-g", type=float, default=-50.0, help="Expected minimum acceleration in g.")
    parser.add_argument("--max-g", type=float, default=50.0, help="Expected maximum acceleration in g.")
    parser.add_argument(
        "--excitation-current",
        type=float,
        default=0.004,
        help="IEPE current excitation in amperes.",
    )
    parser.add_argument(
        "--coupling",
        default="AC",
        choices=["AC", "DC", "GND", "NONE"],
        help="Input coupling mode. Use NONE to leave the DAQmx default unchanged.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=5.0,
        help="Extra seconds to acquire and discard after task start.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data",
        help="Directory for CSV, PNG, and metadata outputs.",
    )
    parser.add_argument("--prefix", default="vibration", help="Output file prefix.")
    parser.add_argument("--show", action="store_true", help="Show the waveform window after saving PNG.")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Generate synthetic vibration data instead of reading NI hardware.",
    )
    parser.add_argument(
        "--no-reserve",
        action="store_true",
        help="Do not reserve TCP/IP NI devices before acquisition.",
    )
    parser.add_argument(
        "--override-reservation",
        action="store_true",
        help="Override an existing network device reservation.",
    )
    return parser


def resolve_channels(args: argparse.Namespace) -> list[str]:
    if args.channels:
        return [channel for item in args.channels for channel in item.split(",") if channel]

    if args.simulate:
        return ["sim_ai0", "sim_ai1"] if args.all_channels else ["sim_ai0"]

    detected = find_ai_channels(product_type_hint="9234")
    if not detected:
        raise RuntimeError(
            "No NI 9234 AI channels were detected. Run scripts\\ni_probe.py and check NI MAX."
        )
    return detected if args.all_channels else detected[:1]


def print_stats(stats: list[dict]) -> None:
    print("Channel statistics:")
    for item in stats:
        print(
            "  - {channel}: mean={mean_g:.6g} g, rms={rms_g:.6g} g, "
            "peak_to_peak={peak_to_peak_g:.6g} g".format(**item)
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.sample_rate <= 0:
        raise ValueError("--sample-rate must be positive.")
    if args.duration <= 0:
        raise ValueError("--duration must be positive.")
    if args.settle_seconds < 0:
        raise ValueError("--settle-seconds must be zero or positive.")
    if args.min_g >= args.max_g:
        raise ValueError("--min-g must be smaller than --max-g.")

    reserved_devices = []
    if not args.simulate and not args.no_reserve:
        for item in reserve_network_devices(override=args.override_reservation):
            status = "OK" if item.ok else "FAILED"
            message = f": {item.message}" if item.message else ""
            print(f"Reservation {item.device}: {status}{message}")
            if item.ok:
                reserved_devices.append(item.device)

    channels = resolve_channels(args)
    config = AcquisitionConfig(
        channels=channels,
        sample_rate_hz=args.sample_rate,
        duration_s=args.duration,
        sensitivity_mv_per_g=args.sensor_sensitivity_mv_per_g,
        min_g=args.min_g,
        max_g=args.max_g,
        excitation_current_a=args.excitation_current,
        coupling=None if args.coupling == "NONE" else args.coupling,
        settle_s=args.settle_seconds,
    )

    try:
        if args.simulate:
            time_s, data = generate_simulated_acceleration(config)
        else:
            time_s, data = acquire_acceleration(config)

        paths = build_output_paths(args.output_dir, args.prefix)
        save_csv(paths.csv_path, time_s, data, channels)
        save_waveform_plot(paths.png_path, time_s, data, channels, show=args.show)

        stats = compute_channel_stats(data, channels)
        metadata = {
            "mode": "simulated" if args.simulate else "nidaqmx",
            "channels": channels,
            "sample_rate_hz": args.sample_rate,
            "duration_s": args.duration,
            "samples_per_channel": int(data.shape[1]),
            "sensor_sensitivity_mv_per_g": args.sensor_sensitivity_mv_per_g,
            "min_g": args.min_g,
            "max_g": args.max_g,
            "excitation_current_a": args.excitation_current,
            "coupling": None if args.coupling == "NONE" else args.coupling,
            "settle_s": args.settle_seconds,
            "stats": stats,
        }
        save_metadata(paths.json_path, metadata)

        print(f"Acquired {data.shape[1]} samples/channel from {len(channels)} channel(s).")
        print(f"CSV: {paths.csv_path}")
        print(f"PNG: {paths.png_path}")
        print(f"Metadata: {paths.json_path}")
        print_stats(stats)
    finally:
        if reserved_devices:
            for item in unreserve_network_devices(reserved_devices):
                status = "OK" if item.ok else "FAILED"
                message = f": {item.message}" if item.message else ""
                print(f"Release {item.device}: {status}{message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
