from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .config import AcquisitionGroup, RunConfiguration, SignalType


def safe_name(value: str) -> str:
    keep = []
    for char in value:
        if char.isalnum() or char in ("-", "_", "."):
            keep.append(char)
        else:
            keep.append("_")
    return "".join(keep).strip("_") or "unnamed"


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, SignalType):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value


class RunStorage:
    def __init__(self, config: RunConfiguration, device_snapshot: dict) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = f"run_{stamp}"
        self.run_dir = config.output_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.run_dir / "manifest.json"
        self._write_manifest(config, device_snapshot)

    def _write_manifest(self, config: RunConfiguration, device_snapshot: dict) -> None:
        payload = {
            "run_id": self.run_id,
            "created_at_local": datetime.now().isoformat(timespec="seconds"),
            "time_axis": "sample_index / configured_sample_rate_hz; generated from DAQmx hardware-timed samples",
            "output_dir": str(self.run_dir),
            "configuration": to_jsonable(config),
            "device_snapshot": to_jsonable(device_snapshot),
        }
        atomic_write_json(self.manifest_path, payload)

    def group_dir(self, group: AcquisitionGroup) -> Path:
        setting = group.settings
        dirname = (
            f"{group.signal_type.value}_"
            f"{setting.sample_rate_hz:g}Hz_"
            f"{setting.segment_samples}samples"
        )
        path = self.run_dir / safe_name(dirname)
        path.mkdir(parents=True, exist_ok=True)
        return path


class SegmentWriter:
    def __init__(self, run_storage: RunStorage, group: AcquisitionGroup) -> None:
        self.group = group
        self.root = run_storage.group_dir(group)
        self.segment_index = 0
        self.save_channels = group.save_channels
        self.save_indices = [
            index for index, channel in enumerate(group.read_channels) if channel in self.save_channels
        ]

    def write_segment(
        self,
        sample_start_index: int,
        sample_rate_hz: float,
        time_s: np.ndarray,
        data: np.ndarray,
        partial: bool = False,
    ) -> tuple[Path, Path] | None:
        if not self.save_channels:
            return None

        self.segment_index += 1
        settings = self.group.settings
        tag = "partial" if partial else "segment"
        base = (
            f"{self.segment_index:06d}_{tag}_"
            f"{self.group.signal_type.value}_"
            f"{sample_rate_hz:g}Hz_"
            f"{data.shape[1]}samples_"
            f"start{sample_start_index}"
        )
        csv_path = self.root / f"{safe_name(base)}.csv"
        json_path = self.root / f"{safe_name(base)}.json"

        selected = data[self.save_indices, :]
        write_segment_csv(csv_path, sample_start_index, time_s, selected, self.save_channels)
        metadata = {
            "segment_index": self.segment_index,
            "partial": partial,
            "signal_type": self.group.signal_type.value,
            "unit": self.group.signal_type.unit,
            "channels": self.save_channels,
            "sample_start_index": sample_start_index,
            "sample_count": int(selected.shape[1]),
            "sample_rate_hz": sample_rate_hz,
            "time_axis": "time_s = sample_index / sample_rate_hz from DAQmx hardware-timed samples",
            "settings": to_jsonable(settings),
            "stats": segment_stats(selected, self.save_channels, self.group.signal_type.unit),
        }
        atomic_write_json(json_path, metadata)
        return csv_path, json_path


def write_segment_csv(
    path: Path,
    sample_start_index: int,
    time_s: np.ndarray,
    data: np.ndarray,
    channels: list[str],
) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    header = ["sample_index", "time_s", *channels]
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for column in range(data.shape[1]):
            sample_index = sample_start_index + column
            writer.writerow([sample_index, time_s[column], *data[:, column]])
    os.replace(tmp_path, path)


def atomic_write_json(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def segment_stats(data: np.ndarray, channels: list[str], unit: str) -> list[dict]:
    stats = []
    for index, channel in enumerate(channels):
        values = data[index]
        stats.append(
            {
                "channel": channel,
                "unit": unit,
                "mean": float(np.mean(values)),
                "rms": float(np.sqrt(np.mean(np.square(values)))),
                "min": float(np.min(values)),
                "max": float(np.max(values)),
                "peak_to_peak": float(np.ptp(values)),
            }
        )
    return stats
