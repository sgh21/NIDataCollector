from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_analysis.vibration_io import VibrationPayloadError, VibrationSegment, read_vibration_segment, write_vibration_segment
from data_analysis.vibration_velocity import VelocityIntegrationConfig, integrate_acceleration_to_velocity, velocity_metadata


ANALYSIS_BANDS_HZ = (
    (0.0, 10.0),
    (10.0, 100.0),
    (100.0, 1000.0),
    (1000.0, 5000.0),
    (5000.0, None),
)

CONDITION_LABELS = {
    "run_20260707_6000rpm_9min": "标准/默认22℃冷却",
    "run_20260707_6000rpm_10min_20℃": "冷却20℃",
    "run_20260707_6000rpm_10min_24℃": "冷却24℃",
    "run_20260707_6000rpm_10min_reset": "振动传感器重新放置",
    "run_20260707_6000rpm_10min_reset_spindle": "重新安装刀柄",
    "run_20260707_6000rpm_10min_wo": "无冷却",
    "run_20260707_6000rpm_10min_wo_load": "不加刀柄",
}

CONDITION_ORDER = tuple(CONDITION_LABELS.values())

SPEED_REFERENCE_RUNS = {
    5000.0: "run_20260707_5000rpm_10min",
    6000.0: "run_20260707_6000rpm_9min",
    7000.0: "run_20260707_7000rpm_10min",
    8000.0: "run_20260707_8000rpm_10min",
}

REFERENCES = [
    {
        "title": "ISO 20816-1 overview",
        "url": "https://www.iso.org/standard/89921.html",
        "note": "ISO 20816 defines general machine-vibration measurement and evaluation principles.",
    },
    {
        "title": "SKF vibration sensor catalog",
        "url": "https://cdn.skfmediahub.skf.com/api/public/094e70a3b7aa46f7/pdf_preview_medium/11604_18_EN_-_Vibration_Sensor_Catalog_LOW_pdf_preview_medium.pdf",
        "note": "SKF describes velocity sensors as useful for low-to-medium-frequency rotating-machine monitoring.",
    },
    {
        "title": "Hansford HS-530 operating notes",
        "url": "https://hansfordsensors.com/wp-content/uploads/2023/03/HS530-Op.Notes-QM005.4.pdf",
        "note": "A commercial accelerometer conditioner uses 10 Hz to 1 kHz bandwidth for RMS velocity output.",
    },
]


@dataclass(frozen=True)
class StableWindow:
    target_rpm: float
    block_start_s: float
    block_end_s: float
    analysis_start_s: float
    analysis_end_s: float
    actual_speed_mean_rpm: float
    actual_speed_std_rpm: float
    actual_speed_min_rpm: float
    actual_speed_max_rpm: float
    speed_ok_ratio: float | None
    current_mean_a: float | None
    current_std_a: float | None


@dataclass(frozen=True)
class WindowSegment:
    segment: VibrationSegment
    source_files: tuple[str, ...]


def analyze_speed_velocity_response(
    *,
    data_root: Path,
    output_dir: Path,
    analysis_duration_s: float = 60.0,
    highpass_hz: float = 10.0,
    lowpass_hz: float | None = 1000.0,
    stable_tolerance_rpm: float | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    velocity_dir = output_dir / "velocity_segments"
    figures_dir.mkdir(parents=True, exist_ok=True)
    velocity_dir.mkdir(parents=True, exist_ok=True)

    config = VelocityIntegrationConfig(highpass_hz=highpass_hz, lowpass_hz=lowpass_hz)
    run_rows: list[dict[str, Any]] = []
    channel_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    windows: dict[str, StableWindow] = {}

    for run_dir in sorted(path for path in data_root.iterdir() if path.is_dir()):
        try:
            run_result = _analyze_run(
                run_dir=run_dir,
                velocity_dir=velocity_dir,
                analysis_duration_s=analysis_duration_s,
                config=config,
                stable_tolerance_rpm=stable_tolerance_rpm,
            )
        except SkipRun as exc:
            skipped.append({"run": run_dir.name, "reason": str(exc)})
            continue

        run_rows.append(run_result["run"])
        channel_rows.extend(run_result["channels"])
        windows[run_dir.name] = run_result["window"]

    if not run_rows:
        raise VibrationPayloadError("no single-target speed runs could be analyzed")

    run_df = pd.DataFrame(run_rows)
    channel_df = pd.DataFrame(channel_rows)
    by_speed_df = _aggregate_by_speed(run_df, channel_df)
    condition_df = _condition_rows_6000(channel_df)
    spectrum_df = _spectrum_energy_rows(channel_df)

    _write_tables(output_dir, run_df, channel_df, by_speed_df, condition_df, spectrum_df, skipped)
    generated_figures = _write_figures(
        data_root=data_root,
        figures_dir=figures_dir,
        run_df=run_df,
        channel_df=channel_df,
        by_speed_df=by_speed_df,
        condition_df=condition_df,
        windows=windows,
        config=config,
    )

    legacy_html = output_dir / "velocity_response_report.html"
    if legacy_html.exists():
        legacy_html.unlink()

    analysis = {
        "method": {
            "velocity_unit": "mm/s",
            "integration": "frequency_domain",
            "formula": "V(f)=A(f)/(j*2*pi*f)",
            "source_acceleration_unit": "g",
            "standard_gravity_mm_per_s2": 9806.65,
            "highpass_hz": highpass_hz,
            "lowpass_hz": lowpass_hz,
            "analysis_duration_s": analysis_duration_s,
            "stable_tolerance_rpm": stable_tolerance_rpm,
            "stable_window_policy": "last analysis_duration_s seconds of the longest stable target-speed block",
        },
        "condition_map": CONDITION_LABELS,
        "runs": _records_json_safe(run_df),
        "channels": _records_json_safe(channel_df),
        "by_speed": _records_json_safe(by_speed_df),
        "condition_response_6000": _records_json_safe(condition_df),
        "spectrum_energy": _records_json_safe(spectrum_df),
        "skipped": skipped,
        "figures": generated_figures,
        "references": REFERENCES,
    }
    (output_dir / "velocity_response_analysis.json").write_text(
        json.dumps(_json_safe(analysis), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return analysis


class SkipRun(RuntimeError):
    pass


def _analyze_run(
    *,
    run_dir: Path,
    velocity_dir: Path,
    analysis_duration_s: float,
    config: VelocityIntegrationConfig,
    stable_tolerance_rpm: float | None,
) -> dict[str, Any]:
    manifest = _read_manifest(run_dir)
    target_rpm = _target_rpm_from_manifest(manifest)
    if target_rpm is None:
        raise SkipRun("manifest has no single target_speed_rpm")

    telemetry_path = run_dir / "spindle_telemetry.csv"
    if not telemetry_path.exists():
        raise SkipRun("missing spindle_telemetry.csv")
    telemetry = pd.read_csv(telemetry_path)
    window = _find_stable_window(
        telemetry,
        target_rpm=target_rpm,
        analysis_duration_s=analysis_duration_s,
        tolerance_rpm=stable_tolerance_rpm,
    )
    sensor_map = _sensor_map(manifest)
    acceleration_window = load_acceleration_window(run_dir, window.analysis_start_s, window.analysis_end_s)
    velocity_data, warnings = integrate_acceleration_to_velocity(
        acceleration_window.segment.data,
        sample_rate_hz=acceleration_window.segment.sample_rate_hz,
        input_unit=acceleration_window.segment.unit,
        config=config,
    )
    velocity_path = velocity_dir / f"{_safe_name(run_dir.name)}_velocity_{_band_name(config)}_last{int(analysis_duration_s)}s.npz.xz"
    if not velocity_path.exists():
        write_vibration_segment(
            velocity_path,
            time_s=acceleration_window.segment.time_s,
            data=velocity_data,
            channels=acceleration_window.segment.channels,
            sample_start_index=acceleration_window.segment.sample_start_index,
            sample_rate_hz=acceleration_window.segment.sample_rate_hz,
            signal_type="velocity",
            unit="mm/s",
            extra_fields={
                **velocity_metadata(acceleration_window.segment, config),
                "source_run": np.asarray(run_dir.name, dtype=str),
                "source_window_start_s": np.asarray(window.analysis_start_s, dtype=np.float64),
                "source_window_end_s": np.asarray(window.analysis_end_s, dtype=np.float64),
            },
        )

    channel_rows = _channel_feature_rows(
        run_dir=run_dir,
        target_rpm=target_rpm,
        window=window,
        acceleration=acceleration_window.segment.data,
        velocity=velocity_data,
        sample_rate_hz=acceleration_window.segment.sample_rate_hz,
        channels=acceleration_window.segment.channels,
        sensor_map=sensor_map,
    )
    run_row = _run_summary_row(
        run_dir=run_dir,
        manifest=manifest,
        window=window,
        channel_rows=channel_rows,
        source_files=acceleration_window.source_files,
        velocity_path=velocity_path,
        integration_warnings=warnings,
    )
    return {"run": run_row, "channels": channel_rows, "window": window}


def load_acceleration_window(run_dir: Path, start_s: float, end_s: float) -> WindowSegment:
    acceleration_dirs = sorted(path for path in run_dir.glob("acceleration_*") if path.is_dir())
    if not acceleration_dirs:
        raise SkipRun("missing acceleration segment directory")

    selected_times: list[np.ndarray] = []
    selected_data: list[np.ndarray] = []
    source_files: list[str] = []
    sample_start_index: int | None = None
    sample_rate_hz: float | None = None
    channels: tuple[str, ...] | None = None

    for path in sorted(acceleration_dirs[0].glob("*.npz.xz")):
        segment = read_vibration_segment(path)
        if segment.time_s[-1] < start_s or segment.time_s[0] >= end_s:
            continue
        mask = (segment.time_s >= start_s) & (segment.time_s < end_s)
        if not np.any(mask):
            continue
        indices = np.flatnonzero(mask)
        if sample_start_index is None:
            sample_start_index = int(segment.sample_start_index + int(indices[0]))
            sample_rate_hz = segment.sample_rate_hz
            channels = segment.channels
        selected_times.append(segment.time_s[mask])
        selected_data.append(segment.data[:, mask])
        source_files.append(path.name)

    if not selected_data or sample_start_index is None or sample_rate_hz is None or channels is None:
        raise SkipRun(f"no acceleration samples found in window {start_s:.3f}-{end_s:.3f} s")

    time_s = np.concatenate(selected_times)
    data = np.concatenate(selected_data, axis=1)
    segment = VibrationSegment(
        path=run_dir / f"acceleration_window_{start_s:.3f}_{end_s:.3f}.npz.xz",
        time_s=time_s,
        data=data,
        channels=channels,
        sample_start_index=sample_start_index,
        sample_rate_hz=sample_rate_hz,
        signal_type="acceleration",
        unit="g",
    )
    return WindowSegment(segment=segment, source_files=tuple(source_files))


def _read_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise SkipRun("missing manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _target_rpm_from_manifest(manifest: dict[str, Any]) -> float | None:
    raw = (
        manifest.get("configuration", {})
        .get("experiment_record", {})
        .get("condition", {})
        .get("target_speed_rpm")
    )
    if raw is None or raw == "":
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def _sensor_map(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for group in manifest.get("configuration", {}).get("groups", []):
        for channel in group.get("channels", []):
            sensor = channel.get("sensor", {})
            rows[channel.get("physical_name", "")] = {
                "sensor_id": str(sensor.get("sensor_id", "")),
                "position": str(sensor.get("measurement_position", "")),
                "direction": str(sensor.get("direction", "")),
                "mounting": str(sensor.get("mounting_method", "")),
            }
    return rows


def _find_stable_window(
    telemetry: pd.DataFrame,
    *,
    target_rpm: float,
    analysis_duration_s: float,
    tolerance_rpm: float | None,
) -> StableWindow:
    required = {"time_s", "target_rpm", "actual_speed_rpm"}
    missing = required.difference(telemetry.columns)
    if missing:
        raise SkipRun(f"telemetry missing columns: {', '.join(sorted(missing))}")

    frame = telemetry.copy()
    for column in ["time_s", "target_rpm", "actual_speed_rpm", "current_a"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values("time_s")
    tolerance = tolerance_rpm if tolerance_rpm is not None else max(10.0, target_rpm * 0.002)
    stable = (
        np.isfinite(frame["time_s"])
        & np.isfinite(frame["target_rpm"])
        & np.isfinite(frame["actual_speed_rpm"])
        & (np.abs(frame["target_rpm"] - target_rpm) <= 1.0)
        & (np.abs(frame["actual_speed_rpm"] - target_rpm) <= tolerance)
    )
    stable_frame = frame.loc[stable].copy()
    if stable_frame.empty:
        raise SkipRun(f"no stable telemetry rows within +/-{tolerance:.1f} rpm of target")

    groups = (stable_frame["time_s"].diff().fillna(0.0) > 3.0).cumsum()
    blocks: list[pd.DataFrame] = [block for _, block in stable_frame.groupby(groups)]
    block = max(blocks, key=lambda item: float(item["time_s"].iloc[-1] - item["time_s"].iloc[0]))
    block_start = float(block["time_s"].iloc[0])
    block_end = float(block["time_s"].iloc[-1])
    if block_end <= block_start:
        raise SkipRun("stable telemetry block has zero duration")
    analysis_end = block_end
    analysis_start = max(block_start, analysis_end - analysis_duration_s)
    if analysis_end - analysis_start < min(analysis_duration_s, 30.0):
        raise SkipRun("stable telemetry block is too short for analysis")

    speed_ok_ratio = None
    if "speed_ok" in block.columns:
        speed_ok_ratio = float(block["speed_ok"].astype(str).str.lower().eq("true").mean())
    current_mean = None
    current_std = None
    if "current_a" in block.columns:
        current = block["current_a"].dropna()
        if not current.empty:
            current_mean = float(current.mean())
            current_std = float(current.std(ddof=0))

    return StableWindow(
        target_rpm=target_rpm,
        block_start_s=block_start,
        block_end_s=block_end,
        analysis_start_s=analysis_start,
        analysis_end_s=analysis_end,
        actual_speed_mean_rpm=float(block["actual_speed_rpm"].mean()),
        actual_speed_std_rpm=float(block["actual_speed_rpm"].std(ddof=0)),
        actual_speed_min_rpm=float(block["actual_speed_rpm"].min()),
        actual_speed_max_rpm=float(block["actual_speed_rpm"].max()),
        speed_ok_ratio=speed_ok_ratio,
        current_mean_a=current_mean,
        current_std_a=current_std,
    )


def _channel_feature_rows(
    *,
    run_dir: Path,
    target_rpm: float,
    window: StableWindow,
    acceleration: np.ndarray,
    velocity: np.ndarray,
    sample_rate_hz: float,
    channels: tuple[str, ...],
    sensor_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rotating_frequency_hz = window.actual_speed_mean_rpm / 60.0
    for index, channel in enumerate(channels):
        metadata = sensor_map.get(channel, {})
        acc_features = _signal_features(
            acceleration[index],
            sample_rate_hz=sample_rate_hz,
            unit="g",
            rotating_frequency_hz=rotating_frequency_hz,
        )
        vel_features = _signal_features(
            velocity[index],
            sample_rate_hz=sample_rate_hz,
            unit="mm/s",
            rotating_frequency_hz=rotating_frequency_hz,
        )
        row = {
            "run": run_dir.name,
            "condition_label": _condition_label(run_dir.name),
            "target_rpm": target_rpm,
            "actual_speed_mean_rpm": window.actual_speed_mean_rpm,
            "rotating_frequency_hz": rotating_frequency_hz,
            "window_start_s": window.analysis_start_s,
            "window_end_s": window.analysis_end_s,
            "duration_s": window.analysis_end_s - window.analysis_start_s,
            "channel": channel,
            "sensor_id": metadata.get("sensor_id", ""),
            "position": metadata.get("position", ""),
            "direction": metadata.get("direction", ""),
            "mounting": metadata.get("mounting", ""),
        }
        row.update({f"acc_{key}": value for key, value in acc_features.items()})
        row.update({f"vel_{key}": value for key, value in vel_features.items()})
        rows.append(row)
    return rows


def _signal_features(
    values: np.ndarray,
    *,
    sample_rate_hz: float,
    unit: str,
    rotating_frequency_hz: float,
) -> dict[str, Any]:
    clean = np.asarray(values, dtype=np.float64)
    clean = clean[np.isfinite(clean)]
    if clean.size < 4:
        return {}
    demeaned = clean - float(np.mean(clean))
    frequencies, amplitudes = _amplitude_spectrum(demeaned, sample_rate_hz)
    peak = _dominant_peak(frequencies, amplitudes, low_hz=10.0, high_hz=1000.0)
    overall_peak = _dominant_peak(frequencies, amplitudes, low_hz=0.0, high_hz=None)
    band_rows = _band_rows(frequencies, amplitudes, sample_rate_hz)
    one_x = _amplitude_near(frequencies, amplitudes, rotating_frequency_hz)
    two_x = _amplitude_near(frequencies, amplitudes, rotating_frequency_hz * 2.0)
    three_x = _amplitude_near(frequencies, amplitudes, rotating_frequency_hz * 3.0)
    harmonic_energy = _harmonic_energy_ratio(frequencies, amplitudes, rotating_frequency_hz)
    rms = float(np.sqrt(np.mean(clean**2)))
    mean_abs = float(np.mean(np.abs(clean)))
    peak_abs = float(np.max(np.abs(clean)))
    total_energy = _total_spectrum_energy(frequencies, amplitudes)
    suffix = _unit_suffix(unit)
    return {
        f"rms_{suffix}": rms,
        f"mean_abs_{suffix}": mean_abs,
        f"peak_abs_{suffix}": peak_abs,
        f"peak_to_peak_{suffix}": float(np.ptp(clean)),
        "crest_factor": float(peak_abs / max(rms, 1e-12)),
        "kurtosis": _kurtosis(clean),
        "dominant_10_1000_hz": peak["frequency_hz"],
        f"dominant_10_1000_amp_{suffix}": peak["amplitude"],
        "dominant_overall_hz": overall_peak["frequency_hz"],
        f"dominant_overall_amp_{suffix}": overall_peak["amplitude"],
        "spectral_centroid_hz": _spectral_centroid(frequencies, amplitudes),
        "spectral_bandwidth_hz": _spectral_bandwidth(frequencies, amplitudes),
        "spectral_rolloff_85_hz": _spectral_rolloff(frequencies, amplitudes, fraction=0.85),
        f"one_x_amp_{suffix}": one_x["amplitude"],
        "one_x_frequency_hz": one_x["frequency_hz"],
        f"two_x_amp_{suffix}": two_x["amplitude"],
        "two_x_frequency_hz": two_x["frequency_hz"],
        f"three_x_amp_{suffix}": three_x["amplitude"],
        "three_x_frequency_hz": three_x["frequency_hz"],
        "one_x_energy_ratio": harmonic_energy["one_x"],
        "two_x_energy_ratio": harmonic_energy["two_x"],
        "three_x_energy_ratio": harmonic_energy["three_x"],
        "one_to_three_x_energy_ratio": harmonic_energy["one_to_three_x"],
        "spectrum_energy_total": total_energy,
        **{f"band_{_band_label(row['low_hz'], row['high_hz'])}_ratio": row["energy_ratio"] for row in band_rows},
        **{f"band_{_band_label(row['low_hz'], row['high_hz'])}_energy": row["energy"] for row in band_rows},
    }


def _amplitude_spectrum(values: np.ndarray, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
    n = values.size
    window = np.hanning(n)
    frequencies = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    amplitudes = (2.0 / max(float(np.sum(window)), 1e-12)) * np.abs(np.fft.rfft(values * window))
    if amplitudes.size:
        amplitudes[0] /= 2.0
    return frequencies, amplitudes


def _dominant_peak(
    frequencies: np.ndarray,
    amplitudes: np.ndarray,
    *,
    low_hz: float,
    high_hz: float | None,
) -> dict[str, float]:
    mask = frequencies >= low_hz
    if high_hz is not None:
        mask &= frequencies <= high_hz
    if low_hz <= 0.0:
        mask &= frequencies > 0.0
    if not np.any(mask):
        return {"frequency_hz": 0.0, "amplitude": 0.0}
    masked_indices = np.flatnonzero(mask)
    local_index = int(masked_indices[np.argmax(amplitudes[masked_indices])])
    return {"frequency_hz": float(frequencies[local_index]), "amplitude": float(amplitudes[local_index])}


def _amplitude_near(
    frequencies: np.ndarray,
    amplitudes: np.ndarray,
    center_hz: float,
    *,
    half_width_hz: float = 0.5,
) -> dict[str, float]:
    if center_hz <= 0.0:
        return {"frequency_hz": 0.0, "amplitude": 0.0}
    mask = (frequencies >= center_hz - half_width_hz) & (frequencies <= center_hz + half_width_hz)
    if not np.any(mask):
        index = int(np.argmin(np.abs(frequencies - center_hz)))
        return {"frequency_hz": float(frequencies[index]), "amplitude": float(amplitudes[index])}
    indices = np.flatnonzero(mask)
    index = int(indices[np.argmax(amplitudes[indices])])
    return {"frequency_hz": float(frequencies[index]), "amplitude": float(amplitudes[index])}


def _energy_near(
    frequencies: np.ndarray,
    amplitudes: np.ndarray,
    center_hz: float,
    *,
    half_width_hz: float = 0.5,
) -> float:
    if center_hz <= 0.0:
        return 0.0
    mask = (frequencies >= center_hz - half_width_hz) & (frequencies <= center_hz + half_width_hz)
    return float(np.sum(amplitudes[mask] ** 2)) if np.any(mask) else 0.0


def _harmonic_energy_ratio(frequencies: np.ndarray, amplitudes: np.ndarray, rotating_frequency_hz: float) -> dict[str, float]:
    total = _total_spectrum_energy(frequencies, amplitudes)
    one = _energy_near(frequencies, amplitudes, rotating_frequency_hz)
    two = _energy_near(frequencies, amplitudes, rotating_frequency_hz * 2.0)
    three = _energy_near(frequencies, amplitudes, rotating_frequency_hz * 3.0)
    if total <= 0.0:
        return {"one_x": 0.0, "two_x": 0.0, "three_x": 0.0, "one_to_three_x": 0.0}
    return {
        "one_x": float(one / total),
        "two_x": float(two / total),
        "three_x": float(three / total),
        "one_to_three_x": float((one + two + three) / total),
    }


def _band_rows(frequencies: np.ndarray, amplitudes: np.ndarray, sample_rate_hz: float) -> list[dict[str, float]]:
    nyquist = sample_rate_hz / 2.0
    total_energy = _total_spectrum_energy(frequencies, amplitudes)
    rows: list[dict[str, float]] = []
    for low_hz, high_hz in ANALYSIS_BANDS_HZ:
        high = nyquist if high_hz is None else min(high_hz, nyquist)
        if low_hz >= nyquist:
            continue
        if high_hz is None:
            mask = (frequencies >= low_hz) & (frequencies <= high)
        else:
            mask = (frequencies >= low_hz) & (frequencies < high)
        energy = float(np.sum(amplitudes[mask] ** 2))
        rows.append(
            {
                "low_hz": low_hz,
                "high_hz": high,
                "energy": energy,
                "energy_ratio": float(energy / total_energy) if total_energy > 0 else 0.0,
            }
        )
    return rows


def _total_spectrum_energy(frequencies: np.ndarray, amplitudes: np.ndarray) -> float:
    return float(np.sum(amplitudes[frequencies > 0.0] ** 2))


def _spectral_centroid(frequencies: np.ndarray, amplitudes: np.ndarray) -> float:
    mask = frequencies > 0.0
    weights = amplitudes[mask] ** 2
    total = float(np.sum(weights))
    if total <= 0.0:
        return 0.0
    return float(np.sum(frequencies[mask] * weights) / total)


def _spectral_bandwidth(frequencies: np.ndarray, amplitudes: np.ndarray) -> float:
    mask = frequencies > 0.0
    weights = amplitudes[mask] ** 2
    total = float(np.sum(weights))
    if total <= 0.0:
        return 0.0
    centroid = _spectral_centroid(frequencies, amplitudes)
    return float(np.sqrt(np.sum(((frequencies[mask] - centroid) ** 2) * weights) / total))


def _spectral_rolloff(frequencies: np.ndarray, amplitudes: np.ndarray, *, fraction: float) -> float:
    mask = frequencies > 0.0
    freq = frequencies[mask]
    energy = amplitudes[mask] ** 2
    total = float(np.sum(energy))
    if total <= 0.0 or freq.size == 0:
        return 0.0
    threshold = total * fraction
    index = int(np.searchsorted(np.cumsum(energy), threshold, side="left"))
    index = min(index, freq.size - 1)
    return float(freq[index])


def _run_summary_row(
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    window: StableWindow,
    channel_rows: list[dict[str, Any]],
    source_files: tuple[str, ...],
    velocity_path: Path,
    integration_warnings: tuple[str, ...],
) -> dict[str, Any]:
    front_rows = [row for row in channel_rows if row.get("position") == "FrontBearingOuter"]
    bench_rows = [row for row in channel_rows if row.get("position") == "BenchTop"]
    acc1 = [row for row in channel_rows if row.get("sensor_id") == "ACC1"]
    acc3 = [row for row in channel_rows if row.get("sensor_id") == "ACC3"]
    return {
        "run": run_dir.name,
        "condition_label": _condition_label(run_dir.name),
        "target_rpm": window.target_rpm,
        "created_at_local": manifest.get("created_at_local", ""),
        "stable_block_start_s": window.block_start_s,
        "stable_block_end_s": window.block_end_s,
        "analysis_window_start_s": window.analysis_start_s,
        "analysis_window_end_s": window.analysis_end_s,
        "analysis_duration_s": window.analysis_end_s - window.analysis_start_s,
        "actual_speed_mean_rpm": window.actual_speed_mean_rpm,
        "actual_speed_std_rpm": window.actual_speed_std_rpm,
        "actual_speed_min_rpm": window.actual_speed_min_rpm,
        "actual_speed_max_rpm": window.actual_speed_max_rpm,
        "speed_ok_ratio": window.speed_ok_ratio,
        "current_mean_a": window.current_mean_a,
        "current_std_a": window.current_std_a,
        "source_segment_files_used": len(source_files),
        "source_files_first_last": f"{source_files[0]} .. {source_files[-1]}" if source_files else "",
        "velocity_npz_xz": str(velocity_path),
        "integration_warnings": "; ".join(integration_warnings),
        "front_velocity_rms_mm_s_mean": _mean(front_rows, "vel_rms_mm_s"),
        "front_velocity_1x_mm_s_mean": _mean(front_rows, "vel_one_x_amp_mm_s"),
        "front_velocity_harmonic_energy_ratio_mean": _mean(front_rows, "vel_one_to_three_x_energy_ratio"),
        "front_velocity_10_100_ratio_mean": _mean(front_rows, "vel_band_10_100_ratio"),
        "front_velocity_100_1000_ratio_mean": _mean(front_rows, "vel_band_100_1000_ratio"),
        "front_acceleration_rms_g_mean": _mean(front_rows, "acc_rms_g"),
        "bench_velocity_rms_mm_s_mean": _mean(bench_rows, "vel_rms_mm_s"),
        "bench_velocity_1x_mm_s_mean": _mean(bench_rows, "vel_one_x_amp_mm_s"),
        "bench_velocity_harmonic_energy_ratio_mean": _mean(bench_rows, "vel_one_to_three_x_energy_ratio"),
        "bench_acceleration_rms_g_mean": _mean(bench_rows, "acc_rms_g"),
        "front_y_velocity_rms_mm_s": _mean(acc1, "vel_rms_mm_s"),
        "bench_y_velocity_rms_mm_s": _mean(acc3, "vel_rms_mm_s"),
        "front_to_bench_velocity_rms_ratio": _ratio(_mean(front_rows, "vel_rms_mm_s"), _mean(bench_rows, "vel_rms_mm_s")),
        "max_velocity_rms_mm_s": _max(channel_rows, "vel_rms_mm_s"),
        "max_acceleration_rms_g": _max(channel_rows, "acc_rms_g"),
    }


def _aggregate_by_speed(run_df: pd.DataFrame, channel_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for target_rpm, runs in run_df.groupby("target_rpm", sort=True):
        channels = channel_df[channel_df["target_rpm"] == target_rpm]
        front = channels[channels["position"] == "FrontBearingOuter"]
        bench = channels[channels["position"] == "BenchTop"]
        rows.append(
            {
                "target_rpm": float(target_rpm),
                "run_count": int(len(runs)),
                "front_velocity_rms_mm_s_mean": _series_mean(runs["front_velocity_rms_mm_s_mean"]),
                "front_velocity_rms_mm_s_std": _series_std(runs["front_velocity_rms_mm_s_mean"]),
                "front_velocity_1x_mm_s_mean": _series_mean(runs["front_velocity_1x_mm_s_mean"]),
                "front_velocity_harmonic_energy_ratio_mean": _series_mean(
                    runs["front_velocity_harmonic_energy_ratio_mean"]
                ),
                "front_velocity_10_100_ratio_mean": _series_mean(runs["front_velocity_10_100_ratio_mean"]),
                "front_velocity_100_1000_ratio_mean": _series_mean(runs["front_velocity_100_1000_ratio_mean"]),
                "front_acceleration_rms_g_mean": _series_mean(runs["front_acceleration_rms_g_mean"]),
                "bench_velocity_rms_mm_s_mean": _series_mean(runs["bench_velocity_rms_mm_s_mean"]),
                "bench_velocity_1x_mm_s_mean": _series_mean(runs["bench_velocity_1x_mm_s_mean"]),
                "bench_velocity_harmonic_energy_ratio_mean": _series_mean(
                    runs["bench_velocity_harmonic_energy_ratio_mean"]
                ),
                "bench_acceleration_rms_g_mean": _series_mean(runs["bench_acceleration_rms_g_mean"]),
                "front_velocity_dominant_10_1000_hz_mean": _mean_records(front, "vel_dominant_10_1000_hz"),
                "bench_velocity_dominant_10_1000_hz_mean": _mean_records(bench, "vel_dominant_10_1000_hz"),
                "actual_speed_std_rpm_mean": _series_mean(runs["actual_speed_std_rpm"]),
            }
        )
    return pd.DataFrame(rows)


def _condition_rows_6000(channel_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    frame = channel_df[channel_df["run"].isin(CONDITION_LABELS)].copy()
    if frame.empty:
        return pd.DataFrame(rows)

    baseline = frame[frame["run"] == "run_20260707_6000rpm_9min"]
    baseline_by_sensor = {
        str(row["sensor_id"]): row
        for _, row in baseline.iterrows()
        if str(row.get("sensor_id", ""))
    }

    for _, row in frame.iterrows():
        sensor_id = str(row.get("sensor_id", ""))
        base = baseline_by_sensor.get(sensor_id)
        rows.append(
            {
                "condition_order": _condition_order(str(row["condition_label"])),
                "condition_label": row["condition_label"],
                "run": row["run"],
                "sensor_id": sensor_id,
                "position": row["position"],
                "direction": row["direction"],
                "target_rpm": row["target_rpm"],
                "actual_speed_mean_rpm": row["actual_speed_mean_rpm"],
                "vel_rms_mm_s": row.get("vel_rms_mm_s"),
                "vel_rms_relative_to_standard": _relative(row.get("vel_rms_mm_s"), None if base is None else base.get("vel_rms_mm_s")),
                "vel_peak_abs_mm_s": row.get("vel_peak_abs_mm_s"),
                "vel_peak_to_peak_mm_s": row.get("vel_peak_to_peak_mm_s"),
                "vel_crest_factor": row.get("vel_crest_factor"),
                "vel_dominant_10_1000_hz": row.get("vel_dominant_10_1000_hz"),
                "vel_dominant_10_1000_amp_mm_s": row.get("vel_dominant_10_1000_amp_mm_s"),
                "vel_one_x_amp_mm_s": row.get("vel_one_x_amp_mm_s"),
                "vel_one_x_relative_to_standard": _relative(
                    row.get("vel_one_x_amp_mm_s"), None if base is None else base.get("vel_one_x_amp_mm_s")
                ),
                "vel_two_x_amp_mm_s": row.get("vel_two_x_amp_mm_s"),
                "vel_three_x_amp_mm_s": row.get("vel_three_x_amp_mm_s"),
                "vel_one_to_three_x_energy_ratio": row.get("vel_one_to_three_x_energy_ratio"),
                "vel_band_10_100_ratio": row.get("vel_band_10_100_ratio"),
                "vel_band_100_1000_ratio": row.get("vel_band_100_1000_ratio"),
                "vel_spectral_centroid_hz": row.get("vel_spectral_centroid_hz"),
                "vel_spectral_rolloff_85_hz": row.get("vel_spectral_rolloff_85_hz"),
                "acc_rms_g": row.get("acc_rms_g"),
                "acc_rms_relative_to_standard": _relative(row.get("acc_rms_g"), None if base is None else base.get("acc_rms_g")),
                "front_to_bench_hint": "front" if row.get("position") == "FrontBearingOuter" else "bench",
            }
        )

    return pd.DataFrame(rows).sort_values(["condition_order", "sensor_id"]).reset_index(drop=True)


def _spectrum_energy_rows(channel_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in channel_df.iterrows():
        for signal_type, unit in (("acc", "g"), ("vel", "mm/s")):
            for low_hz, high_hz in ANALYSIS_BANDS_HZ:
                high_value = 12800.0 if high_hz is None else high_hz
                band = _band_label(low_hz, high_value)
                energy_key = f"{signal_type}_band_{band}_energy"
                ratio_key = f"{signal_type}_band_{band}_ratio"
                if energy_key not in row or ratio_key not in row:
                    continue
                rows.append(
                    {
                        "run": row["run"],
                        "condition_label": row["condition_label"],
                        "target_rpm": row["target_rpm"],
                        "actual_speed_mean_rpm": row["actual_speed_mean_rpm"],
                        "rotating_frequency_hz": row["rotating_frequency_hz"],
                        "channel": row["channel"],
                        "sensor_id": row["sensor_id"],
                        "position": row["position"],
                        "direction": row["direction"],
                        "signal_type": "acceleration" if signal_type == "acc" else "velocity",
                        "unit": unit,
                        "band_label": _band_display(low_hz, high_value),
                        "low_hz": low_hz,
                        "high_hz": high_value,
                        "energy": row[energy_key],
                        "energy_ratio": row[ratio_key],
                    }
                )
    return pd.DataFrame(rows)


def _write_tables(
    output_dir: Path,
    run_df: pd.DataFrame,
    channel_df: pd.DataFrame,
    by_speed_df: pd.DataFrame,
    condition_df: pd.DataFrame,
    spectrum_df: pd.DataFrame,
    skipped: list[dict[str, str]],
) -> None:
    run_df.to_csv(output_dir / "velocity_response_run_summary.csv", index=False, encoding="utf-8-sig")
    channel_df.to_csv(output_dir / "velocity_response_channel_features.csv", index=False, encoding="utf-8-sig")
    by_speed_df.to_csv(output_dir / "velocity_response_by_speed.csv", index=False, encoding="utf-8-sig")
    condition_df.to_csv(output_dir / "velocity_response_by_condition_6000.csv", index=False, encoding="utf-8-sig")
    spectrum_df.to_csv(output_dir / "velocity_response_spectrum_energy.csv", index=False, encoding="utf-8-sig")
    (output_dir / "skipped_runs.json").write_text(
        json.dumps(skipped, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_figures(
    *,
    data_root: Path,
    figures_dir: Path,
    run_df: pd.DataFrame,
    channel_df: pd.DataFrame,
    by_speed_df: pd.DataFrame,
    condition_df: pd.DataFrame,
    windows: dict[str, StableWindow],
    config: VelocityIntegrationConfig,
) -> list[dict[str, str]]:
    figures: list[dict[str, str]] = []
    figures.append(_plot_velocity_rms_by_speed(run_df, by_speed_df, figures_dir))
    figures.append(_plot_velocity_harmonics_by_speed(channel_df, figures_dir))
    figures.append(_plot_velocity_dominant_vs_rotating_frequency(channel_df, figures_dir))
    figures.append(_plot_velocity_band_ratio_by_speed(by_speed_df, figures_dir))

    representative = _representative_run(run_df)
    if representative is not None:
        run_name = str(representative["run"])
        window = windows[run_name]
        run_dir = data_root / run_name
        acceleration_window = load_acceleration_window(run_dir, window.analysis_start_s, window.analysis_end_s)
        velocity, _ = integrate_acceleration_to_velocity(
            acceleration_window.segment.data,
            sample_rate_hz=acceleration_window.segment.sample_rate_hz,
            input_unit=acceleration_window.segment.unit,
            config=config,
        )
        channel_index = _front_channel_index(channel_df, run_name, acceleration_window.segment.channels)
        figures.append(
            _plot_acceleration_velocity_time(
                figures_dir,
                run_name=run_name,
                time_s=acceleration_window.segment.time_s,
                acceleration=acceleration_window.segment.data[channel_index],
                velocity=velocity[channel_index],
            )
        )
        figures.append(
            _plot_acceleration_velocity_zoom_spectrum(
                figures_dir,
                run_name=run_name,
                sample_rate_hz=acceleration_window.segment.sample_rate_hz,
                acceleration=acceleration_window.segment.data[channel_index],
                velocity=velocity[channel_index],
            )
        )
        figures.append(
            _plot_acceleration_velocity_full_spectrum_energy(
                figures_dir,
                run_name=run_name,
                sample_rate_hz=acceleration_window.segment.sample_rate_hz,
                acceleration=acceleration_window.segment.data[channel_index],
                velocity=velocity[channel_index],
            )
        )
        figures.append(
            _plot_band_energy_comparison(
                figures_dir,
                run_name=run_name,
                sample_rate_hz=acceleration_window.segment.sample_rate_hz,
                acceleration=acceleration_window.segment.data[channel_index],
                velocity=velocity[channel_index],
            )
        )

    figures.append(_plot_velocity_spectrum_by_speed(data_root, windows, channel_df, figures_dir, config))
    if not condition_df.empty:
        figures.append(_plot_condition_rms_relative(condition_df, figures_dir))
        figures.append(_plot_condition_heatmap(condition_df, figures_dir))
        figures.append(_plot_condition_spectrum_overlay(data_root, windows, channel_df, figures_dir, config))

    return [figure for figure in figures if figure]


def _plot_velocity_rms_by_speed(run_df: pd.DataFrame, by_speed_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    ref = _speed_reference_run_rows(run_df)
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    if not ref.empty:
        ax.plot(ref["target_rpm"], ref["front_velocity_rms_mm_s_mean"], marker="o", label="Front bearing reference")
        ax.plot(ref["target_rpm"], ref["bench_velocity_rms_mm_s_mean"], marker="s", label="Bench top reference")
    all_6000 = run_df[run_df["target_rpm"] == 6000.0]
    if len(all_6000) > 1:
        ax.scatter(
            all_6000["target_rpm"],
            all_6000["front_velocity_rms_mm_s_mean"],
            marker="o",
            facecolors="none",
            edgecolors="#4c78a8",
            label="6000 rpm conditions",
        )
    if not by_speed_df.empty:
        ax.errorbar(
            by_speed_df["target_rpm"],
            by_speed_df["front_velocity_rms_mm_s_mean"],
            yerr=by_speed_df["front_velocity_rms_mm_s_std"],
            fmt="none",
            ecolor="#4c78a8",
            capsize=4,
            alpha=0.7,
            label="Front mean/std by speed",
        )
    ax.set_xlabel("Target speed (rpm)")
    ax.set_ylabel("Velocity RMS (mm/s, 10-1000 Hz integration)")
    ax.set_title("Velocity RMS by spindle speed")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "velocity_rms_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Velocity RMS by speed", "path": f"figures/{path.name}"}


def _plot_velocity_harmonics_by_speed(channel_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    ref = _speed_reference_channel_rows(channel_df)
    acc1 = ref[ref["sensor_id"] == "ACC1"].sort_values("target_rpm")
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    if not acc1.empty:
        ax.plot(acc1["target_rpm"], acc1["vel_one_x_amp_mm_s"], marker="o", label="1x")
        ax.plot(acc1["target_rpm"], acc1["vel_two_x_amp_mm_s"], marker="s", label="2x")
        ax.plot(acc1["target_rpm"], acc1["vel_three_x_amp_mm_s"], marker="^", label="3x")
    ax.set_xlabel("Target speed (rpm)")
    ax.set_ylabel("Velocity amplitude near harmonic (mm/s)")
    ax.set_title("ACC1 harmonic velocity response by speed")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "velocity_harmonics_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Velocity harmonics by speed", "path": f"figures/{path.name}"}


def _plot_velocity_dominant_vs_rotating_frequency(channel_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    ref = _speed_reference_channel_rows(channel_df)
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    markers = {"ACC1": "o", "ACC2": "s", "ACC3": "^"}
    for sensor_id, rows in ref.groupby("sensor_id"):
        ax.scatter(
            rows["rotating_frequency_hz"],
            rows["vel_dominant_10_1000_hz"],
            marker=markers.get(str(sensor_id), "o"),
            label=str(sensor_id),
            s=55,
        )
    if not ref.empty:
        x_min = max(0.0, float(ref["rotating_frequency_hz"].min()) * 0.8)
        x_max = float(ref["rotating_frequency_hz"].max()) * 1.2
        x = np.linspace(x_min, x_max, 100)
        ax.plot(x, x, linestyle="--", color="#666666", linewidth=1, label="1x reference")
        ax.plot(x, x * 2.0, linestyle=":", color="#888888", linewidth=1, label="2x reference")
    ax.set_xlabel("Rotating frequency rpm/60 (Hz)")
    ax.set_ylabel("Dominant velocity frequency in 10-1000 Hz (Hz)")
    ax.set_title("Does velocity dominant frequency follow spindle speed?")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "velocity_dominant_vs_rotating_frequency.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Dominant frequency vs rotating frequency", "path": f"figures/{path.name}"}


def _plot_velocity_band_ratio_by_speed(by_speed_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    ax.plot(
        by_speed_df["target_rpm"],
        by_speed_df["front_velocity_10_100_ratio_mean"],
        marker="o",
        label="10-100 Hz",
    )
    ax.plot(
        by_speed_df["target_rpm"],
        by_speed_df["front_velocity_100_1000_ratio_mean"],
        marker="s",
        label="100-1000 Hz",
    )
    ax.set_xlabel("Target speed (rpm)")
    ax.set_ylabel("Front-bearing velocity energy ratio")
    ax.set_title("Velocity energy distribution by speed")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "velocity_band_ratio_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Velocity band energy by speed", "path": f"figures/{path.name}"}


def _plot_acceleration_velocity_time(
    figures_dir: Path,
    *,
    run_name: str,
    time_s: np.ndarray,
    acceleration: np.ndarray,
    velocity: np.ndarray,
) -> dict[str, str]:
    relative_time = time_s - time_s[0]
    mask = relative_time <= 2.0
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True)
    axes[0].plot(relative_time[mask], acceleration[mask], linewidth=0.7)
    axes[0].set_ylabel("Acceleration (g)")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(relative_time[mask], velocity[mask], linewidth=0.7, color="#b35c00")
    axes[1].set_xlabel("Time from window start (s)")
    axes[1].set_ylabel("Velocity (mm/s)")
    axes[1].grid(True, alpha=0.3)
    fig.suptitle(f"Acceleration vs velocity waveform: {run_name}")
    fig.tight_layout()
    path = figures_dir / "acceleration_velocity_time.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Acceleration and velocity waveform", "path": f"figures/{path.name}"}


def _plot_acceleration_velocity_zoom_spectrum(
    figures_dir: Path,
    *,
    run_name: str,
    sample_rate_hz: float,
    acceleration: np.ndarray,
    velocity: np.ndarray,
) -> dict[str, str]:
    acc_freq, acc_amp = _amplitude_spectrum(acceleration - np.mean(acceleration), sample_rate_hz)
    vel_freq, vel_amp = _amplitude_spectrum(velocity - np.mean(velocity), sample_rate_hz)
    mask_acc = acc_freq <= 1000.0
    mask_vel = vel_freq <= 1000.0
    acc_plot_freq, acc_plot_amp = _downsample_spectrum(acc_freq[mask_acc], acc_amp[mask_acc], max_points=4000)
    vel_plot_freq, vel_plot_amp = _downsample_spectrum(vel_freq[mask_vel], vel_amp[mask_vel], max_points=4000)
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True)
    axes[0].plot(acc_plot_freq, acc_plot_amp, linewidth=0.7)
    axes[0].set_ylabel("Acceleration amp. (g)")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(vel_plot_freq, vel_plot_amp, linewidth=0.7, color="#b35c00")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel("Velocity amp. (mm/s)")
    axes[1].grid(True, alpha=0.3)
    fig.suptitle(f"0-1000 Hz spectrum comparison: {run_name}")
    fig.tight_layout()
    path = figures_dir / "acceleration_velocity_spectrum.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Acceleration and velocity spectrum", "path": f"figures/{path.name}"}


def _plot_acceleration_velocity_full_spectrum_energy(
    figures_dir: Path,
    *,
    run_name: str,
    sample_rate_hz: float,
    acceleration: np.ndarray,
    velocity: np.ndarray,
) -> dict[str, str]:
    acc_freq, acc_amp = _amplitude_spectrum(acceleration - np.mean(acceleration), sample_rate_hz)
    vel_freq, vel_amp = _amplitude_spectrum(velocity - np.mean(velocity), sample_rate_hz)
    acc_freq, acc_energy = _downsample_spectrum(acc_freq[1:], acc_amp[1:] ** 2)
    vel_freq, vel_energy = _downsample_spectrum(vel_freq[1:], vel_amp[1:] ** 2)
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.8), sharex=True)
    axes[0].plot(acc_freq, np.maximum(acc_energy, 1e-30), linewidth=0.7)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("Acceleration energy")
    axes[0].grid(True, alpha=0.3)
    axes[1].plot(vel_freq, np.maximum(vel_energy, 1e-30), linewidth=0.7, color="#b35c00")
    axes[1].set_yscale("log")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel("Velocity energy")
    axes[1].grid(True, alpha=0.3)
    for ax in axes:
        for marker in (10.0, 100.0, 1000.0, 5000.0):
            ax.axvline(marker, color="#999999", linewidth=0.7, linestyle="--", alpha=0.65)
    fig.suptitle(f"Full-spectrum energy comparison: {run_name}")
    fig.tight_layout()
    path = figures_dir / "acceleration_velocity_full_spectrum_energy.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Acceleration and velocity full-spectrum energy", "path": f"figures/{path.name}"}


def _plot_band_energy_comparison(
    figures_dir: Path,
    *,
    run_name: str,
    sample_rate_hz: float,
    acceleration: np.ndarray,
    velocity: np.ndarray,
) -> dict[str, str]:
    acc_freq, acc_amp = _amplitude_spectrum(acceleration - np.mean(acceleration), sample_rate_hz)
    vel_freq, vel_amp = _amplitude_spectrum(velocity - np.mean(velocity), sample_rate_hz)
    acc_bands = _band_rows(acc_freq, acc_amp, sample_rate_hz)
    vel_bands = _band_rows(vel_freq, vel_amp, sample_rate_hz)
    labels = [_band_display(row["low_hz"], row["high_hz"]) for row in acc_bands]
    x = np.arange(len(labels))
    width = 0.36
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    ax.bar(x - width / 2, [row["energy_ratio"] for row in acc_bands], width, label="Acceleration")
    ax.bar(x + width / 2, [row["energy_ratio"] for row in vel_bands], width, label="Velocity")
    ax.set_xticks(x, labels, rotation=20)
    ax.set_ylabel("Spectrum energy ratio")
    ax.set_title(f"Band energy comparison: {run_name}")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "acceleration_velocity_band_energy.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Acceleration and velocity band energy", "path": f"figures/{path.name}"}


def _plot_velocity_spectrum_by_speed(
    data_root: Path,
    windows: dict[str, StableWindow],
    channel_df: pd.DataFrame,
    figures_dir: Path,
    config: VelocityIntegrationConfig,
) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(10, 5.2))
    plotted = False
    for target_rpm, run_name in SPEED_REFERENCE_RUNS.items():
        if run_name not in windows:
            continue
        window = windows[run_name]
        segment = load_acceleration_window(data_root / run_name, window.analysis_start_s, window.analysis_end_s)
        velocity, _ = integrate_acceleration_to_velocity(
            segment.segment.data,
            sample_rate_hz=segment.segment.sample_rate_hz,
            input_unit=segment.segment.unit,
            config=config,
        )
        channel_index = _front_channel_index(channel_df, run_name, segment.segment.channels)
        freq, amp = _amplitude_spectrum(velocity[channel_index] - np.mean(velocity[channel_index]), segment.segment.sample_rate_hz)
        mask = freq <= 1000.0
        plot_freq, plot_amp = _downsample_spectrum(freq[mask], amp[mask], max_points=3000)
        ax.plot(plot_freq, plot_amp, linewidth=0.8, label=f"{target_rpm:g} rpm")
        plotted = True
    if not plotted:
        return {}
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Velocity amplitude (mm/s)")
    ax.set_title("ACC1 velocity spectrum by target speed")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "velocity_spectrum_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Velocity spectrum by speed", "path": f"figures/{path.name}"}


def _plot_condition_rms_relative(condition_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    pivot = condition_df.pivot_table(
        index="condition_label",
        columns="sensor_id",
        values="vel_rms_relative_to_standard",
        aggfunc="mean",
    ).reindex(CONDITION_ORDER)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    x = np.arange(len(pivot.index))
    width = 0.24
    for offset, sensor_id in enumerate(["ACC1", "ACC2", "ACC3"]):
        if sensor_id not in pivot:
            continue
        ax.bar(x + (offset - 1) * width, pivot[sensor_id], width, label=sensor_id)
    ax.axhline(1.0, color="#666666", linewidth=1, linestyle="--", label="standard")
    ax.set_xticks(x, pivot.index, rotation=25, ha="right")
    ax.set_ylabel("Velocity RMS / standard condition")
    ax.set_title("6000 rpm condition effect on velocity RMS")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / "velocity_condition_relative_change_6000.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "6000 rpm condition relative change", "path": f"figures/{path.name}"}


def _plot_condition_heatmap(condition_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    feature_rows: list[dict[str, Any]] = []
    for condition in CONDITION_ORDER:
        rows = condition_df[condition_df["condition_label"] == condition]
        if rows.empty:
            continue
        by_sensor = {str(row["sensor_id"]): row for _, row in rows.iterrows()}
        feature_rows.append(
            {
                "condition": condition,
                "ACC1 RMS": _cell(by_sensor, "ACC1", "vel_rms_relative_to_standard"),
                "ACC2 RMS": _cell(by_sensor, "ACC2", "vel_rms_relative_to_standard"),
                "ACC3 RMS": _cell(by_sensor, "ACC3", "vel_rms_relative_to_standard"),
                "ACC1 1x": _cell(by_sensor, "ACC1", "vel_one_x_relative_to_standard"),
                "ACC3 1x": _cell(by_sensor, "ACC3", "vel_one_x_relative_to_standard"),
                "ACC1 100-1000": _cell(by_sensor, "ACC1", "vel_band_100_1000_ratio"),
            }
        )
    heat = pd.DataFrame(feature_rows).set_index("condition")
    matrix = heat.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(9.5, 5.4))
    image = ax.imshow(np.nan_to_num(matrix, nan=1.0), aspect="auto", cmap="viridis")
    ax.set_xticks(np.arange(len(heat.columns)), heat.columns, rotation=25, ha="right")
    ax.set_yticks(np.arange(len(heat.index)), heat.index)
    for row_index in range(matrix.shape[0]):
        for col_index in range(matrix.shape[1]):
            value = matrix[row_index, col_index]
            text = "NA" if not math.isfinite(value) else f"{value:.2f}"
            ax.text(col_index, row_index, text, ha="center", va="center", color="white", fontsize=8)
    ax.set_title("6000 rpm condition feature heatmap")
    fig.colorbar(image, ax=ax, label="Relative value or energy ratio")
    fig.tight_layout()
    path = figures_dir / "velocity_condition_heatmap_6000.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "6000 rpm condition heatmap", "path": f"figures/{path.name}"}


def _plot_condition_spectrum_overlay(
    data_root: Path,
    windows: dict[str, StableWindow],
    channel_df: pd.DataFrame,
    figures_dir: Path,
    config: VelocityIntegrationConfig,
) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(10, 5.2))
    plotted = False
    for run_name, label in CONDITION_LABELS.items():
        if run_name not in windows:
            continue
        window = windows[run_name]
        segment = load_acceleration_window(data_root / run_name, window.analysis_start_s, window.analysis_end_s)
        velocity, _ = integrate_acceleration_to_velocity(
            segment.segment.data,
            sample_rate_hz=segment.segment.sample_rate_hz,
            input_unit=segment.segment.unit,
            config=config,
        )
        channel_index = _front_channel_index(channel_df, run_name, segment.segment.channels)
        freq, amp = _amplitude_spectrum(velocity[channel_index] - np.mean(velocity[channel_index]), segment.segment.sample_rate_hz)
        mask = freq <= 1000.0
        plot_freq, plot_amp = _downsample_spectrum(freq[mask], amp[mask], max_points=2500)
        ax.plot(plot_freq, plot_amp, linewidth=0.75, label=label)
        plotted = True
    if not plotted:
        return {}
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Velocity amplitude (mm/s)")
    ax.set_title("ACC1 velocity spectrum across 6000 rpm conditions")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path = figures_dir / "velocity_condition_spectrum_overlay_6000.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "6000 rpm condition spectrum overlay", "path": f"figures/{path.name}"}


def _representative_run(run_df: pd.DataFrame) -> pd.Series | None:
    standard = run_df[run_df["run"] == "run_20260707_6000rpm_9min"]
    if not standard.empty:
        return standard.iloc[0]
    candidates = run_df[run_df["target_rpm"] == 6000.0].copy()
    if candidates.empty:
        candidates = run_df.copy()
    if candidates.empty:
        return None
    median = float(candidates["front_velocity_rms_mm_s_mean"].median())
    candidates["_distance"] = np.abs(candidates["front_velocity_rms_mm_s_mean"] - median)
    return candidates.sort_values(["_distance", "run"]).iloc[0]


def _front_channel_index(channel_df: pd.DataFrame, run_name: str, channels: tuple[str, ...]) -> int:
    rows = channel_df[(channel_df["run"] == run_name) & (channel_df["sensor_id"] == "ACC1")]
    if rows.empty:
        return 0
    channel = str(rows.iloc[0]["channel"])
    return channels.index(channel) if channel in channels else 0


def _speed_reference_run_rows(run_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, run_name in SPEED_REFERENCE_RUNS.items():
        match = run_df[run_df["run"] == run_name]
        if not match.empty:
            rows.append(match.iloc[0])
    if not rows:
        return pd.DataFrame(columns=run_df.columns)
    return pd.DataFrame(rows).sort_values("target_rpm")


def _speed_reference_channel_rows(channel_df: pd.DataFrame) -> pd.DataFrame:
    names = set(SPEED_REFERENCE_RUNS.values())
    return channel_df[channel_df["run"].isin(names)].copy().sort_values(["target_rpm", "sensor_id"])


def _condition_label(run_name: str) -> str:
    return CONDITION_LABELS.get(run_name, f"{_target_from_run_name(run_name)} rpm")


def _condition_order(label: str) -> int:
    try:
        return CONDITION_ORDER.index(label)
    except ValueError:
        return len(CONDITION_ORDER)


def _target_from_run_name(run_name: str) -> str:
    match = re.search(r"_(\d+)rpm", run_name)
    return match.group(1) if match else run_name


def _cell(rows: dict[str, pd.Series], sensor_id: str, key: str) -> float:
    row = rows.get(sensor_id)
    if row is None:
        return float("nan")
    value = row.get(key)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return safe or "run"


def _band_name(config: VelocityIntegrationConfig) -> str:
    high = f"{config.highpass_hz:g}Hz"
    low = "nyquist" if config.lowpass_hz is None else f"{config.lowpass_hz:g}Hz"
    return f"{high}_{low}".replace(".", "p")


def _unit_suffix(unit: str) -> str:
    return "mm_s" if unit == "mm/s" else "g"


def _band_label(low_hz: float, high_hz: float | None) -> str:
    if high_hz is None:
        return f"{low_hz:g}_nyquist"
    return f"{low_hz:g}_{high_hz:g}"


def _band_display(low_hz: float, high_hz: float | None) -> str:
    high = "Nyquist" if high_hz is None else f"{high_hz:g}"
    return f"{low_hz:g}-{high} Hz"


def _downsample_spectrum(frequencies: np.ndarray, energy: np.ndarray, *, max_points: int = 6000) -> tuple[np.ndarray, np.ndarray]:
    if frequencies.size <= max_points:
        return frequencies, energy
    indices = np.linspace(0, frequencies.size - 1, max_points).astype(int)
    return frequencies[indices], energy[indices]


def _kurtosis(values: np.ndarray) -> float:
    std = float(np.std(values, ddof=0))
    if values.size < 4 or std <= 1e-12:
        return 0.0
    centered = values - float(np.mean(values))
    return float(np.mean(centered**4) / (std**4))


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if not math.isfinite(float(numerator)) or not math.isfinite(float(denominator)) or abs(float(denominator)) <= 1e-12:
        return None
    return float(numerator) / float(denominator)


def _relative(value: Any, baseline: Any) -> float | None:
    try:
        value_float = float(value)
        baseline_float = float(baseline)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value_float) or not math.isfinite(baseline_float) or abs(baseline_float) <= 1e-12:
        return None
    return float(value_float / baseline_float)


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if key in row and row[key] is not None and math.isfinite(float(row[key]))]
    return float(np.mean(values)) if values else None


def _max(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if key in row and row[key] is not None and math.isfinite(float(row[key]))]
    return float(np.max(values)) if values else None


def _series_mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def _series_std(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < 2:
        return None
    return float(values.std(ddof=1))


def _mean_records(frame: pd.DataFrame, key: str) -> float | None:
    if frame.empty or key not in frame:
        return None
    values = pd.to_numeric(frame[key], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def _records_json_safe(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [_json_safe(record) for record in frame.to_dict(orient="records")]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_json_safe(inner) for inner in value]
    if isinstance(value, tuple):
        return [_json_safe(inner) for inner in value]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value
