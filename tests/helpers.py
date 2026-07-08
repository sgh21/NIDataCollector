from __future__ import annotations

import io
import lzma
from pathlib import Path

import numpy as np


def write_npz_xz_segment(
    path: Path,
    *,
    data: np.ndarray,
    sample_rate_hz: float,
    channels: list[str],
    signal_type: str = "acceleration",
    unit: str = "g",
    sample_start_index: int = 0,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(data, dtype=float)
    if data.ndim != 2:
        raise ValueError("data must have shape (channel_count, sample_count)")
    if data.shape[0] != len(channels):
        raise ValueError("channels length must match data.shape[0]")

    sample_count = data.shape[1]
    time_s = (sample_start_index + np.arange(sample_count, dtype=float)) / float(sample_rate_hz)

    buffer = io.BytesIO()
    np.savez(
        buffer,
        time_s=time_s.astype(np.float64),
        data=data.astype(np.float64),
        channels=np.asarray(channels, dtype=str),
        sample_start_index=np.asarray(sample_start_index, dtype=np.int64),
        sample_rate_hz=np.asarray(float(sample_rate_hz), dtype=np.float64),
        signal_type=np.asarray(signal_type, dtype=str),
        unit=np.asarray(unit, dtype=str),
    )

    with lzma.open(path, "wb") as handle:
        handle.write(buffer.getvalue())

    return path
