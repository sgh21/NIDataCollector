from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import (
    AccelerationSettings,
    AcquisitionGroup,
    RunConfiguration,
    SignalType,
    TemperatureRtdSettings,
)
from .devices import get_system_snapshot, reserve_network_devices
from .storage import RunStorage, SegmentWriter


@dataclass(frozen=True)
class AcquisitionEvent:
    kind: str
    group: str = ""
    message: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class SegmentWriteJob:
    sample_start_index: int
    sample_rate_hz: float
    time_s: np.ndarray
    data: np.ndarray
    partial: bool = False


class AsyncSegmentWriter:
    def __init__(
        self,
        writer: SegmentWriter,
        events: queue.Queue[AcquisitionEvent],
        group: AcquisitionGroup,
        max_queue_size: int = 16,
    ) -> None:
        self.writer = writer
        self.events = events
        self.group = group
        self._queue: queue.Queue[SegmentWriteJob | None] = queue.Queue(maxsize=max_queue_size)
        self._thread = threading.Thread(
            target=self._run,
            name=f"writer-{group.signal_type.value}",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def enqueue(
        self,
        sample_start_index: int,
        sample_rate_hz: float,
        time_s: np.ndarray,
        data: np.ndarray,
        partial: bool = False,
    ) -> None:
        job = SegmentWriteJob(
            sample_start_index=sample_start_index,
            sample_rate_hz=sample_rate_hz,
            time_s=time_s.copy(),
            data=data.copy(),
            partial=partial,
        )
        self._queue.put(job)

    def close(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                result = self.writer.write_segment(
                    job.sample_start_index,
                    job.sample_rate_hz,
                    job.time_s,
                    job.data,
                    partial=job.partial,
                )
                if result is not None:
                    label = "partial " if job.partial else ""
                    self.events.put(
                        AcquisitionEvent(
                            "saved",
                            group=self.group.signal_type.value,
                            message=f"Saved {label}{result[0].name}",
                            payload={"csv": str(result[0]), "json": str(result[1])},
                        )
                    )
            except Exception as exc:
                self.events.put(
                    AcquisitionEvent(
                        "error",
                        group=self.group.signal_type.value,
                        message=f"Writer {type(exc).__name__}: {exc}",
                        payload={"traceback": traceback.format_exc()},
                    )
                )
            finally:
                self._queue.task_done()


class AcquisitionController:
    def __init__(self) -> None:
        self.events: queue.Queue[AcquisitionEvent] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, config: RunConfiguration) -> Path:
        if self._running:
            raise RuntimeError("Acquisition is already running.")
        if not config.groups:
            raise ValueError("Select at least one channel to visualize or save.")

        self._stop_event.clear()
        for result in reserve_network_devices(override=False):
            status = "reserved" if result.ok else "reservation failed"
            self.events.put(AcquisitionEvent("status", message=f"{result.device}: {status} {result.message}"))

        snapshot = get_system_snapshot()
        storage = RunStorage(config, snapshot)

        self._threads = []
        for group in config.groups:
            worker = AcquisitionWorker(group, storage, self.events, self._stop_event)
            thread = threading.Thread(target=worker.run, name=f"daq-{group.signal_type.value}", daemon=True)
            self._threads.append(thread)
            thread.start()

        self._running = True
        self.events.put(AcquisitionEvent("started", message=f"Run folder: {storage.run_dir}"))
        return storage.run_dir

    def stop(self) -> None:
        self._stop_event.set()

    def poll_finished(self) -> bool:
        if not self._running:
            return True
        alive = any(thread.is_alive() for thread in self._threads)
        if not alive:
            self._running = False
            self.events.put(AcquisitionEvent("stopped", message="Acquisition stopped."))
            return True
        return False


class AcquisitionWorker:
    def __init__(
        self,
        group: AcquisitionGroup,
        storage: RunStorage,
        events: queue.Queue[AcquisitionEvent],
        stop_event: threading.Event,
    ) -> None:
        self.group = group
        self.storage = storage
        self.events = events
        self.stop_event = stop_event

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            self.events.put(
                AcquisitionEvent(
                    "error",
                    group=self.group.signal_type.value,
                    message=f"{type(exc).__name__}: {exc}",
                    payload={"traceback": traceback.format_exc()},
                )
            )
            self.stop_event.set()

    def _run(self) -> None:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType
        from nidaqmx.stream_readers import AnalogMultiChannelReader

        settings = self.group.settings
        channels = self.group.read_channels
        if not channels:
            return

        writer = (
            AsyncSegmentWriter(SegmentWriter(self.storage, self.group), self.events, self.group)
            if self.group.save_channels
            else None
        )
        if writer is not None:
            writer.start()
        samples_per_segment = max(1, int(settings.segment_samples))
        chunk_samples = choose_chunk_size(settings.sample_rate_hz, samples_per_segment)
        data_buffer = np.empty((len(channels), chunk_samples), dtype=np.float64)

        try:
            with nidaqmx.Task() as task:
                configure_group_channels(task, self.group)
                buffer_samples = max(samples_per_segment * 4, chunk_samples * 4, 1000)
                task.timing.cfg_samp_clk_timing(
                    settings.sample_rate_hz,
                    sample_mode=AcquisitionType.CONTINUOUS,
                    samps_per_chan=buffer_samples,
                )
                reader = AnalogMultiChannelReader(task.in_stream)
                reader.verify_array_shape = True
                task.start()

                self._discard_settle_samples(reader, data_buffer)
                self.events.put(
                    AcquisitionEvent(
                        "status",
                        group=self.group.signal_type.value,
                        message=f"{self.group.signal_type.label} task started: {len(channels)} channel(s)",
                    )
                )

                sample_cursor = 0
                segment_start = 0
                segment = np.empty((len(channels), samples_per_segment), dtype=np.float64)
                segment_fill = 0

                while not self.stop_event.is_set():
                    n_read = reader.read_many_sample(
                        data_buffer,
                        number_of_samples_per_channel=chunk_samples,
                        timeout=max(1.0, chunk_samples / settings.sample_rate_hz + 1.0),
                    )
                    if n_read <= 0:
                        continue

                    chunk = data_buffer[:, :n_read].copy()
                    time_s = (np.arange(n_read, dtype=np.float64) + sample_cursor) / settings.sample_rate_hz
                    self._emit_plot(sample_cursor, time_s, chunk)

                    offset = 0
                    while offset < n_read:
                        take = min(samples_per_segment - segment_fill, n_read - offset)
                        segment[:, segment_fill : segment_fill + take] = chunk[:, offset : offset + take]
                        segment_fill += take
                        offset += take
                        if segment_fill == samples_per_segment:
                            if writer is not None:
                                segment_time = (
                                    np.arange(samples_per_segment, dtype=np.float64) + segment_start
                                ) / settings.sample_rate_hz
                                writer.enqueue(
                                    segment_start,
                                    settings.sample_rate_hz,
                                    segment_time,
                                    segment,
                                )
                            segment_start += samples_per_segment
                            segment_fill = 0

                    sample_cursor += n_read

                if writer is not None and segment_fill:
                    partial = segment[:, :segment_fill]
                    partial_time = (
                        np.arange(segment_fill, dtype=np.float64) + segment_start
                    ) / settings.sample_rate_hz
                    writer.enqueue(
                        segment_start,
                        settings.sample_rate_hz,
                        partial_time,
                        partial,
                        partial=True,
                    )
        finally:
            if writer is not None:
                self.events.put(
                    AcquisitionEvent(
                        "status",
                        group=self.group.signal_type.value,
                        message=f"Flushing {self.group.signal_type.label} file queue",
                    )
                )
                writer.close()

    def _discard_settle_samples(self, reader: Any, data_buffer: np.ndarray) -> None:
        settings = self.group.settings
        if not isinstance(settings, AccelerationSettings):
            return
        settle_samples = int(round(settings.settle_seconds * settings.sample_rate_hz))
        remaining = max(0, settle_samples)
        while remaining and not self.stop_event.is_set():
            count = min(remaining, data_buffer.shape[1])
            discard = data_buffer if count == data_buffer.shape[1] else np.empty((data_buffer.shape[0], count))
            reader.read_many_sample(discard, number_of_samples_per_channel=count, timeout=5.0)
            remaining -= count

    def _emit_plot(self, sample_start: int, time_s: np.ndarray, data: np.ndarray) -> None:
        plot_channels = self.group.visualize_channels
        if not plot_channels:
            return
        read_channels = self.group.read_channels
        plot_indices = [read_channels.index(channel) for channel in plot_channels]
        plot_data = data[plot_indices, :]
        plot_time = time_s
        max_points = 1600
        if plot_data.shape[1] > max_points:
            step = int(np.ceil(plot_data.shape[1] / max_points))
            plot_data = plot_data[:, ::step]
            plot_time = plot_time[::step]
        self.events.put(
            AcquisitionEvent(
                "data",
                group=self.group.signal_type.value,
                payload={
                    "sample_start": sample_start,
                    "sample_rate_hz": self.group.settings.sample_rate_hz,
                    "unit": self.group.signal_type.unit,
                    "channels": plot_channels,
                    "time_s": plot_time,
                    "data": plot_data,
                },
            )
        )


def choose_chunk_size(sample_rate_hz: float, segment_samples: int) -> int:
    responsive_chunk = max(1, int(round(sample_rate_hz * 0.05)))
    return max(1, min(segment_samples, responsive_chunk))


def configure_group_channels(task: Any, group: AcquisitionGroup) -> None:
    if group.signal_type == SignalType.ACCELERATION:
        configure_acceleration_channels(task, group)
        return
    if group.signal_type == SignalType.TEMPERATURE_RTD:
        configure_temperature_channels(task, group)
        return
    raise ValueError(f"Unsupported signal type: {group.signal_type}")


def configure_acceleration_channels(task: Any, group: AcquisitionGroup) -> None:
    from nidaqmx.constants import AccelSensitivityUnits, AccelUnits, Coupling, ExcitationSource

    settings = group.settings
    if not isinstance(settings, AccelerationSettings):
        raise TypeError("Acceleration group requires AccelerationSettings.")

    for channel in group.read_channels:
        ai_channel = task.ai_channels.add_ai_accel_chan(
            channel,
            min_val=settings.min_value,
            max_val=settings.max_value,
            units=AccelUnits.G,
            sensitivity=settings.sensitivity_mv_per_g,
            sensitivity_units=AccelSensitivityUnits.MILLIVOLTS_PER_G,
            current_excit_source=ExcitationSource.INTERNAL,
            current_excit_val=settings.excitation_current_a,
        )
        if settings.coupling.upper() != "NONE":
            ai_channel.ai_coupling = Coupling[settings.coupling.upper()]


def configure_temperature_channels(task: Any, group: AcquisitionGroup) -> None:
    from nidaqmx.constants import ExcitationSource, RTDType, ResistanceConfiguration, TemperatureUnits

    settings = group.settings
    if not isinstance(settings, TemperatureRtdSettings):
        raise TypeError("Temperature group requires TemperatureRtdSettings.")

    for channel in group.read_channels:
        task.ai_channels.add_ai_rtd_chan(
            channel,
            min_val=settings.min_value,
            max_val=settings.max_value,
            units=TemperatureUnits.DEG_C,
            rtd_type=RTDType[settings.rtd_type.upper()],
            resistance_config=ResistanceConfiguration[settings.resistance_config.upper()],
            current_excit_source=ExcitationSource.INTERNAL,
            current_excit_val=settings.excitation_current_a,
            r_0=settings.r0_ohms,
        )
