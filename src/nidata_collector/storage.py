from __future__ import annotations

import csv
import json
import os
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .config import AcquisitionGroup, ChannelSelection, RunConfiguration, SignalType


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
        self.config = config
        self.run_dir = config.output_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.run_dir / "manifest.json"
        self.segment_records_path = self.run_dir / "segment_records.csv"
        self._record_lock = threading.Lock()
        self._write_manifest(config, device_snapshot)
        self._write_run_records(config)

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

    def _write_run_records(self, config: RunConfiguration) -> None:
        write_dict_csv(
            self.run_dir / "experiment_record.csv",
            [run_record_row(self.run_id, config)],
            EXPERIMENT_RECORD_COLUMNS,
        )
        write_dict_csv(
            self.run_dir / "spindle_info.csv",
            [spindle_info_row(self.run_id, config)],
            SPINDLE_INFO_COLUMNS,
        )
        sensor_rows = []
        for group in config.groups:
            for channel in group.channels:
                sensor_rows.append(sensor_info_row(self.run_id, group, channel))
        write_dict_csv(self.run_dir / "sensor_info.csv", sensor_rows, SENSOR_INFO_COLUMNS)
        write_dict_csv(self.segment_records_path, [], SEGMENT_RECORD_COLUMNS)

    def build_segment_record(
        self,
        group: AcquisitionGroup,
        channels: list[str],
        unit: str,
        sample_rate_hz: float,
        sample_count: int,
        sample_start_index: int,
        partial: bool,
        csv_path: Path,
        json_path: Path,
    ) -> dict[str, Any]:
        base = run_record_row(self.run_id, self.config)
        base.update(
            {
                "signal_type": group.signal_type.value,
                "channels": ";".join(channels),
                "unit": unit,
                "sample_rate_hz": sample_rate_hz,
                "sample_count": sample_count,
                "sample_duration_s": sample_count / sample_rate_hz if sample_rate_hz else "",
                "sample_start_index": sample_start_index,
                "partial": partial,
                "data_file": csv_path.name,
                "metadata_file": json_path.name,
                "data_path": str(csv_path),
                "metadata_path": str(json_path),
            }
        )
        return base

    def append_segment_record(self, record: dict[str, Any]) -> None:
        with self._record_lock:
            append_dict_csv(self.segment_records_path, record, SEGMENT_RECORD_COLUMNS)


class SegmentWriter:
    def __init__(self, run_storage: RunStorage, group: AcquisitionGroup) -> None:
        self.run_storage = run_storage
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
        segment_record = self.run_storage.build_segment_record(
            self.group,
            self.save_channels,
            self.group.signal_type.unit,
            sample_rate_hz,
            int(selected.shape[1]),
            sample_start_index,
            partial,
            csv_path,
            json_path,
        )
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
            "experiment_record": to_jsonable(self.run_storage.config.experiment_record),
            "segment_record": to_jsonable(segment_record),
            "stats": segment_stats(selected, self.save_channels, self.group.signal_type.unit),
        }
        atomic_write_json(json_path, metadata)
        self.run_storage.append_segment_record(segment_record)
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


EXPERIMENT_RECORD_COLUMNS = [
    "run_id",
    "operator_note",
    "spindle_id",
    "spindle_model",
    "rated_speed_rpm",
    "max_speed_rpm",
    "test_date",
    "accumulated_runtime_hours",
    "target_speed_rpm",
    "actual_speed_rpm",
    "ramp_method",
    "run_duration_s",
    "preheated",
    "thermal_state",
    "front_bearing_temp_deg_c",
    "rear_bearing_temp_deg_c",
    "motor_housing_temp_deg_c",
    "ambient_temp_deg_c",
    "set_speed_rpm",
    "speed_actual_rpm",
    "speed_fluctuation_rpm",
    "has_phase_signal",
    "abnormal_noise",
    "over_temperature",
    "alarm",
    "cable_loose",
    "acquisition_interrupted",
    "misoperation",
    "exception_note",
    "rotation_accuracy_measured",
    "rotation_accuracy_value",
    "rotation_accuracy_position",
    "rotation_accuracy_condition",
    "label",
]

SPINDLE_INFO_COLUMNS = [
    "run_id",
    "spindle_id",
    "spindle_model",
    "rated_speed_rpm",
    "max_speed_rpm",
    "test_date",
    "accumulated_runtime_hours",
]

SENSOR_INFO_COLUMNS = [
    "run_id",
    "signal_type",
    "channel",
    "device_name",
    "product_type",
    "sensor_id",
    "measurement_position",
    "direction",
    "mounting_method",
    "sample_rate_hz",
    "unit",
    "plot",
    "save",
]

SEGMENT_RECORD_COLUMNS = [
    *EXPERIMENT_RECORD_COLUMNS,
    "signal_type",
    "channels",
    "unit",
    "sample_rate_hz",
    "sample_count",
    "sample_duration_s",
    "sample_start_index",
    "partial",
    "data_file",
    "metadata_file",
    "data_path",
    "metadata_path",
]


def run_record_row(run_id: str, config: RunConfiguration) -> dict[str, Any]:
    record = config.experiment_record
    return {
        "run_id": run_id,
        "operator_note": config.operator_note,
        "spindle_id": record.spindle.spindle_id,
        "spindle_model": record.spindle.model,
        "rated_speed_rpm": record.spindle.rated_speed_rpm,
        "max_speed_rpm": record.spindle.max_speed_rpm,
        "test_date": record.spindle.test_date,
        "accumulated_runtime_hours": record.spindle.accumulated_runtime_hours,
        "target_speed_rpm": record.condition.target_speed_rpm,
        "actual_speed_rpm": record.condition.actual_speed_rpm,
        "ramp_method": record.condition.ramp_method,
        "run_duration_s": record.condition.run_duration_s,
        "preheated": record.condition.preheated,
        "thermal_state": record.condition.thermal_state,
        "front_bearing_temp_deg_c": record.temperature.front_bearing_deg_c,
        "rear_bearing_temp_deg_c": record.temperature.rear_bearing_deg_c,
        "motor_housing_temp_deg_c": record.temperature.motor_housing_deg_c,
        "ambient_temp_deg_c": record.temperature.ambient_deg_c,
        "set_speed_rpm": record.speed.set_speed_rpm,
        "speed_actual_rpm": record.speed.actual_speed_rpm,
        "speed_fluctuation_rpm": record.speed.fluctuation_rpm,
        "has_phase_signal": record.speed.has_phase_signal,
        "abnormal_noise": record.exception.abnormal_noise,
        "over_temperature": record.exception.over_temperature,
        "alarm": record.exception.alarm,
        "cable_loose": record.exception.cable_loose,
        "acquisition_interrupted": record.exception.acquisition_interrupted,
        "misoperation": record.exception.misoperation,
        "exception_note": record.exception.note,
        "rotation_accuracy_measured": record.followup.rotation_accuracy_measured,
        "rotation_accuracy_value": record.followup.rotation_accuracy_value,
        "rotation_accuracy_position": record.followup.measurement_position,
        "rotation_accuracy_condition": record.followup.measurement_condition,
        "label": record.followup.label,
    }


def spindle_info_row(run_id: str, config: RunConfiguration) -> dict[str, Any]:
    spindle = config.experiment_record.spindle
    return {
        "run_id": run_id,
        "spindle_id": spindle.spindle_id,
        "spindle_model": spindle.model,
        "rated_speed_rpm": spindle.rated_speed_rpm,
        "max_speed_rpm": spindle.max_speed_rpm,
        "test_date": spindle.test_date,
        "accumulated_runtime_hours": spindle.accumulated_runtime_hours,
    }


def sensor_info_row(run_id: str, group: AcquisitionGroup, channel: ChannelSelection) -> dict[str, Any]:
    sensor = channel.sensor
    return {
        "run_id": run_id,
        "signal_type": group.signal_type.value,
        "channel": channel.physical_name,
        "device_name": channel.device_name,
        "product_type": channel.product_type,
        "sensor_id": sensor.sensor_id,
        "measurement_position": sensor.measurement_position,
        "direction": sensor.direction,
        "mounting_method": sensor.mounting_method,
        "sample_rate_hz": group.settings.sample_rate_hz,
        "unit": group.signal_type.unit,
        "plot": channel.visualize,
        "save": channel.save,
    }


def write_dict_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(csv_row(row, columns))
    os.replace(tmp_path, path)


def append_dict_csv(path: Path, row: dict[str, Any], columns: list[str]) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writerow(csv_row(row, columns))


def csv_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {column: "" if row.get(column) is None else row.get(column, "") for column in columns}


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
