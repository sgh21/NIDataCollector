from __future__ import annotations

import queue
import threading
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from .config import (
    AccelerationSettings,
    AcquisitionGroup,
    RunConfiguration,
    SignalType,
    TemperatureNtcSettings,
    TemperatureRtdSettings,
)
from .devices import get_system_snapshot, reserve_network_devices, unreserve_network_devices
from .storage import RunStorage, SegmentWriter
from .temperature_card import Damx8013Client, temperature_channel_index


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


class RecordingControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._storage: RunStorage | None = None
        self._generation = 0
        self._started_at_monotonic: float | None = None

    def start(self, storage: RunStorage) -> int:
        with self._lock:
            if self._storage is not None:
                raise RuntimeError("Recording has already been triggered.")
            self._storage = storage
            self._generation += 1
            self._started_at_monotonic = time.monotonic()
            return self._generation

    def snapshot(self) -> tuple[RunStorage | None, int, float | None]:
        with self._lock:
            return self._storage, self._generation, self._started_at_monotonic

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._storage is not None


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
        self._workers: list[AcquisitionWorker] = []
        self._stop_event = threading.Event()
        self._running = False
        self._reserved_devices: set[str] = set()
        self._config: RunConfiguration | None = None
        self._device_snapshot: dict[str, Any] | None = None
        self._recording_control = RecordingControl()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def recording(self) -> bool:
        return self._recording_control.recording

    @property
    def has_saves(self) -> bool:
        return self._config.has_saves if self._config is not None else False

    def start(self, config: RunConfiguration) -> None:
        if self._running:
            raise RuntimeError("Acquisition is already running.")
        if not config.groups:
            raise ValueError("Select at least one channel to visualize or save.")

        self._stop_event.clear()
        self._recording_control = RecordingControl()
        self._config = None
        self._device_snapshot = None
        active_groups = list(config.groups)
        has_daqmx_groups = any(group.signal_type != SignalType.TEMPERATURE_NTC for group in active_groups)
        if has_daqmx_groups:
            try:
                reservations = reserve_network_devices(override=config.override_network_reservation)
            except Exception as exc:
                active_groups = self._drop_daqmx_groups(
                    active_groups,
                    f"NI reservation skipped: {type(exc).__name__}: {exc}",
                )
            else:
                failures = [result for result in reservations if not result.ok]
                for result in reservations:
                    status = "reserved" if result.ok else "reservation failed"
                    self.events.put(AcquisitionEvent("status", message=f"{result.device}: {status} {result.message}"))
                    if result.ok:
                        self._reserved_devices.add(result.device)
                if failures:
                    details = "\n".join(f"{item.device}: {item.message}" for item in failures)
                    self.release_reserved_devices()
                    active_groups = self._drop_daqmx_groups(
                        active_groups,
                        f"NI acquisition disabled because network reservation failed:\n{details}",
                    )

        if any(group.signal_type != SignalType.TEMPERATURE_NTC for group in active_groups):
            try:
                self._device_snapshot = get_system_snapshot()
            except Exception as exc:
                self.release_reserved_devices()
                active_groups = self._drop_daqmx_groups(
                    active_groups,
                    f"NI acquisition disabled because device snapshot failed: {type(exc).__name__}: {exc}",
                )

        if not active_groups:
            raise RuntimeError("No selected acquisition device is available.")

        if self._device_snapshot is None:
            self._device_snapshot = {"driver_version": "", "devices": []}
        effective_config = replace(config, groups=active_groups)
        self._config = effective_config
        self._device_snapshot["serial_temperature_cards"] = serial_temperature_card_snapshots(active_groups)
        if len(active_groups) != len(config.groups):
            self.events.put(
                AcquisitionEvent(
                    "status",
                    message=(
                        f"Monitoring will continue with {len(active_groups)} available "
                        f"acquisition group(s)."
                    ),
                )
            )

        self._threads = []
        self._workers = []
        for group in active_groups:
            if group.signal_type == SignalType.TEMPERATURE_NTC:
                worker = TemperatureNtcWorker(group, self._recording_control, self.events, self._stop_event)
            else:
                worker = AcquisitionWorker(group, self._recording_control, self.events, self._stop_event)
            self._workers.append(worker)
            thread = threading.Thread(target=worker.run, name=f"acq-{group.signal_type.value}", daemon=True)
            self._threads.append(thread)
            thread.start()

        self._running = True
        self.events.put(
            AcquisitionEvent(
                "started",
                message="Monitoring started. Press Trigger to record saved channels.",
            )
        )

    def trigger_recording(self) -> Path:
        if not self._running:
            raise RuntimeError("Start monitoring before triggering a recording.")
        if self._config is None or self._device_snapshot is None:
            raise RuntimeError("Monitoring configuration is not available.")
        if not self._config.has_saves:
            raise ValueError("Select at least one Save channel before triggering a recording.")
        if self._recording_control.recording:
            raise RuntimeError("Recording has already been triggered.")
        storage = RunStorage(self._config, self._device_snapshot)
        self._recording_control.start(storage)
        self.events.put(
            AcquisitionEvent(
                "recording_started",
                message=f"Recording triggered: {storage.run_dir}",
                payload={"run_dir": str(storage.run_dir)},
            )
        )
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
            self._workers = []
            self._config = None
            self._device_snapshot = None
            return True
        return False

    def shutdown(self, join_timeout_s: float = 5.0) -> None:
        self.stop()
        for thread in self._threads:
            thread.join(timeout=join_timeout_s)
        self._running = any(thread.is_alive() for thread in self._threads)
        if not self._running:
            self.release_reserved_devices()

    def release_reserved_devices(self) -> None:
        if not self._reserved_devices:
            return
        try:
            results = unreserve_network_devices(self._reserved_devices)
        except Exception as exc:
            self.events.put(
                AcquisitionEvent(
                    "status",
                    message=f"NI release skipped: {type(exc).__name__}: {exc}",
                )
            )
            return
        for result in results:
            status = "released" if result.ok else "release failed"
            self.events.put(AcquisitionEvent("status", message=f"{result.device}: {status} {result.message}"))
            if result.ok:
                self._reserved_devices.discard(result.device)

    def _drop_daqmx_groups(self, groups: list[AcquisitionGroup], reason: str) -> list[AcquisitionGroup]:
        remaining = [group for group in groups if group.signal_type == SignalType.TEMPERATURE_NTC]
        if remaining:
            self.events.put(
                AcquisitionEvent(
                    "status",
                    message=f"{reason}\nSerial temperature acquisition remains available.",
                )
            )
            return remaining
        raise RuntimeError(reason)


class AcquisitionWorker:
    def __init__(
        self,
        group: AcquisitionGroup,
        recording_control: RecordingControl,
        events: queue.Queue[AcquisitionEvent],
        stop_event: threading.Event,
    ) -> None:
        self.group = group
        self.recording_control = recording_control
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

    def _run(self) -> None:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType
        from nidaqmx.stream_readers import AnalogMultiChannelReader

        settings = self.group.settings
        channels = self.group.read_channels
        if not channels:
            return

        writer: AsyncSegmentWriter | None = None
        local_recording_generation = 0
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
                next_record_sample = 0

                while not self.stop_event.is_set():
                    storage, generation, _started_at = self.recording_control.snapshot()
                    if generation != local_recording_generation:
                        writer = self._start_writer(storage)
                        local_recording_generation = generation
                        segment_fill = 0
                        segment_start = 0
                        next_record_sample = 0

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

                    if writer is not None:
                        offset = 0
                        while offset < n_read:
                            if segment_fill == 0:
                                segment_start = next_record_sample
                            take = min(samples_per_segment - segment_fill, n_read - offset)
                            segment[:, segment_fill : segment_fill + take] = chunk[:, offset : offset + take]
                            segment_fill += take
                            offset += take
                            next_record_sample += take
                            if segment_fill == samples_per_segment:
                                segment_time = (
                                    np.arange(samples_per_segment, dtype=np.float64) + segment_start
                                ) / settings.sample_rate_hz
                                writer.enqueue(
                                    segment_start,
                                    settings.sample_rate_hz,
                                    segment_time,
                                    segment,
                                )
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

    def _start_writer(self, storage: RunStorage | None) -> AsyncSegmentWriter | None:
        if storage is None or not self.group.save_channels:
            return None
        writer = AsyncSegmentWriter(SegmentWriter(storage, self.group), self.events, self.group)
        writer.start()
        self.events.put(
            AcquisitionEvent(
                "status",
                group=self.group.signal_type.value,
                message=f"{self.group.signal_type.label} recording enabled: {len(self.group.save_channels)} channel(s)",
            )
        )
        return writer

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


class TemperatureNtcWorker(AcquisitionWorker):
    def _run(self) -> None:
        settings = self.group.settings
        if not isinstance(settings, TemperatureNtcSettings):
            raise TypeError("NTC temperature group requires TemperatureNtcSettings.")

        channels = self.group.read_channels
        if not channels:
            return

        channel_indices = [temperature_channel_index(channel) for channel in channels]
        samples_per_segment = max(1, int(settings.segment_samples))
        poll_interval_s = 1.0 / settings.sample_rate_hz
        writer: AsyncSegmentWriter | None = None
        local_recording_generation = 0
        sample_cursor = 0
        segment_start = 0
        segment = np.empty((len(channels), samples_per_segment), dtype=np.float64)
        segment_fill = 0
        next_record_sample = 0
        consecutive_failures = 0

        try:
            with Damx8013Client(settings) as client:
                if settings.sync_parameters_on_start:
                    client.sync_ntc_parameters()
                    self.events.put(
                        AcquisitionEvent(
                            "status",
                            group=self.group.signal_type.value,
                            message=(
                                f"DAMX-8013 NTC parameters synced on {settings.port}: "
                                f"R={settings.r_kohms:g}K, B={settings.b_value}"
                            ),
                        )
                    )

                self.events.put(
                    AcquisitionEvent(
                        "status",
                        group=self.group.signal_type.value,
                        message=f"DAMX-8013 task started on {settings.port}: {len(channels)} channel(s)",
                    )
                )

                while not self.stop_event.is_set():
                    loop_started = time.monotonic()
                    storage, generation, _started_at = self.recording_control.snapshot()
                    if generation != local_recording_generation:
                        writer = self._start_writer(storage)
                        local_recording_generation = generation
                        segment_fill = 0
                        segment_start = 0
                        next_record_sample = 0

                    try:
                        all_temperatures = client.read_temperatures()
                        consecutive_failures = 0
                    except Exception as exc:
                        consecutive_failures += 1
                        self.events.put(
                            AcquisitionEvent(
                                "status",
                                group=self.group.signal_type.value,
                                message=(
                                    f"DAMX-8013 read failed "
                                    f"({consecutive_failures}/3): {type(exc).__name__}: {exc}"
                                ),
                            )
                        )
                        if consecutive_failures >= 3:
                            raise RuntimeError(
                                "DAMX-8013 read failed 3 consecutive times."
                            ) from exc
                        self._wait_for_next_poll(loop_started, poll_interval_s)
                        continue

                    chunk = np.asarray(
                        [[all_temperatures[index]] for index in channel_indices],
                        dtype=np.float64,
                    )
                    time_s = np.array([sample_cursor / settings.sample_rate_hz], dtype=np.float64)
                    self._emit_plot(sample_cursor, time_s, chunk)

                    if writer is not None:
                        if segment_fill == 0:
                            segment_start = next_record_sample
                        segment[:, segment_fill] = chunk[:, 0]
                        segment_fill += 1
                        next_record_sample += 1
                        if segment_fill == samples_per_segment:
                            segment_time = (
                                np.arange(samples_per_segment, dtype=np.float64) + segment_start
                            ) / settings.sample_rate_hz
                            writer.enqueue(
                                segment_start,
                                settings.sample_rate_hz,
                                segment_time,
                                segment,
                            )
                            segment_fill = 0

                    sample_cursor += 1
                    self._wait_for_next_poll(loop_started, poll_interval_s)

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

    def _wait_for_next_poll(self, loop_started: float, poll_interval_s: float) -> None:
        remaining = poll_interval_s - (time.monotonic() - loop_started)
        if remaining > 0:
            self.stop_event.wait(remaining)


def serial_temperature_card_snapshots(groups: list[AcquisitionGroup]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for group in groups:
        if group.signal_type != SignalType.TEMPERATURE_NTC:
            continue
        settings = group.settings
        if not isinstance(settings, TemperatureNtcSettings):
            continue
        snapshots.append(
            {
                "model": settings.model,
                "port": settings.port,
                "slave_id": settings.slave_id,
                "baudrate": settings.baudrate,
                "data_bits": settings.data_bits,
                "parity": settings.parity,
                "stop_bits": settings.stop_bits,
                "channel_count": settings.channel_count,
                "channels": group.read_channels,
            }
        )
    return snapshots


def choose_chunk_size(sample_rate_hz: float, segment_samples: int) -> int:
    responsive_chunk = max(1, int(round(sample_rate_hz * 0.01)))
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

    resistance_config_name = settings.resistance_config.upper()
    if resistance_config_name == "TWO_WIRE" and any(
        "9216" in channel.product_type for channel in group.channels
    ):
        # NI documents the NI 9216 two-wire RTD workaround as a 3-wire DAQmx task.
        resistance_config_name = "THREE_WIRE"

    for channel in group.read_channels:
        task.ai_channels.add_ai_rtd_chan(
            channel,
            min_val=settings.min_value,
            max_val=settings.max_value,
            units=TemperatureUnits.DEG_C,
            rtd_type=RTDType[settings.rtd_type.upper()],
            resistance_config=ResistanceConfiguration[resistance_config_name],
            current_excit_source=ExcitationSource.INTERNAL,
            current_excit_val=settings.excitation_current_a,
            r_0=settings.r0_ohms,
        )
