from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from data_analysis.speed_velocity_response import (
    SPEED_REFERENCE_RUNS,
    SkipRun,
    _amplitude_near,
    _amplitude_spectrum,
    _band_label,
    _band_rows,
    _dominant_peak,
    _find_stable_window,
    _read_manifest,
    _safe_name,
    _sensor_map,
    load_acceleration_window,
)
from data_analysis.vibration_io import VibrationPayloadError, write_vibration_segment
from data_analysis.vibration_velocity import (
    VelocityIntegrationConfig,
    integrate_acceleration_to_velocity,
    integrate_velocity_to_displacement,
)


def analyze_displacement_response(
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
    segment_dir = output_dir / "displacement_segments"
    figures_dir.mkdir(parents=True, exist_ok=True)
    segment_dir.mkdir(parents=True, exist_ok=True)

    config = VelocityIntegrationConfig(highpass_hz=highpass_hz, lowpass_hz=lowpass_hz)
    channel_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    spectra: dict[tuple[str, float], dict[str, np.ndarray | float | str]] = {}

    for target_rpm, run_name in SPEED_REFERENCE_RUNS.items():
        run_dir = data_root / run_name
        if not run_dir.exists():
            skipped.append({"run": run_name, "reason": "missing run directory"})
            continue
        try:
            result = _analyze_reference_run(
                run_dir=run_dir,
                target_rpm=target_rpm,
                segment_dir=segment_dir,
                analysis_duration_s=analysis_duration_s,
                config=config,
                stable_tolerance_rpm=stable_tolerance_rpm,
            )
        except SkipRun as exc:
            skipped.append({"run": run_name, "reason": str(exc)})
            continue

        run_rows.append(result["run"])
        channel_rows.extend(result["channels"])
        spectra.update(result["spectra"])

    if not channel_rows:
        raise VibrationPayloadError("no speed-reference runs could be analyzed for displacement")

    channel_df = pd.DataFrame(channel_rows)
    run_df = pd.DataFrame(run_rows)
    order_df = _order_peak_rows(channel_df)
    _write_tables(output_dir, run_df, channel_df, order_df, skipped)
    figures = _write_figures(figures_dir, channel_df, spectra)

    analysis = {
        "method": {
            "source": "acceleration -> velocity -> displacement",
            "velocity_formula": "V(f)=A(f)/(j*2*pi*f)",
            "displacement_formula": "D(f)=V(f)/(j*2*pi*f)",
            "displacement_unit": "mm",
            "reported_plot_unit": "um",
            "highpass_hz": highpass_hz,
            "lowpass_hz": lowpass_hz,
            "analysis_duration_s": analysis_duration_s,
            "stable_window_policy": "last analysis_duration_s seconds of the stable target-speed block",
            "order_axis": "order = frequency_hz / (rpm / 60)",
        },
        "runs": _records_json_safe(run_df),
        "channels": _records_json_safe(channel_df),
        "order_peaks": _records_json_safe(order_df),
        "skipped": skipped,
        "figures": figures,
    }
    (output_dir / "displacement_response_analysis.json").write_text(
        json.dumps(_json_safe(analysis), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return analysis


def _analyze_reference_run(
    *,
    run_dir: Path,
    target_rpm: float,
    segment_dir: Path,
    analysis_duration_s: float,
    config: VelocityIntegrationConfig,
    stable_tolerance_rpm: float | None,
) -> dict[str, Any]:
    manifest = _read_manifest(run_dir)
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
    acceleration_window = load_acceleration_window(run_dir, window.analysis_start_s, window.analysis_end_s)
    velocity, velocity_warnings = integrate_acceleration_to_velocity(
        acceleration_window.segment.data,
        sample_rate_hz=acceleration_window.segment.sample_rate_hz,
        input_unit=acceleration_window.segment.unit,
        config=config,
    )
    displacement, displacement_warnings = integrate_velocity_to_displacement(
        velocity,
        sample_rate_hz=acceleration_window.segment.sample_rate_hz,
        input_unit="mm/s",
        config=config,
    )
    displacement_path = segment_dir / f"{_safe_name(run_dir.name)}_displacement_10Hz_1000Hz_last{int(analysis_duration_s)}s.npz.xz"
    if not displacement_path.exists():
        write_vibration_segment(
            displacement_path,
            time_s=acceleration_window.segment.time_s,
            data=displacement,
            channels=acceleration_window.segment.channels,
            sample_start_index=acceleration_window.segment.sample_start_index,
            sample_rate_hz=acceleration_window.segment.sample_rate_hz,
            signal_type="displacement",
            unit="mm",
            extra_fields={
                "source_run": np.asarray(run_dir.name, dtype=str),
                "source_signal_type": np.asarray(acceleration_window.segment.signal_type, dtype=str),
                "source_unit": np.asarray(acceleration_window.segment.unit, dtype=str),
                "conversion_method": np.asarray("frequency_domain_double_integration", dtype=str),
                "conversion_highpass_hz": np.asarray(float(config.highpass_hz), dtype=np.float64),
                "conversion_lowpass_hz": np.asarray(np.nan if config.lowpass_hz is None else float(config.lowpass_hz)),
                "source_window_start_s": np.asarray(window.analysis_start_s, dtype=np.float64),
                "source_window_end_s": np.asarray(window.analysis_end_s, dtype=np.float64),
            },
        )

    sensor_map = _sensor_map(manifest)
    rotating_frequency_hz = window.actual_speed_mean_rpm / 60.0
    channel_rows: list[dict[str, Any]] = []
    spectra: dict[tuple[str, float], dict[str, np.ndarray | float | str]] = {}
    for index, channel in enumerate(acceleration_window.segment.channels):
        metadata = sensor_map.get(channel, {})
        features = _displacement_features(
            displacement[index],
            sample_rate_hz=acceleration_window.segment.sample_rate_hz,
            rotating_frequency_hz=rotating_frequency_hz,
        )
        sensor_id = metadata.get("sensor_id", "") or channel
        row = {
            "run": run_dir.name,
            "target_rpm": target_rpm,
            "actual_speed_mean_rpm": window.actual_speed_mean_rpm,
            "rotating_frequency_hz": rotating_frequency_hz,
            "window_start_s": window.analysis_start_s,
            "window_end_s": window.analysis_end_s,
            "duration_s": window.analysis_end_s - window.analysis_start_s,
            "channel": channel,
            "sensor_id": sensor_id,
            "position": metadata.get("position", ""),
            "direction": metadata.get("direction", ""),
            "displacement_npz_xz": str(displacement_path),
            "integration_warnings": "; ".join((*velocity_warnings, *displacement_warnings)),
        }
        row.update(features)
        channel_rows.append(row)

        freq, amp = _amplitude_spectrum(displacement[index] - float(np.mean(displacement[index])), acceleration_window.segment.sample_rate_hz)
        spectra[(str(sensor_id), float(target_rpm))] = {
            "frequency_hz": freq,
            "order": freq / rotating_frequency_hz,
            "amplitude_um": amp * 1000.0,
            "sensor_id": str(sensor_id),
            "target_rpm": float(target_rpm),
        }

    return {
        "run": {
            "run": run_dir.name,
            "target_rpm": target_rpm,
            "actual_speed_mean_rpm": window.actual_speed_mean_rpm,
            "actual_speed_std_rpm": window.actual_speed_std_rpm,
            "window_start_s": window.analysis_start_s,
            "window_end_s": window.analysis_end_s,
            "source_segment_files_used": len(acceleration_window.source_files),
            "source_files_first_last": f"{acceleration_window.source_files[0]} .. {acceleration_window.source_files[-1]}",
            "displacement_npz_xz": str(displacement_path),
        },
        "channels": channel_rows,
        "spectra": spectra,
    }


def _displacement_features(
    values_mm: np.ndarray,
    *,
    sample_rate_hz: float,
    rotating_frequency_hz: float,
) -> dict[str, Any]:
    clean = np.asarray(values_mm, dtype=np.float64)
    clean = clean[np.isfinite(clean)]
    if clean.size < 4:
        return {}
    demeaned = clean - float(np.mean(clean))
    freq, amp_mm = _amplitude_spectrum(demeaned, sample_rate_hz)
    peak = _dominant_peak(freq, amp_mm, low_hz=10.0, high_hz=1000.0)
    one_x = _amplitude_near(freq, amp_mm, rotating_frequency_hz)
    two_x = _amplitude_near(freq, amp_mm, rotating_frequency_hz * 2.0)
    three_x = _amplitude_near(freq, amp_mm, rotating_frequency_hz * 3.0)
    bands = _band_rows(freq, amp_mm, sample_rate_hz)
    rms_mm = float(np.sqrt(np.mean(clean**2)))
    peak_abs_mm = float(np.max(np.abs(clean)))
    dominant_hz = float(peak["frequency_hz"])
    return {
        "disp_rms_mm": rms_mm,
        "disp_rms_um": rms_mm * 1000.0,
        "disp_peak_abs_mm": peak_abs_mm,
        "disp_peak_abs_um": peak_abs_mm * 1000.0,
        "disp_peak_to_peak_mm": float(np.ptp(clean)),
        "disp_peak_to_peak_um": float(np.ptp(clean) * 1000.0),
        "disp_dominant_10_1000_hz": dominant_hz,
        "disp_dominant_order": dominant_hz / rotating_frequency_hz if rotating_frequency_hz > 0.0 else None,
        "disp_dominant_amp_mm": float(peak["amplitude"]),
        "disp_dominant_amp_um": float(peak["amplitude"] * 1000.0),
        "disp_one_x_amp_mm": float(one_x["amplitude"]),
        "disp_one_x_amp_um": float(one_x["amplitude"] * 1000.0),
        "disp_two_x_amp_um": float(two_x["amplitude"] * 1000.0),
        "disp_three_x_amp_um": float(three_x["amplitude"] * 1000.0),
        "disp_one_x_frequency_hz": float(one_x["frequency_hz"]),
        "disp_two_x_frequency_hz": float(two_x["frequency_hz"]),
        "disp_three_x_frequency_hz": float(three_x["frequency_hz"]),
        **{f"disp_band_{_band_label(row['low_hz'], row['high_hz'])}_ratio": row["energy_ratio"] for row in bands},
    }


def _order_peak_rows(channel_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in channel_df.iterrows():
        rows.append(
            {
                "target_rpm": row["target_rpm"],
                "sensor_id": row["sensor_id"],
                "dominant_frequency_hz": row["disp_dominant_10_1000_hz"],
                "dominant_order": row["disp_dominant_order"],
                "dominant_amplitude_um": row["disp_dominant_amp_um"],
                "one_x_amplitude_um": row["disp_one_x_amp_um"],
            }
        )
    return pd.DataFrame(rows).sort_values(["sensor_id", "target_rpm"])


def _write_tables(
    output_dir: Path,
    run_df: pd.DataFrame,
    channel_df: pd.DataFrame,
    order_df: pd.DataFrame,
    skipped: list[dict[str, str]],
) -> None:
    run_df.to_csv(output_dir / "displacement_response_run_summary.csv", index=False, encoding="utf-8-sig")
    channel_df.to_csv(output_dir / "displacement_response_channel_features.csv", index=False, encoding="utf-8-sig")
    order_df.to_csv(output_dir / "displacement_response_order_peaks.csv", index=False, encoding="utf-8-sig")
    (output_dir / "skipped_runs.json").write_text(json.dumps(skipped, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_figures(
    figures_dir: Path,
    channel_df: pd.DataFrame,
    spectra: dict[tuple[str, float], dict[str, np.ndarray | float | str]],
) -> list[dict[str, str]]:
    generated: list[dict[str, str]] = []
    generated.append(_plot_dominant_frequency(channel_df, figures_dir))
    generated.append(_plot_dominant_order(channel_df, figures_dir))
    for sensor_id in sorted(channel_df["sensor_id"].astype(str).unique()):
        spectrum = _plot_spectrum_by_speed(sensor_id, spectra, figures_dir)
        order_spectrum = _plot_order_spectrum_by_speed(sensor_id, spectra, figures_dir)
        if spectrum:
            generated.append(spectrum)
        if order_spectrum:
            generated.append(order_spectrum)
    return [item for item in generated if item]


def _plot_dominant_frequency(channel_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    for sensor_id, rows in channel_df.groupby("sensor_id", sort=True):
        rows = rows.sort_values("target_rpm")
        ax.plot(rows["target_rpm"], rows["disp_dominant_10_1000_hz"], marker="o", label=str(sensor_id))
    rpm = np.array(sorted(channel_df["target_rpm"].unique()), dtype=float)
    for order, style in [(1, "--"), (2, ":"), (3, "-.")]:
        ax.plot(rpm, rpm / 60.0 * order, linestyle=style, color="#777777", linewidth=1, label=f"{order}x")
    ax.set_xlabel("Target speed (rpm)")
    ax.set_ylabel("Dominant displacement frequency (Hz)")
    ax.set_title("Displacement dominant frequency by speed")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "displacement_dominant_frequency_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Displacement dominant frequency by speed", "path": f"figures/{path.name}"}


def _plot_dominant_order(channel_df: pd.DataFrame, figures_dir: Path) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    for sensor_id, rows in channel_df.groupby("sensor_id", sort=True):
        rows = rows.sort_values("target_rpm")
        ax.plot(rows["target_rpm"], rows["disp_dominant_order"], marker="o", label=str(sensor_id))
    for order in (1, 2, 3):
        ax.axhline(order, linestyle="--", color="#888888", linewidth=0.9)
    ax.set_xlabel("Target speed (rpm)")
    ax.set_ylabel("Dominant order")
    ax.set_title("Displacement dominant order by speed")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "displacement_dominant_order_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": "Displacement dominant order by speed", "path": f"figures/{path.name}"}


def _plot_spectrum_by_speed(
    sensor_id: str,
    spectra: dict[tuple[str, float], dict[str, np.ndarray | float | str]],
    figures_dir: Path,
) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(10, 5.2))
    plotted = False
    for rpm in sorted({key[1] for key in spectra if key[0] == sensor_id}):
        spectrum = spectra[(sensor_id, rpm)]
        freq = np.asarray(spectrum["frequency_hz"], dtype=float)
        amp = np.asarray(spectrum["amplitude_um"], dtype=float)
        mask = freq <= 1000.0
        ax.plot(freq[mask], amp[mask], linewidth=0.8, label=f"{rpm:g} rpm")
        plotted = True
    if not plotted:
        plt.close(fig)
        return {}
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Displacement amplitude (um)")
    ax.set_title(f"{sensor_id} displacement spectrum by target speed")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / f"{_safe_name(sensor_id)}_displacement_spectrum_by_speed.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": f"{sensor_id} displacement spectrum by speed", "path": f"figures/{path.name}"}


def _plot_order_spectrum_by_speed(
    sensor_id: str,
    spectra: dict[tuple[str, float], dict[str, np.ndarray | float | str]],
    figures_dir: Path,
) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(10, 5.2))
    plotted = False
    for rpm in sorted({key[1] for key in spectra if key[0] == sensor_id}):
        spectrum = spectra[(sensor_id, rpm)]
        order = np.asarray(spectrum["order"], dtype=float)
        amp = np.asarray(spectrum["amplitude_um"], dtype=float)
        mask = (order >= 0.0) & (order <= 10.0)
        ax.plot(order[mask], amp[mask], linewidth=0.8, label=f"{rpm:g} rpm")
        plotted = True
    if not plotted:
        plt.close(fig)
        return {}
    for marker in (1, 2, 3):
        ax.axvline(marker, color="#999999", linewidth=0.8, linestyle="--", alpha=0.7)
    ax.set_xlabel("Order = frequency / (rpm / 60)")
    ax.set_ylabel("Displacement amplitude (um)")
    ax.set_title(f"{sensor_id} displacement order spectrum")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    path = figures_dir / f"{_safe_name(sensor_id)}_displacement_order_spectrum.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return {"label": f"{sensor_id} displacement order spectrum", "path": f"figures/{path.name}"}


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
