from __future__ import annotations

import queue
import threading
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nidata_collector.core import engine as daq_engine
from nidata_collector.config import (
    AccelerationSettings,
    AcquisitionGroup,
    ChannelSelection,
    RunConfiguration,
    SignalType,
    TemperatureNtcSettings,
)
from nidata_collector.hardware.ni import ReservationResult


class FakeWorker:
    created_signal_types: list[SignalType] = []

    def __init__(self, group, recording_control, events, stop_event) -> None:
        self.group = group
        FakeWorker.created_signal_types.append(group.signal_type)

    def run(self) -> None:
        return


def make_ni_group() -> AcquisitionGroup:
    return AcquisitionGroup(
        signal_type=SignalType.ACCELERATION,
        channels=[
            ChannelSelection(
                physical_name="Dev1/ai0",
                device_name="Dev1",
                product_type="NI 9234",
                signal_type=SignalType.ACCELERATION,
                visualize=True,
                save=True,
            )
        ],
        settings=AccelerationSettings(
            sample_rate_hz=1000.0,
            segment_samples=100,
            segment_seconds=0.1,
            min_value=-5.0,
            max_value=5.0,
        ),
    )


def make_ntc_group() -> AcquisitionGroup:
    return AcquisitionGroup(
        signal_type=SignalType.TEMPERATURE_NTC,
        channels=[
            ChannelSelection(
                physical_name="COM9/ntc1",
                device_name="COM9",
                product_type="DAMX-8013",
                signal_type=SignalType.TEMPERATURE_NTC,
                visualize=True,
                save=True,
            )
        ],
        settings=TemperatureNtcSettings(
            sample_rate_hz=1.0,
            segment_samples=10,
            segment_seconds=10.0,
            min_value=-40.0,
            max_value=150.0,
            port="COM9",
        ),
    )


class AcquisitionIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeWorker.created_signal_types = []

    def test_mixed_start_keeps_ntc_when_ni_reservation_fails(self) -> None:
        config = RunConfiguration(output_dir=Path("data"), groups=[make_ni_group(), make_ntc_group()])
        controller = daq_engine.AcquisitionController()

        with (
            patch.object(daq_engine, "reserve_network_devices", return_value=[
                ReservationResult("cDAQ9188", False, "offline")
            ]),
            patch.object(daq_engine, "TemperatureNtcWorker", FakeWorker),
            patch.object(daq_engine, "AcquisitionWorker", FakeWorker),
        ):
            controller.start(config)

        self.assertTrue(controller.has_saves)
        self.assertEqual([SignalType.TEMPERATURE_NTC], [group.signal_type for group in controller._config.groups])
        self.assertEqual([SignalType.TEMPERATURE_NTC], FakeWorker.created_signal_types)
        controller.poll_finished()

    def test_mixed_start_keeps_ntc_when_ni_snapshot_fails(self) -> None:
        config = RunConfiguration(output_dir=Path("data"), groups=[make_ni_group(), make_ntc_group()])
        controller = daq_engine.AcquisitionController()

        with (
            patch.object(daq_engine, "reserve_network_devices", return_value=[]),
            patch.object(daq_engine, "get_system_snapshot", side_effect=RuntimeError("driver offline")),
            patch.object(daq_engine, "TemperatureNtcWorker", FakeWorker),
            patch.object(daq_engine, "AcquisitionWorker", FakeWorker),
        ):
            controller.start(config)

        self.assertTrue(controller.has_saves)
        self.assertEqual([SignalType.TEMPERATURE_NTC], [group.signal_type for group in controller._config.groups])
        self.assertEqual([SignalType.TEMPERATURE_NTC], FakeWorker.created_signal_types)
        controller.poll_finished()

    def test_worker_error_does_not_stop_other_workers(self) -> None:
        class FailingWorker(daq_engine.AcquisitionWorker):
            def _run(self) -> None:
                raise RuntimeError("boom")

        events: queue.Queue[daq_engine.AcquisitionEvent] = queue.Queue()
        stop_event = threading.Event()
        worker = FailingWorker(make_ntc_group(), daq_engine.RecordingControl(), events, stop_event)

        worker.run()

        self.assertFalse(stop_event.is_set())
        event = events.get_nowait()
        self.assertEqual("error", event.kind)
        self.assertIn("boom", event.message)


if __name__ == "__main__":
    unittest.main()
