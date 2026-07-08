from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from data_analysis.vibration_io import VibrationPayloadError, VibrationSegment


STANDARD_GRAVITY_MM_PER_S2 = 9806.65


@dataclass(frozen=True)
class VelocityIntegrationConfig:
    highpass_hz: float = 10.0
    lowpass_hz: float | None = 1000.0
    highpass_transition_hz: float | None = None
    lowpass_transition_hz: float | None = None


def integrate_acceleration_to_velocity(
    acceleration: np.ndarray,
    *,
    sample_rate_hz: float,
    input_unit: str,
    config: VelocityIntegrationConfig | None = None,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Convert acceleration samples to stable vibration velocity in mm/s.

    The integration is performed in the frequency domain:
    V(f) = A(f) / (j * 2*pi*f). DC and very-low-frequency content are removed
    with a smooth high-pass taper so bias and sensor drift are not integrated.
    """

    config = config or VelocityIntegrationConfig()
    _validate_config(config, sample_rate_hz)
    acceleration = np.asarray(acceleration, dtype=np.float64)
    if acceleration.ndim != 2:
        raise VibrationPayloadError(
            f"acceleration must be 2D with shape (channel_count, sample_count), got {acceleration.shape}"
        )
    if acceleration.shape[1] < 4:
        raise VibrationPayloadError("at least 4 samples are required for velocity integration")

    acceleration_mm_s2 = _to_mm_per_s2(acceleration, input_unit)
    clean, warnings = _interpolate_nonfinite(acceleration_mm_s2)
    clean = clean - np.mean(clean, axis=1, keepdims=True)

    sample_count = clean.shape[1]
    frequencies = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    spectrum = np.fft.rfft(clean, axis=1)
    weights = _integration_weights(frequencies, config)

    velocity_spectrum = np.zeros_like(spectrum, dtype=np.complex128)
    nonzero = frequencies > 0.0
    velocity_spectrum[:, nonzero] = spectrum[:, nonzero] / (1j * 2.0 * np.pi * frequencies[nonzero])
    velocity_spectrum *= weights[None, :]

    velocity = np.fft.irfft(velocity_spectrum, n=sample_count, axis=1)
    velocity = velocity - np.mean(velocity, axis=1, keepdims=True)
    return velocity.astype(np.float64), tuple(warnings)


def integrate_velocity_to_displacement(
    velocity: np.ndarray,
    *,
    sample_rate_hz: float,
    input_unit: str,
    config: VelocityIntegrationConfig | None = None,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """Convert velocity samples to stable displacement in mm."""

    config = config or VelocityIntegrationConfig()
    _validate_config(config, sample_rate_hz)
    velocity = np.asarray(velocity, dtype=np.float64)
    if velocity.ndim != 2:
        raise VibrationPayloadError(f"velocity must be 2D with shape (channel_count, sample_count), got {velocity.shape}")
    if velocity.shape[1] < 4:
        raise VibrationPayloadError("at least 4 samples are required for displacement integration")

    velocity_mm_s = _to_mm_per_s(velocity, input_unit)
    clean, warnings = _interpolate_nonfinite(velocity_mm_s)
    clean = clean - np.mean(clean, axis=1, keepdims=True)

    sample_count = clean.shape[1]
    frequencies = np.fft.rfftfreq(sample_count, d=1.0 / sample_rate_hz)
    spectrum = np.fft.rfft(clean, axis=1)
    weights = _integration_weights(frequencies, config)

    displacement_spectrum = np.zeros_like(spectrum, dtype=np.complex128)
    nonzero = frequencies > 0.0
    displacement_spectrum[:, nonzero] = spectrum[:, nonzero] / (1j * 2.0 * np.pi * frequencies[nonzero])
    displacement_spectrum *= weights[None, :]

    displacement = np.fft.irfft(displacement_spectrum, n=sample_count, axis=1)
    displacement = displacement - np.mean(displacement, axis=1, keepdims=True)
    return displacement.astype(np.float64), tuple(warnings)


def velocity_metadata(source: VibrationSegment, config: VelocityIntegrationConfig) -> dict[str, Any]:
    return {
        "source_path": np.asarray(str(source.path), dtype=str),
        "source_signal_type": np.asarray(source.signal_type, dtype=str),
        "source_unit": np.asarray(source.unit, dtype=str),
        "conversion_method": np.asarray("frequency_domain_integration", dtype=str),
        "conversion_formula": np.asarray("V(f)=A(f)/(j*2*pi*f)", dtype=str),
        "conversion_highpass_hz": np.asarray(float(config.highpass_hz), dtype=np.float64),
        "conversion_lowpass_hz": np.asarray(np.nan if config.lowpass_hz is None else float(config.lowpass_hz)),
        "conversion_output_unit": np.asarray("mm/s", dtype=str),
    }


def _validate_config(config: VelocityIntegrationConfig, sample_rate_hz: float) -> None:
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        raise VibrationPayloadError(f"sample_rate_hz must be finite and positive, got {sample_rate_hz!r}")
    nyquist = sample_rate_hz / 2.0
    if not np.isfinite(config.highpass_hz) or config.highpass_hz < 0.0:
        raise VibrationPayloadError("highpass_hz must be finite and non-negative")
    if config.highpass_hz >= nyquist:
        raise VibrationPayloadError("highpass_hz must be below Nyquist")
    if config.lowpass_hz is not None:
        if not np.isfinite(config.lowpass_hz) or config.lowpass_hz <= 0.0:
            raise VibrationPayloadError("lowpass_hz must be finite and positive when supplied")
        if config.lowpass_hz <= config.highpass_hz:
            raise VibrationPayloadError("lowpass_hz must be greater than highpass_hz")
        if config.lowpass_hz >= nyquist:
            raise VibrationPayloadError("lowpass_hz must be below Nyquist")


def _to_mm_per_s2(values: np.ndarray, unit: str) -> np.ndarray:
    normalized = unit.strip().lower().replace(" ", "")
    if normalized in {"g", "gn"}:
        return values * STANDARD_GRAVITY_MM_PER_S2
    if normalized in {"m/s^2", "m/s2", "mps2"}:
        return values * 1000.0
    if normalized in {"mm/s^2", "mm/s2", "mmps2"}:
        return values.astype(np.float64, copy=True)
    raise VibrationPayloadError(f"unsupported acceleration unit for velocity conversion: {unit!r}")


def _to_mm_per_s(values: np.ndarray, unit: str) -> np.ndarray:
    normalized = unit.strip().lower().replace(" ", "")
    if normalized in {"mm/s", "mmps"}:
        return values.astype(np.float64, copy=True)
    if normalized in {"m/s", "mps"}:
        return values * 1000.0
    if normalized in {"um/s", "umps", "micrometer/s", "micrometers/s"}:
        return values / 1000.0
    raise VibrationPayloadError(f"unsupported velocity unit for displacement conversion: {unit!r}")


def _interpolate_nonfinite(values: np.ndarray) -> tuple[np.ndarray, list[str]]:
    result = np.empty_like(values, dtype=np.float64)
    warnings: list[str] = []
    sample_axis = np.arange(values.shape[1], dtype=np.float64)
    for channel_index, row in enumerate(values):
        mask = np.isfinite(row)
        finite_ratio = float(np.count_nonzero(mask) / row.size) if row.size else 0.0
        if finite_ratio < 1.0:
            warnings.append(f"channel {channel_index} non-finite samples interpolated; finite ratio={finite_ratio:.6f}")
        if finite_ratio < 0.5:
            raise VibrationPayloadError(
                f"channel {channel_index} finite-value ratio below 0.5; cannot integrate reliably"
            )
        if finite_ratio == 1.0:
            result[channel_index] = row
            continue
        finite_indices = np.flatnonzero(mask).astype(np.float64)
        result[channel_index] = np.interp(sample_axis, finite_indices, row[mask].astype(np.float64))
    return result, warnings


def _integration_weights(frequencies: np.ndarray, config: VelocityIntegrationConfig) -> np.ndarray:
    weights = np.ones_like(frequencies, dtype=np.float64)
    weights *= _highpass_weights(
        frequencies,
        cutoff_hz=float(config.highpass_hz),
        transition_hz=_default_highpass_transition(config),
    )
    if config.lowpass_hz is not None:
        weights *= _lowpass_weights(
            frequencies,
            cutoff_hz=float(config.lowpass_hz),
            transition_hz=_default_lowpass_transition(config),
        )
    weights[0] = 0.0
    return weights


def _default_highpass_transition(config: VelocityIntegrationConfig) -> float:
    if config.highpass_transition_hz is not None:
        return float(config.highpass_transition_hz)
    if config.highpass_hz <= 0.0:
        return 0.0
    return min(config.highpass_hz, max(1.0, config.highpass_hz * 0.25))


def _default_lowpass_transition(config: VelocityIntegrationConfig) -> float:
    if config.lowpass_transition_hz is not None:
        return float(config.lowpass_transition_hz)
    if config.lowpass_hz is None:
        return 0.0
    return min(config.lowpass_hz, max(10.0, config.lowpass_hz * 0.1))


def _highpass_weights(frequencies: np.ndarray, *, cutoff_hz: float, transition_hz: float) -> np.ndarray:
    weights = np.ones_like(frequencies, dtype=np.float64)
    if cutoff_hz <= 0.0:
        weights[0] = 0.0
        return weights
    transition_hz = max(0.0, min(float(transition_hz), cutoff_hz))
    stop_hz = cutoff_hz - transition_hz
    weights[frequencies <= stop_hz] = 0.0
    if transition_hz > 0.0:
        ramp = (frequencies > stop_hz) & (frequencies < cutoff_hz)
        phase = (frequencies[ramp] - stop_hz) / transition_hz
        weights[ramp] = 0.5 - 0.5 * np.cos(np.pi * phase)
    return weights


def _lowpass_weights(frequencies: np.ndarray, *, cutoff_hz: float, transition_hz: float) -> np.ndarray:
    weights = np.ones_like(frequencies, dtype=np.float64)
    transition_hz = max(0.0, min(float(transition_hz), cutoff_hz))
    pass_hz = cutoff_hz - transition_hz
    weights[frequencies >= cutoff_hz] = 0.0
    if transition_hz > 0.0:
        ramp = (frequencies > pass_hz) & (frequencies < cutoff_hz)
        phase = (frequencies[ramp] - pass_hz) / transition_hz
        weights[ramp] = 0.5 + 0.5 * np.cos(np.pi * phase)
    return weights
