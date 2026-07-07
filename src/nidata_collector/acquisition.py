from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AcquisitionConfig:
    channels: list[str]
    sample_rate_hz: float = 5120.0
    duration_s: float = 2.0
    sensitivity_mv_per_g: float = 100.0
    min_g: float = -50.0
    max_g: float = 50.0
    excitation_current_a: float = 0.004
    coupling: str | None = "AC"
    settle_s: float = 5.0

    @property
    def samples_per_channel(self) -> int:
        return max(1, int(round(self.sample_rate_hz * self.duration_s)))

    @property
    def settle_samples(self) -> int:
        return max(0, int(round(self.sample_rate_hz * self.settle_s)))


def acquire_acceleration(config: AcquisitionConfig) -> tuple[np.ndarray, np.ndarray]:
    import nidaqmx
    from nidaqmx.constants import (
        AccelSensitivityUnits,
        AccelUnits,
        AcquisitionType,
        Coupling,
        ExcitationSource,
    )
    from nidaqmx.stream_readers import AnalogMultiChannelReader

    if not config.channels:
        raise ValueError("At least one physical channel is required.")

    samples = config.samples_per_channel
    settle_samples = config.settle_samples
    read_samples = samples + settle_samples
    data = np.empty((len(config.channels), read_samples), dtype=np.float64)

    with nidaqmx.Task() as task:
        for channel in config.channels:
            ai_channel = task.ai_channels.add_ai_accel_chan(
                channel,
                min_val=config.min_g,
                max_val=config.max_g,
                units=AccelUnits.G,
                sensitivity=config.sensitivity_mv_per_g,
                sensitivity_units=AccelSensitivityUnits.MILLIVOLTS_PER_G,
                current_excit_source=ExcitationSource.INTERNAL,
                current_excit_val=config.excitation_current_a,
            )
            if config.coupling is not None:
                ai_channel.ai_coupling = Coupling[config.coupling.upper()]

        task.timing.cfg_samp_clk_timing(
            config.sample_rate_hz,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=read_samples,
        )

        reader = AnalogMultiChannelReader(task.in_stream)
        task.start()
        read_count = reader.read_many_sample(
            data,
            number_of_samples_per_channel=read_samples,
            timeout=config.duration_s + config.settle_s + 10.0,
        )

    if isinstance(read_count, int) and read_count < read_samples:
        data = data[:, :read_count]

    if settle_samples:
        if data.shape[1] <= settle_samples:
            raise RuntimeError("Acquisition returned no samples after settling period.")
        data = data[:, settle_samples:]

    time_s = np.arange(data.shape[1], dtype=np.float64) / config.sample_rate_hz
    return time_s, data


def generate_simulated_acceleration(config: AcquisitionConfig) -> tuple[np.ndarray, np.ndarray]:
    if not config.channels:
        raise ValueError("At least one simulated channel is required.")

    samples = config.samples_per_channel
    time_s = np.arange(samples, dtype=np.float64) / config.sample_rate_hz
    data = np.empty((len(config.channels), samples), dtype=np.float64)

    for index, _channel in enumerate(config.channels):
        base_freq = 12.0 + index * 7.5
        harmonic = base_freq * 2.0
        data[index] = (
            0.8 * np.sin(2.0 * np.pi * base_freq * time_s)
            + 0.15 * np.sin(2.0 * np.pi * harmonic * time_s)
            + 0.02 * np.cos(2.0 * np.pi * 1.0 * time_s)
        )

    return time_s, data
