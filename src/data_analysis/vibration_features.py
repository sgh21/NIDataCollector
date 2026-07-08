from __future__ import annotations

from typing import Any

import numpy as np
from scipy import signal, stats

from data_analysis.vibration_io import VibrationSegment


DEFAULT_BANDS_HZ = (
    (0.0, 10.0),
    (10.0, 100.0),
    (100.0, 1000.0),
    (1000.0, 5000.0),
)


def analyze_segment(
    segment: VibrationSegment,
    *,
    rpm: float | None = None,
    top_peaks: int = 10,
) -> dict[str, Any]:
    rotating_frequency_hz = None if rpm is None else float(rpm) / 60.0
    channels = [
        analyze_channel(
            channel=channel,
            time_s=segment.time_s,
            values=segment.data[index],
            sample_rate_hz=segment.sample_rate_hz,
            unit=segment.unit,
            rpm=rpm,
            top_peaks=top_peaks,
        )
        for index, channel in enumerate(segment.channels)
    ]
    return {
        "input": {
            "path": str(segment.path),
            "signal_type": segment.signal_type,
            "unit": segment.unit,
            "sample_rate_hz": segment.sample_rate_hz,
            "sample_start_index": segment.sample_start_index,
        },
        "selected_channels": list(segment.channels),
        "rpm": None if rpm is None else float(rpm),
        "rotating_frequency_hz": rotating_frequency_hz,
        "warnings": list(segment.warnings),
        "channels": channels,
    }


def analyze_channel(
    *,
    channel: str,
    time_s: np.ndarray,
    values: np.ndarray,
    sample_rate_hz: float,
    unit: str,
    rpm: float | None,
    top_peaks: int,
) -> dict[str, Any]:
    clean_values, warnings = _finite_values(np.asarray(values, dtype=float))
    if clean_values.size < 4:
        return {
            "channel": channel,
            "basic": _basic_info(channel, time_s, values, sample_rate_hz, unit),
            "time_domain": {},
            "frequency_domain": {},
            "envelope": {},
            "warnings": warnings + ["signal is too short for reliable analysis"],
            "analysis_notes": ["Signal is too short for reliable FFT, PSD, or envelope analysis."],
        }

    demeaned = clean_values - float(np.mean(clean_values))
    time_features = _time_domain_features(clean_values, sample_rate_hz)
    frequency_features = _frequency_features(demeaned, sample_rate_hz, rpm, top_peaks)
    envelope_features = _envelope_features(demeaned, sample_rate_hz, rpm, top_peaks)
    notes = _analysis_notes(time_features, frequency_features, clean_values)

    return {
        "channel": channel,
        "basic": _basic_info(channel, time_s, values, sample_rate_hz, unit),
        "time_domain": time_features,
        "frequency_domain": frequency_features,
        "envelope": envelope_features,
        "warnings": warnings,
        "analysis_notes": notes,
    }


def _finite_values(values: np.ndarray) -> tuple[np.ndarray, list[str]]:
    mask = np.isfinite(values)
    finite_ratio = float(np.count_nonzero(mask) / values.size) if values.size else 0.0
    warnings: list[str] = []
    if finite_ratio < 1.0:
        warnings.append(f"non-finite values removed; finite ratio={finite_ratio:.6f}")
    if finite_ratio < 0.5:
        return np.asarray([], dtype=float), warnings + ["finite-value ratio below 0.5"]
    return values[mask].astype(float), warnings


def _basic_info(channel: str, time_s: np.ndarray, values: np.ndarray, sample_rate_hz: float, unit: str) -> dict[str, Any]:
    finite_ratio = float(np.count_nonzero(np.isfinite(values)) / values.size) if values.size else 0.0
    finite_values = values[np.isfinite(values)]
    near_constant = bool(
        finite_values.size > 0
        and np.nanstd(finite_values) <= max(1e-12, abs(float(np.nanmean(finite_values))) * 1e-9)
    )
    return {
        "channel": channel,
        "sample_rate_hz": float(sample_rate_hz),
        "sample_count": int(values.size),
        "duration_s": float(time_s[-1] - time_s[0]) if len(time_s) > 1 else 0.0,
        "time_start_s": float(time_s[0]) if len(time_s) else None,
        "time_end_s": float(time_s[-1]) if len(time_s) else None,
        "unit": unit,
        "finite_ratio": finite_ratio,
        "near_constant": near_constant,
    }


def _time_domain_features(values: np.ndarray, sample_rate_hz: float) -> dict[str, float]:
    abs_values = np.abs(values)
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=0))
    rms = float(np.sqrt(np.mean(values**2)))
    peak_abs = float(np.max(abs_values))
    absolute_mean = float(np.mean(abs_values))
    sqrt_abs_mean = float(np.mean(np.sqrt(abs_values)))
    eps = 1e-12
    signs = np.signbit(values)
    zero_crossings = int(np.count_nonzero(signs[1:] != signs[:-1])) if values.size > 1 else 0
    return {
        "mean": mean,
        "std": std,
        "rms": rms,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "peak_abs": peak_abs,
        "peak_to_peak": float(np.ptp(values)),
        "absolute_mean": absolute_mean,
        "energy": float(np.sum(values**2)),
        "crest_factor": float(peak_abs / max(rms, eps)),
        "impulse_factor": float(peak_abs / max(absolute_mean, eps)),
        "shape_factor": float(rms / max(absolute_mean, eps)),
        "clearance_factor": float(peak_abs / max(sqrt_abs_mean**2, eps)),
        "skewness": float(stats.skew(values, bias=False)) if values.size > 2 else 0.0,
        "kurtosis": float(stats.kurtosis(values, fisher=False, bias=False)) if values.size > 3 else 0.0,
        "zero_crossing_rate_hz": float(zero_crossings * sample_rate_hz / max(values.size - 1, 1)),
    }


def _frequency_features(values: np.ndarray, sample_rate_hz: float, rpm: float | None, top_peaks: int) -> dict[str, Any]:
    n = values.size
    windowed = values * np.hanning(n)
    frequencies = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    amplitudes = (2.0 / max(np.sum(np.hanning(n)), 1e-12)) * np.abs(np.fft.rfft(windowed))
    if amplitudes.size:
        amplitudes[0] = amplitudes[0] / 2.0
    dominant_frequency_hz = 0.0
    if amplitudes.size:
        if amplitudes.size > 1 and np.any(amplitudes[1:] > 0):
            dominant_index = int(np.argmax(amplitudes[1:]) + 1)
        else:
            dominant_index = int(np.argmax(amplitudes))
        dominant_frequency_hz = float(frequencies[dominant_index])
    peak_rows = _top_peaks(frequencies, amplitudes, top_peaks, rpm)
    psd_freq, psd_values = signal.welch(values, fs=sample_rate_hz, nperseg=min(4096, n))
    power_sum = float(np.sum(amplitudes))
    centroid = float(np.sum(frequencies * amplitudes) / power_sum) if power_sum > 0 else 0.0
    bandwidth = (
        float(np.sqrt(np.sum(((frequencies - centroid) ** 2) * amplitudes) / power_sum))
        if power_sum > 0
        else 0.0
    )
    cumulative = np.cumsum(amplitudes)
    rolloff_index = int(np.searchsorted(cumulative, 0.85 * cumulative[-1])) if cumulative.size and cumulative[-1] > 0 else 0
    return {
        "dominant_frequency_hz": dominant_frequency_hz,
        "spectral_peaks": peak_rows,
        "spectral_centroid_hz": centroid,
        "spectral_bandwidth_hz": bandwidth,
        "spectral_rolloff_hz": float(frequencies[min(rolloff_index, len(frequencies) - 1)]) if frequencies.size else 0.0,
        "bands": _band_features(frequencies, amplitudes, sample_rate_hz),
        "_spectrum": {"frequency_hz": frequencies.tolist(), "amplitude": amplitudes.tolist()},
        "_psd": {"frequency_hz": psd_freq.tolist(), "power": psd_values.tolist()},
    }


def _envelope_features(values: np.ndarray, sample_rate_hz: float, rpm: float | None, top_peaks: int) -> dict[str, Any]:
    analytic = signal.hilbert(values)
    envelope = np.abs(analytic)
    envelope_demeaned = envelope - float(np.mean(envelope))
    n = envelope_demeaned.size
    frequencies = np.fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    amplitudes = (2.0 / max(n, 1)) * np.abs(np.fft.rfft(envelope_demeaned))
    peaks = _top_peaks(frequencies, amplitudes, top_peaks, rpm)
    return {
        "rms": float(np.sqrt(np.mean(envelope**2))),
        "kurtosis": float(stats.kurtosis(envelope, fisher=False, bias=False)) if envelope.size > 3 else 0.0,
        "spectral_peaks": peaks,
        "_spectrum": {"frequency_hz": frequencies.tolist(), "amplitude": amplitudes.tolist()},
    }


def _top_peaks(frequencies: np.ndarray, amplitudes: np.ndarray, top_peaks: int, rpm: float | None) -> list[dict[str, float]]:
    if frequencies.size < 2 or amplitudes.size < 2:
        return []
    peak_indices, _ = signal.find_peaks(amplitudes)
    if peak_indices.size == 0:
        peak_indices = np.array([int(np.argmax(amplitudes[1:]) + 1)])
    ordered = peak_indices[np.argsort(amplitudes[peak_indices])[::-1]][:top_peaks]
    return [_with_order(float(frequencies[index]), float(amplitudes[index]), rpm) for index in ordered]


def _with_order(frequency_hz: float, amplitude: float, rpm: float | None) -> dict[str, float]:
    row = {"frequency_hz": frequency_hz, "amplitude": amplitude}
    if rpm is not None and rpm > 0:
        row["order"] = frequency_hz / (rpm / 60.0)
    return row


def _band_features(frequencies: np.ndarray, amplitudes: np.ndarray, sample_rate_hz: float) -> list[dict[str, float]]:
    nyquist = sample_rate_hz / 2.0
    edges = list(DEFAULT_BANDS_HZ) + [(5000.0, nyquist)]
    rows: list[dict[str, float]] = []
    total_energy = float(np.sum(amplitudes**2))
    for low, high in edges:
        clipped_low = max(0.0, low)
        clipped_high = min(high, nyquist)
        if clipped_high <= clipped_low:
            continue
        is_final_band = np.isclose(clipped_high, nyquist)
        if is_final_band:
            mask = (frequencies >= clipped_low) & (frequencies <= clipped_high)
        else:
            mask = (frequencies >= clipped_low) & (frequencies < clipped_high)
        energy = float(np.sum(amplitudes[mask] ** 2))
        rows.append(
            {
                "low_hz": clipped_low,
                "high_hz": clipped_high,
                "energy": energy,
                "rms": float(np.sqrt(np.mean(amplitudes[mask] ** 2))) if np.any(mask) else 0.0,
                "energy_ratio": float(energy / total_energy) if total_energy > 0 else 0.0,
            }
        )
    return rows


def _analysis_notes(time_features: dict[str, float], frequency_features: dict[str, Any], values: np.ndarray) -> list[str]:
    notes: list[str] = []
    if time_features.get("crest_factor", 0.0) >= 5.0:
        notes.append("High crest factor suggests impulsive content.")
    if time_features.get("kurtosis", 0.0) >= 5.0:
        notes.append("High kurtosis suggests a sharp distribution or outlier impacts.")
    bands = frequency_features.get("bands", [])
    if bands:
        strongest = max(bands, key=lambda row: row["energy_ratio"])
        if strongest["energy_ratio"] >= 0.6:
            notes.append(
                f"Energy is concentrated in {strongest['low_hz']:.1f}-{strongest['high_hz']:.1f} Hz "
                f"with ratio {strongest['energy_ratio']:.3f}."
            )
    peaks = frequency_features.get("spectral_peaks", [])
    if len(peaks) >= 2 and peaks[0]["amplitude"] > 3.0 * max(peaks[1]["amplitude"], 1e-12):
        notes.append("The spectrum contains a dominant peak.")
    if float(np.std(values)) <= 1e-12:
        notes.append("The signal is near constant.")
    return notes
