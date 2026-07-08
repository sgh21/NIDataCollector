from __future__ import annotations

import argparse
import lzma
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np


def load_npz_xz(path: Path) -> dict[str, np.ndarray]:
    with lzma.open(path, "rb") as handle:
        payload_bytes = handle.read()
    with np.load(BytesIO(payload_bytes), allow_pickle=False) as payload:
        return {key: np.asarray(payload[key]) for key in payload.files}


def scalar_text(payload: dict[str, np.ndarray], key: str) -> str:
    value = payload.get(key)
    if value is None or value.size == 0:
        return ""
    return str(value.reshape(-1)[0])


def print_summary(path: Path, payload: dict[str, np.ndarray], head: int) -> None:
    print(f"file: {path}")
    print("arrays:")
    for key, value in payload.items():
        print(f"  {key}: dtype={value.dtype}, shape={value.shape}")

    print(f"signal_type: {scalar_text(payload, 'signal_type')}")
    print(f"unit: {scalar_text(payload, 'unit')}")
    print(f"sample_rate_hz: {scalar_text(payload, 'sample_rate_hz')}")
    print(f"sample_start_index: {scalar_text(payload, 'sample_start_index')}")

    channels = payload.get("channels")
    if channels is not None:
        print("channels:")
        for index, channel in enumerate(channels.tolist()):
            print(f"  [{index}] {channel}")

    time_s = payload.get("time_s")
    data = payload.get("data")
    if time_s is None or data is None:
        return
    if data.ndim != 2:
        print(f"warning: data is not 2D, got shape={data.shape}")
        return
    if len(time_s) != data.shape[1]:
        print(f"warning: len(time_s)={len(time_s)} but data samples={data.shape[1]}")
        return

    row_count = min(max(head, 0), len(time_s))
    if row_count == 0:
        return

    names = [str(item) for item in (channels.tolist() if channels is not None else range(data.shape[0]))]
    print("preview:")
    print(",".join(["time_s", *names]))
    for sample_index in range(row_count):
        values: list[Any] = [f"{float(time_s[sample_index]):.9g}"]
        values.extend(f"{float(data[channel_index, sample_index]):.9g}" for channel_index in range(data.shape[0]))
        print(",".join(values))


def save_preview_plot(path: Path, payload: dict[str, np.ndarray], output: Path, max_points: int) -> None:
    time_s = payload.get("time_s")
    data = payload.get("data")
    channels = payload.get("channels")
    if time_s is None or data is None:
        raise ValueError("payload must contain time_s and data arrays.")
    if data.ndim != 2 or len(time_s) != data.shape[1]:
        raise ValueError("expected data shape (channel_count, sample_count) aligned with time_s.")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    stride = max(1, int(np.ceil(len(time_s) / max(max_points, 1))))
    names = [str(item) for item in (channels.tolist() if channels is not None else range(data.shape[0]))]

    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=130)
    for channel_index, name in enumerate(names):
        ax.plot(time_s[::stride], data[channel_index, ::stride], linewidth=1.3, label=name)
    ax.set_title(path.name)
    ax.set_xlabel("time_s")
    ax.set_ylabel(scalar_text(payload, "unit") or "value")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize="small")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a compressed NIDataCollector .npz.xz raw segment.")
    parser.add_argument("path", type=Path, help="Path to a .npz.xz raw segment.")
    parser.add_argument("--head", type=int, default=5, help="Number of sample rows to print.")
    parser.add_argument("--plot", type=Path, help="Optional PNG path for a quick preview plot.")
    parser.add_argument("--max-points", type=int, default=5000, help="Maximum points per channel in the preview plot.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_npz_xz(args.path)
    print_summary(args.path, payload, args.head)
    if args.plot:
        save_preview_plot(args.path, payload, args.plot, args.max_points)
        print(f"plot: {args.plot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
