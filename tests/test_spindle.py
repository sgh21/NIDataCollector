from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nidata_collector.spindle import (
    SpindleReading,
    SpindleTelemetryRecorder,
    build_read_frame,
    build_write_frame,
    default_spindle_config,
    checksum,
    load_spindle_config,
    parse_value_response,
    with_checksum,
)


class SpindleProtocolTests(unittest.TestCase):
    def test_read_frame_for_speed_feedback(self) -> None:
        frame = build_read_frame(0x3008, device_id=1)
        self.assertEqual(frame, bytes.fromhex("AA A5 01 52 30 08 5A 8B"))

    def test_write_frame_for_speed_setpoint(self) -> None:
        frame = build_write_frame(0x0008, 500, opcode=0x77, device_id=1)
        self.assertEqual(frame, bytes.fromhex("AA A5 01 77 00 08 00 00 01 F4 5A 75"))

    def test_parse_value_response(self) -> None:
        payload = bytes([0xA5, 0x01, 0x41, 0x02, 0x00])
        payload += struct.pack(">i", 2260)
        payload += b"\x5A"
        frame = with_checksum(payload)
        response = parse_value_response(frame)
        self.assertEqual(response.response_id, 0x02)
        self.assertEqual(response.value, 2260)

    def test_checksum_skips_leading_aa(self) -> None:
        self.assertEqual(checksum(bytes.fromhex("AA A5 01 52 30 08 5A")), 0x8B)


class SpindleConfigTests(unittest.TestCase):
    def test_load_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_spindle_config(Path(tmp) / "missing.json")
        self.assertEqual(config.serial.port, "COM10")
        self.assertEqual(config.speed.address, 0x3008)
        self.assertEqual(config.current.address, 0x3002)

    def test_load_json_config(self) -> None:
        payload = {
            "serial": {"port": "COM12", "baudrate": 19200},
            "signals": {
                "speed": {"address": "0x3008", "response_id": "0x02", "scale": 0.01},
                "current": {"address": "0x3002", "response_id": "0x03", "scale": 0.01},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spindle_control.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_spindle_config(path)
        self.assertEqual(config.serial.port, "COM12")
        self.assertEqual(config.serial.baudrate, 19200)


class SpindleTelemetryTests(unittest.TestCase):
    def test_recorder_writes_csv_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            recorder = SpindleTelemetryRecorder(run_dir, default_spindle_config())
            try:
                recorder.write(
                    10.0,
                    SpindleReading(speed_rpm=123.0, current_a=1.5, speed_ok=True, current_ok=True),
                    target_rpm=500,
                    keepalive_enabled=True,
                )
            finally:
                recorder.close()
            csv_text = (run_dir / "spindle_telemetry.csv").read_text(encoding="utf-8")
            json_payload = json.loads((run_dir / "spindle_telemetry.json").read_text(encoding="utf-8"))
        self.assertIn("actual_speed_rpm", csv_text)
        self.assertIn("123.0", csv_text)
        self.assertEqual(json_payload["source"], "spindle_control")


if __name__ == "__main__":
    unittest.main()
