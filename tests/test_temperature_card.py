from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nidata_collector.hardware.damx8013 import (
    build_read_holding_registers_request,
    build_write_single_register_request,
    encode_r_kohms,
    load_temperature_card_config,
    parse_read_holding_registers_response,
    registers_to_temperatures,
)


class TemperatureCardProtocolTests(unittest.TestCase):
    def test_read_request_crc_matches_manual(self) -> None:
        request = build_read_holding_registers_request(1, 0x0000, 1)
        self.assertEqual(request.hex(" ").upper(), "01 03 00 00 00 01 84 0A")

    def test_read_response_temperature_parses_signed_tenths_deg_c(self) -> None:
        registers = parse_read_holding_registers_response(
            bytes.fromhex("01 03 02 01 13 F8 19"),
            slave_id=1,
            register_count=1,
        )
        self.assertEqual(registers_to_temperatures(registers), [27.5])

    def test_write_request_crc_matches_manual(self) -> None:
        request = build_write_single_register_request(1, 0x0384, 0x0005)
        self.assertEqual(request.hex(" ").upper(), "01 06 03 84 00 05 09 A4")

    def test_ntc_parameter_encoding(self) -> None:
        self.assertEqual(encode_r_kohms(10.0), 1000)
        self.assertEqual(build_write_single_register_request(1, 0x039D, 1000)[:6].hex(" ").upper(), "01 06 03 9D 03 E8")
        self.assertEqual(build_write_single_register_request(1, 0x039E, 3950)[:6].hex(" ").upper(), "01 06 03 9E 0F 6E")

    def test_negative_temperature_register(self) -> None:
        self.assertEqual(registers_to_temperatures([0xFE70]), [-40.0])


class TemperatureCardConfigTests(unittest.TestCase):
    def test_missing_config_uses_damx8013_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_temperature_card_config(Path(tmp) / "missing.json")
        self.assertEqual(config.model, "DAMX-8013")
        self.assertEqual(config.channel_count, 2)
        self.assertEqual(config.r_kohms, 10.0)
        self.assertEqual(config.b_value, 3950)

    def test_valid_config_loads(self) -> None:
        payload = {
            "model": "DAMX-8013",
            "port": "COM7",
            "slave_id": 1,
            "baudrate": 9600,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
            "timeout_s": 0.5,
            "channel_count": 2,
            "sample_rate_hz": 2.0,
            "segment_samples": 20,
            "min_deg_c": -40.0,
            "max_deg_c": 150.0,
            "r_kohms": 10.0,
            "b_value": 3950,
            "sync_parameters_on_start": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "temperature_card.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_temperature_card_config(path)
        self.assertEqual(config.port, "COM7")
        self.assertEqual(config.segment_seconds, 10.0)

    def test_invalid_channel_count_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "temperature_card.json"
            path.write_text(json.dumps({"channel_count": 16}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "channel_count"):
                load_temperature_card_config(path)

    def test_invalid_sample_rate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "temperature_card.json"
            path.write_text(json.dumps({"sample_rate_hz": 0}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sample_rate_hz"):
                load_temperature_card_config(path)

    def test_invalid_r_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "temperature_card.json"
            path.write_text(json.dumps({"r_kohms": 0}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "r_kohms"):
                load_temperature_card_config(path)


if __name__ == "__main__":
    unittest.main()
