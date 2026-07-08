from __future__ import annotations

import csv
import json
import lzma
import os
import threading
from dataclasses import asdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np

from ..config import AcquisitionGroup, ChannelSelection, RunConfiguration, SignalType


STORAGE_FORMAT = "npz_xz_float64_with_time"
SEGMENT_SUMMARY_FILE = "segment_summary.csv"
TRENDS_DIR = "trends"
SUMMARY_WINDOW_SECONDS = 1.0
TREND_OVERVIEW_FILE = "summary_overview.png"


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
            "time_axis": (
                "sample_index / configured_sample_rate_hz; NI groups use DAQmx hardware-timed "
                "samples, DAMX-8013 NTC groups use serial poll count"
            ),
            "storage_format": STORAGE_FORMAT,
            "raw_segment_file": {
                "extension": ".npz.xz",
                "compression": "lzma/xz",
                "arrays": {
                    "time_s": "float64 array, shape=(sample_count,)",
                    "data": "float64 array, shape=(channel_count, sample_count)",
                    "channels": "string array aligned to data axis 0",
                    "sample_start_index": "int64 scalar array",
                    "sample_rate_hz": "float64 scalar array",
                    "signal_type": "string scalar array",
                    "unit": "string scalar array",
                },
                "alignment": "time_s[i] corresponds to data[:, i].",
            },
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
        data_path: Path,
    ) -> dict[str, Any]:
        base = run_record_row(self.run_id, self.config)
        time_start_s = sample_start_index / sample_rate_hz if sample_rate_hz else ""
        time_end_s = (sample_start_index + sample_count) / sample_rate_hz if sample_rate_hz and sample_count else ""
        time_center_s = (
            (float(time_start_s) + float(time_end_s)) / 2.0
            if time_start_s != "" and time_end_s != ""
            else ""
        )
        base.update(
            {
                "signal_type": group.signal_type.value,
                "channels": ";".join(channels),
                "unit": unit,
                "sample_rate_hz": sample_rate_hz,
                "sample_count": sample_count,
                "sample_duration_s": sample_count / sample_rate_hz if sample_rate_hz else "",
                "sample_start_index": sample_start_index,
                "sample_end_index": sample_start_index + sample_count - 1 if sample_count else sample_start_index,
                "time_start_s": time_start_s,
                "time_center_s": time_center_s,
                "time_end_s": time_end_s,
                "partial": partial,
                "data_format": STORAGE_FORMAT,
                "data_file": data_path.name,
                "data_path": str(data_path),
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
    ) -> Path | None:
        if not self.save_channels:
            return None

        self.segment_index += 1
        tag = "partial" if partial else "segment"
        base = (
            f"{self.segment_index:06d}_{tag}_"
            f"{self.group.signal_type.value}_"
            f"{sample_rate_hz:g}Hz_"
            f"{data.shape[1]}samples_"
            f"start{sample_start_index}"
        )
        data_path = self.root / f"{safe_name(base)}.npz.xz"

        selected = data[self.save_indices, :]
        write_segment_npz_xz(
            data_path,
            time_s,
            selected,
            self.save_channels,
            sample_start_index,
            sample_rate_hz,
            self.group.signal_type.value,
            self.group.signal_type.unit,
        )
        segment_record = self.run_storage.build_segment_record(
            self.group,
            self.save_channels,
            self.group.signal_type.unit,
            sample_rate_hz,
            int(selected.shape[1]),
            sample_start_index,
            partial,
            data_path,
        )
        segment_record["segment_index"] = self.segment_index
        self.run_storage.append_segment_record(segment_record)
        return data_path


def write_segment_npz_xz(
    path: Path,
    time_s: np.ndarray,
    data: np.ndarray,
    channels: list[str],
    sample_start_index: int,
    sample_rate_hz: float,
    signal_type: str,
    unit: str,
) -> None:
    if data.ndim != 2:
        raise ValueError("segment data must be a 2D array shaped (channel_count, sample_count).")
    time_values = np.asarray(time_s, dtype=np.float64)
    data_values = np.asarray(data, dtype=np.float64)
    if time_values.shape[0] != data_values.shape[1]:
        raise ValueError("time_s length must match data sample count.")
    if len(channels) != data_values.shape[0]:
        raise ValueError("channel count must match data channel axis.")

    buffer = BytesIO()
    np.savez(
        buffer,
        time_s=time_values,
        data=data_values,
        channels=np.asarray(channels, dtype=np.str_),
        sample_start_index=np.asarray([sample_start_index], dtype=np.int64),
        sample_rate_hz=np.asarray([sample_rate_hz], dtype=np.float64),
        signal_type=np.asarray([signal_type], dtype=np.str_),
        unit=np.asarray([unit], dtype=np.str_),
    )

    tmp_path = Path(str(path) + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(tmp_path, "wb", preset=6) as handle:
        handle.write(buffer.getvalue())
    os.replace(tmp_path, path)


def read_segment_npz_xz(path: Path) -> dict[str, np.ndarray]:
    with lzma.open(path, "rb") as handle:
        payload_bytes = handle.read()
    with np.load(BytesIO(payload_bytes), allow_pickle=False) as payload:
        return {
            "time_s": np.asarray(payload["time_s"], dtype=np.float64),
            "data": np.asarray(payload["data"], dtype=np.float64),
            "channels": np.asarray(payload["channels"], dtype=np.str_),
            "sample_start_index": np.asarray(payload["sample_start_index"], dtype=np.int64),
            "sample_rate_hz": np.asarray(payload["sample_rate_hz"], dtype=np.float64),
            "signal_type": np.asarray(payload["signal_type"], dtype=np.str_),
            "unit": np.asarray(payload["unit"], dtype=np.str_),
        }


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
    "segment_index",
    "signal_type",
    "channels",
    "unit",
    "sample_rate_hz",
    "sample_count",
    "sample_duration_s",
    "sample_start_index",
    "sample_end_index",
    "time_start_s",
    "time_center_s",
    "time_end_s",
    "partial",
    "data_format",
    "data_file",
    "data_path",
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


def read_dict_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def postprocess_run_outputs(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    segment_rows = read_dict_csv(run_dir / "segment_records.csv")
    sensor_labels = load_sensor_labels(run_dir / "sensor_info.csv")
    summary_rows: dict[tuple[float, float], dict[str, Any]] = {}
    metric_columns: list[str] = []

    for record in segment_rows:
        if record.get("data_format") != STORAGE_FORMAT:
            continue
        raw_data_path = record.get("data_path") or ""
        if not raw_data_path:
            continue
        data_path = Path(raw_data_path)
        if not data_path.is_absolute():
            data_path = run_dir / data_path
        if not data_path.exists():
            continue

        payload = read_segment_npz_xz(data_path)
        time_s = payload["time_s"]
        data = payload["data"]
        channels = [str(channel) for channel in payload["channels"].tolist()]
        signal_type = SignalType(str(payload["signal_type"][0]))
        for time_start_s, mask in iter_fixed_summary_windows(time_s):
            row = summary_row_for_window(summary_rows, time_start_s)
            window_data = data[:, mask]
            for item in segment_summary_stats(signal_type, window_data, channels):
                channel_label = safe_name(sensor_labels.get(item["channel"]) or item["channel"])
                prefix = f"{signal_type.value}__{channel_label}"
                for stat_name, value in item["stats"].items():
                    column = f"{prefix}__{stat_name}"
                    if column not in metric_columns:
                        metric_columns.append(column)
                    row[column] = value

    append_spindle_summary(run_dir, summary_rows, metric_columns)
    rows = sorted(summary_rows.values(), key=lambda item: float(item["time_start_s"]))
    columns = ["time_start_s", "time_center_s", "time_end_s", *ordered_metric_columns(metric_columns)]
    summary_path = run_dir / SEGMENT_SUMMARY_FILE
    write_dict_csv(summary_path, rows, columns)
    trend_paths = write_trend_plots(run_dir, rows, columns)
    annotate_manifest_with_postprocess(run_dir, summary_path, trend_paths)
    return {
        "summary_csv": summary_path,
        "trend_pngs": trend_paths,
    }


def iter_fixed_summary_windows(time_s: np.ndarray) -> list[tuple[float, np.ndarray]]:
    time_values = np.asarray(time_s, dtype=np.float64)
    valid = np.isfinite(time_values)
    if not np.any(valid):
        return []

    starts = np.floor(time_values[valid] / SUMMARY_WINDOW_SECONDS) * SUMMARY_WINDOW_SECONDS
    windows = []
    for time_start_s in np.unique(starts):
        time_end_s = float(time_start_s + SUMMARY_WINDOW_SECONDS)
        mask = (time_values >= time_start_s) & (time_values < time_end_s)
        if np.any(mask):
            windows.append((float(time_start_s), mask))
    return windows


def summary_row_for_window(
    summary_rows: dict[tuple[float, float], dict[str, Any]],
    time_start_s: float,
) -> dict[str, Any]:
    time_end_s = time_start_s + SUMMARY_WINDOW_SECONDS
    key = (round(time_start_s, 9), round(time_end_s, 9))
    return summary_rows.setdefault(
        key,
        {
            "time_start_s": time_start_s,
            "time_center_s": time_start_s + SUMMARY_WINDOW_SECONDS / 2.0,
            "time_end_s": time_end_s,
        },
    )


def segment_summary_stats(signal_type: SignalType, data: np.ndarray, channels: list[str]) -> list[dict[str, Any]]:
    stats = []
    for index, channel in enumerate(channels):
        values = np.asarray(data[index], dtype=np.float64)
        values = values[np.isfinite(values)]
        if not len(values):
            continue
        if signal_type == SignalType.ACCELERATION:
            channel_stats = {
                "mean_abs": float(np.mean(np.abs(values))),
                "max": float(np.max(values)),
                "min": float(np.min(values)),
            }
        else:
            channel_stats = {
                "mean": float(np.mean(values)),
                "max": float(np.max(values)),
                "min": float(np.min(values)),
            }
        stats.append({"channel": channel, "stats": channel_stats})
    return stats


def load_sensor_labels(path: Path) -> dict[str, str]:
    labels = {}
    for row in read_dict_csv(path):
        channel = row.get("channel", "")
        if not channel:
            continue
        sensor_id = row.get("sensor_id", "").strip()
        labels[channel] = sensor_id or channel
    return labels


def append_spindle_summary(
    run_dir: Path,
    summary_rows: dict[tuple[float, float], dict[str, Any]],
    metric_columns: list[str],
) -> None:
    telemetry_path = run_dir / "spindle_telemetry.csv"
    telemetry_rows = read_dict_csv(telemetry_path)
    if not telemetry_rows:
        return

    times = np.asarray([_float_or_default(row.get("time_s"), np.nan) for row in telemetry_rows], dtype=np.float64)
    speeds = np.asarray(
        [_float_or_default(row.get("actual_speed_rpm"), np.nan) for row in telemetry_rows],
        dtype=np.float64,
    )
    currents = np.asarray([_float_or_default(row.get("current_a"), np.nan) for row in telemetry_rows], dtype=np.float64)
    valid = np.isfinite(times)
    times = times[valid]
    speeds = speeds[valid]
    currents = currents[valid]
    if not len(times):
        return

    for start in np.unique(np.floor(times / SUMMARY_WINDOW_SECONDS) * SUMMARY_WINDOW_SECONDS):
        row = summary_row_for_window(summary_rows, float(start))
        end = float(start + SUMMARY_WINDOW_SECONDS)
        mask = (times >= start) & (times < end)
        append_spindle_metric(row, metric_columns, "spindle_speed", speeds[mask])
        append_spindle_metric(row, metric_columns, "spindle_current", currents[mask])


def append_spindle_metric(
    row: dict[str, Any],
    metric_columns: list[str],
    prefix: str,
    values: np.ndarray,
) -> None:
    values = values[np.isfinite(values)]
    if not len(values):
        return
    for stat_name, value in (
        ("mean", float(np.mean(values))),
        ("max", float(np.max(values))),
        ("min", float(np.min(values))),
    ):
        column = f"{prefix}__{stat_name}"
        if column not in metric_columns:
            metric_columns.append(column)
        row[column] = value


def ordered_metric_columns(columns: list[str]) -> list[str]:
    return sorted(columns, key=metric_column_sort_key)


def metric_column_sort_key(column: str) -> tuple[int, str, int, str]:
    parts = column.split("__")
    channel = parts[1] if len(parts) > 2 else ""
    stat = parts[-1] if len(parts) > 1 else ""
    stat_rank = {"mean_abs": 0, "mean": 0, "max": 1, "min": 2}.get(stat, 9)
    if column.startswith("acceleration__"):
        return (0, channel, stat_rank, column)
    if column.startswith("temperature_ntc__"):
        return (1, channel, stat_rank, column)
    if column.startswith("temperature_rtd__"):
        return (2, channel, stat_rank, column)
    if column.startswith("spindle_speed__"):
        return (3, channel, stat_rank, column)
    if column.startswith("spindle_current__"):
        return (4, channel, stat_rank, column)
    return (9, channel, stat_rank, column)


def write_trend_plots(run_dir: Path, rows: list[dict[str, Any]], columns: list[str]) -> list[Path]:
    if not rows:
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    trend_dir = run_dir / TRENDS_DIR
    trend_dir.mkdir(parents=True, exist_ok=True)
    x = np.asarray([float(row["time_center_s"]) for row in rows], dtype=np.float64)
    fig, axes = plt.subplots(4, 1, figsize=(12, 11), dpi=130, sharex=True)

    plot_summary_group(
        axes[0],
        rows,
        x,
        [column for column in columns if column.startswith("acceleration__") and column.endswith("__mean_abs")],
        "Vibration mean_abs",
        "g",
    )
    plot_summary_group(
        axes[1],
        rows,
        x,
        temperature_trend_columns(columns),
        "Temperature mean",
        "degC",
    )
    plot_summary_group(
        axes[2],
        rows,
        x,
        ["spindle_speed__mean"],
        "Spindle speed mean",
        "rpm",
    )
    plot_summary_group(
        axes[3],
        rows,
        x,
        ["spindle_current__mean"],
        "Spindle current mean",
        "A",
    )

    axes[-1].set_xlabel("Time center (s)")
    fig.tight_layout()
    path = trend_dir / TREND_OVERVIEW_FILE
    fig.savefig(path)
    plt.close(fig)
    return [path]


def plot_summary_group(
    ax: Any,
    rows: list[dict[str, Any]],
    x: np.ndarray,
    columns: list[str],
    title: str,
    ylabel: str,
) -> None:
    plotted = False
    for column in columns:
        y_values = np.asarray([_float_or_default(row.get(column), np.nan) for row in rows], dtype=np.float64)
        valid = np.isfinite(x) & np.isfinite(y_values)
        if not np.any(valid):
            continue
        ax.plot(x[valid], y_values[valid], linewidth=1.8, label=trend_label(column))
        plotted = True
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    if plotted:
        ax.legend(loc="best", fontsize="small")
    else:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center")


def temperature_trend_columns(columns: list[str]) -> list[str]:
    return sorted(
        [column for column in columns if column.startswith("temperature_") and column.endswith("__mean")],
        key=temperature_column_sort_key,
    )


def temperature_column_sort_key(column: str) -> tuple[int, str]:
    if column.startswith("temperature_ntc__"):
        return (0, column)
    if column.startswith("temperature_rtd__"):
        return (1, column)
    return (9, column)


def trend_label(column: str) -> str:
    if column == "spindle_speed__mean":
        return "speed rpm"
    if column == "spindle_current__mean":
        return "current A"
    parts = column.split("__")
    if len(parts) >= 3:
        return parts[1]
    return column


def annotate_manifest_with_postprocess(run_dir: Path, summary_path: Path, trend_paths: list[Path]) -> None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return
    payload["postprocess"] = {
        "segment_summary_csv": summary_path.name,
        "trends_dir": TRENDS_DIR,
        "trend_pngs": [f"{TRENDS_DIR}/{path.name}" for path in trend_paths],
    }
    atomic_write_json(manifest_path, payload)


def _float_or_default(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
