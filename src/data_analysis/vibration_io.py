from __future__ import annotations

from dataclasses import dataclass, replace
import lzma
from pathlib import Path
from typing import Any

import numpy as np


REQUIRED_FIELDS = (
    "time_s",
    "data",
    "channels",
    "sample_start_index",
    "sample_rate_hz",
    "signal_type",
    "unit",
)


class VibrationPayloadError(ValueError):
    """Raised when a vibration segment cannot be read or validated."""


@dataclass(frozen=True)
class VibrationSegment:
    path: Path
    time_s: np.ndarray
    data: np.ndarray
    channels: tuple[str, ...]
    sample_start_index: int
    sample_rate_hz: float
    signal_type: str
    unit: str
    warnings: tuple[str, ...] = ()


def read_vibration_segment(path: Path | str) -> VibrationSegment:
    segment_path = Path(path)
    if not segment_path.exists():
        raise VibrationPayloadError(f"input file does not exist: {segment_path}")
    if not segment_path.name.endswith(".npz.xz"):
        raise VibrationPayloadError(f"expected a .npz.xz file: {segment_path}")

    try:
        with lzma.open(segment_path, "rb") as handle:
            with np.load(handle, allow_pickle=False) as loaded:
                payload: dict[str, Any] = {name: loaded[name] for name in loaded.files}
    except Exception as exc:
        raise VibrationPayloadError(f"failed to read .npz.xz payload: {segment_path}: {exc}") from exc

    missing = [name for name in REQUIRED_FIELDS if name not in payload]
    if missing:
        raise VibrationPayloadError(f"missing required fields: {', '.join(missing)}")

    time_s = np.asarray(payload["time_s"], dtype=np.float64)
    data = np.asarray(payload["data"], dtype=np.float64)
    raw_channels = np.asarray(payload["channels"])
    if raw_channels.ndim != 1:
        raise VibrationPayloadError(f"channels must be 1D, got shape {raw_channels.shape}")
    if raw_channels.dtype.kind not in {"U", "S", "O"}:
        raise VibrationPayloadError(
            f"channels must be a 1-D string-like array, got dtype {raw_channels.dtype!r}"
        )
    if raw_channels.dtype.kind == "O":
        is_string_like = np.vectorize(lambda value: isinstance(value, (str, bytes, np.str_, np.bytes_)))(raw_channels)
        if not bool(np.all(is_string_like)):
            raise VibrationPayloadError("channels must contain string-like values")
    channels = tuple(str(value) for value in raw_channels.astype(str).tolist())
    sample_start_index = int(np.asarray(payload["sample_start_index"]).item())
    sample_rate_hz = float(np.asarray(payload["sample_rate_hz"]).item())
    if not np.isfinite(sample_rate_hz):
        raise VibrationPayloadError(f"sample_rate_hz must be finite, got {sample_rate_hz!r}")
    signal_type = str(np.asarray(payload["signal_type"]).item())
    unit = str(np.asarray(payload["unit"]).item())

    warnings: list[str] = []

    if signal_type != "acceleration":
        raise VibrationPayloadError(f"signal_type must be acceleration, got {signal_type!r}")
    if data.ndim != 2:
        raise VibrationPayloadError(f"data must be 2D with shape (channel_count, sample_count), got {data.shape}")
    if time_s.ndim != 1:
        raise VibrationPayloadError(f"time_s must be 1D, got shape {time_s.shape}")
    if len(time_s) != data.shape[1]:
        raise VibrationPayloadError(f"len(time_s) must equal data.shape[1], got {len(time_s)} and {data.shape[1]}")
    if len(channels) != data.shape[0]:
        raise VibrationPayloadError(f"len(channels) must equal data.shape[0], got {len(channels)} and {data.shape[0]}")
    if sample_rate_hz <= 0:
        raise VibrationPayloadError(f"sample_rate_hz must be positive, got {sample_rate_hz}")
    if not np.all(np.isfinite(time_s)):
        raise VibrationPayloadError("time_s contains non-finite values")
    if len(time_s) > 1:
        diffs = np.diff(time_s)
        if np.any(diffs < -1e-12):
            raise VibrationPayloadError("time_s must be monotonically increasing")
        expected_dt = 1.0 / sample_rate_hz
        if np.any(np.abs(diffs - expected_dt) > max(1e-9, expected_dt * 0.01)):
            warnings.append("time_s spacing differs from 1/sample_rate_hz by more than 1%")

    return VibrationSegment(
        path=segment_path,
        time_s=time_s,
        data=data,
        channels=channels,
        sample_start_index=sample_start_index,
        sample_rate_hz=sample_rate_hz,
        signal_type=signal_type,
        unit=unit,
        warnings=tuple(warnings),
    )


def select_channels(segment: VibrationSegment, channel: str | None = None) -> VibrationSegment:
    if channel is None:
        return segment
    if channel not in segment.channels:
        available = ", ".join(segment.channels)
        raise VibrationPayloadError(f"unknown channel {channel!r}; available channels: {available}")

    index = segment.channels.index(channel)
    return replace(
        segment,
        data=segment.data[index : index + 1, :],
        channels=(channel,),
    )
