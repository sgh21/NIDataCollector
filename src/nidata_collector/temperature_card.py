from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import TemperatureNtcSettings


DAMX8013_MODEL = "DAMX-8013"
DAMX8013_CHANNEL_COUNT = 2
TEMPERATURE_START_ADDRESS = 0x0000
NTC_R_VALUE_ADDRESS = 0x039D
NTC_B_VALUE_ADDRESS = 0x039E


@dataclass(frozen=True)
class Damx8013Config:
    model: str = DAMX8013_MODEL
    port: str = "COM3"
    slave_id: int = 1
    baudrate: int = 9600
    data_bits: int = 8
    parity: str = "N"
    stop_bits: float = 1.0
    timeout_s: float = 1.0
    channel_count: int = DAMX8013_CHANNEL_COUNT
    sample_rate_hz: float = 1.0
    segment_samples: int = 10
    min_deg_c: float = -40.0
    max_deg_c: float = 150.0
    r_kohms: float = 10.0
    b_value: int = 3950
    sync_parameters_on_start: bool = True

    @property
    def segment_seconds(self) -> float:
        return self.segment_samples / self.sample_rate_hz


def default_temperature_card_payload() -> dict[str, Any]:
    return asdict(Damx8013Config())


def load_temperature_card_config(path: Path) -> Damx8013Config:
    if not path.exists():
        return Damx8013Config()

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    default = Damx8013Config()
    config = Damx8013Config(
        model=_string(payload, "model", default.model),
        port=_string(payload, "port", default.port),
        slave_id=_integer(payload, "slave_id", default.slave_id),
        baudrate=_integer(payload, "baudrate", default.baudrate),
        data_bits=_integer(payload, "data_bits", default.data_bits),
        parity=_string(payload, "parity", default.parity),
        stop_bits=_number(payload, "stop_bits", default.stop_bits),
        timeout_s=_number(payload, "timeout_s", default.timeout_s),
        channel_count=_integer(payload, "channel_count", default.channel_count),
        sample_rate_hz=_number(payload, "sample_rate_hz", default.sample_rate_hz),
        segment_samples=_integer(payload, "segment_samples", default.segment_samples),
        min_deg_c=_number(payload, "min_deg_c", default.min_deg_c),
        max_deg_c=_number(payload, "max_deg_c", default.max_deg_c),
        r_kohms=_number(payload, "r_kohms", default.r_kohms),
        b_value=_integer(payload, "b_value", default.b_value),
        sync_parameters_on_start=_boolean(
            payload,
            "sync_parameters_on_start",
            default.sync_parameters_on_start,
        ),
    )
    validate_temperature_card_config(config)
    return config


def save_temperature_card_config(path: Path, config: Damx8013Config) -> None:
    validate_temperature_card_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def validate_temperature_card_config(config: Damx8013Config) -> None:
    if config.model != DAMX8013_MODEL:
        raise ValueError(f"Only {DAMX8013_MODEL} is supported, got {config.model!r}.")
    if not config.port.strip():
        raise ValueError("DAMX-8013 port must not be empty.")
    if not 1 <= config.slave_id <= 247:
        raise ValueError("DAMX-8013 slave_id must be in 1..247.")
    if config.baudrate <= 0:
        raise ValueError("DAMX-8013 baudrate must be positive.")
    if config.data_bits not in (7, 8):
        raise ValueError("DAMX-8013 data_bits must be 7 or 8.")
    if config.parity.upper() not in ("N", "E", "O"):
        raise ValueError("DAMX-8013 parity must be N, E, or O.")
    if config.stop_bits not in (1, 1.0, 2, 2.0):
        raise ValueError("DAMX-8013 stop_bits must be 1 or 2.")
    if config.timeout_s <= 0:
        raise ValueError("DAMX-8013 timeout_s must be positive.")
    if config.channel_count != DAMX8013_CHANNEL_COUNT:
        raise ValueError("DAMX-8013 channel_count must be 2.")
    if config.sample_rate_hz <= 0:
        raise ValueError("DAMX-8013 sample_rate_hz must be positive.")
    if config.segment_samples <= 0:
        raise ValueError("DAMX-8013 segment_samples must be positive.")
    if config.min_deg_c >= config.max_deg_c:
        raise ValueError("DAMX-8013 min_deg_c must be smaller than max_deg_c.")
    if config.r_kohms <= 0:
        raise ValueError("DAMX-8013 r_kohms must be positive.")
    if not 0 <= encode_r_kohms(config.r_kohms) <= 0xFFFF:
        raise ValueError("DAMX-8013 r_kohms is outside the writable register range.")
    if not 1 <= config.b_value <= 0xFFFF:
        raise ValueError("DAMX-8013 b_value must be in 1..65535.")


def temperature_ntc_settings_from_config(config: Damx8013Config) -> TemperatureNtcSettings:
    return TemperatureNtcSettings(
        sample_rate_hz=config.sample_rate_hz,
        segment_samples=config.segment_samples,
        segment_seconds=config.segment_seconds,
        min_value=config.min_deg_c,
        max_value=config.max_deg_c,
        model=config.model,
        port=config.port,
        slave_id=config.slave_id,
        baudrate=config.baudrate,
        data_bits=config.data_bits,
        parity=config.parity.upper(),
        stop_bits=float(config.stop_bits),
        timeout_s=config.timeout_s,
        channel_count=config.channel_count,
        r_kohms=config.r_kohms,
        b_value=config.b_value,
        sync_parameters_on_start=config.sync_parameters_on_start,
    )


def build_temperature_channel_name(port: str, channel_number: int) -> str:
    if not 1 <= channel_number <= DAMX8013_CHANNEL_COUNT:
        raise ValueError("DAMX-8013 channel number must be 1 or 2.")
    return f"{port}/ntc{channel_number}"


def temperature_channel_index(channel_name: str) -> int:
    marker = "/ntc"
    if marker not in channel_name.lower():
        raise ValueError(f"Invalid DAMX-8013 channel name: {channel_name!r}")
    suffix = channel_name.lower().rsplit(marker, 1)[1]
    try:
        channel_number = int(suffix)
    except ValueError as exc:
        raise ValueError(f"Invalid DAMX-8013 channel name: {channel_name!r}") from exc
    if not 1 <= channel_number <= DAMX8013_CHANNEL_COUNT:
        raise ValueError(f"DAMX-8013 channel must be ntc1 or ntc2: {channel_name!r}")
    return channel_number - 1


def encode_r_kohms(r_kohms: float) -> int:
    return int(round(r_kohms * 100.0))


def desired_ntc_parameter_registers(settings: TemperatureNtcSettings) -> tuple[int, int]:
    return (encode_r_kohms(settings.r_kohms), settings.b_value)


def modbus_crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(payload: bytes) -> bytes:
    crc = modbus_crc16(payload)
    return payload + crc.to_bytes(2, byteorder="little")


def build_read_holding_registers_request(slave_id: int, start_address: int, register_count: int) -> bytes:
    if not 1 <= register_count <= 125:
        raise ValueError("register_count must be in 1..125.")
    payload = bytes([slave_id, 0x03])
    payload += start_address.to_bytes(2, byteorder="big")
    payload += register_count.to_bytes(2, byteorder="big")
    return append_crc(payload)


def build_write_single_register_request(slave_id: int, address: int, value: int) -> bytes:
    _validate_uint16(address, "address")
    _validate_uint16(value, "value")
    payload = bytes([slave_id, 0x06])
    payload += address.to_bytes(2, byteorder="big")
    payload += value.to_bytes(2, byteorder="big")
    return append_crc(payload)


def parse_read_holding_registers_response(
    response: bytes,
    slave_id: int,
    register_count: int,
) -> list[int]:
    expected_length = 5 + register_count * 2
    if len(response) != expected_length:
        raise ValueError(f"Expected {expected_length} response bytes, got {len(response)}.")
    _verify_crc(response)
    if response[0] != slave_id:
        raise ValueError(f"Expected slave ID {slave_id}, got {response[0]}.")
    _raise_modbus_exception(response)
    if response[1] != 0x03:
        raise ValueError(f"Expected function 0x03, got 0x{response[1]:02X}.")
    byte_count = response[2]
    if byte_count != register_count * 2:
        raise ValueError(f"Expected {register_count * 2} data bytes, got {byte_count}.")
    values = []
    for offset in range(3, 3 + byte_count, 2):
        values.append(int.from_bytes(response[offset : offset + 2], byteorder="big"))
    return values


def parse_write_single_register_response(
    response: bytes,
    slave_id: int,
    address: int,
    value: int,
) -> None:
    if len(response) != 8:
        raise ValueError(f"Expected 8 response bytes, got {len(response)}.")
    _verify_crc(response)
    if response[0] != slave_id:
        raise ValueError(f"Expected slave ID {slave_id}, got {response[0]}.")
    _raise_modbus_exception(response)
    expected = build_write_single_register_request(slave_id, address, value)
    if response != expected:
        raise ValueError("Write response does not match the requested address/value.")


def registers_to_temperatures(registers: list[int]) -> list[float]:
    return [signed_register(register) / 10.0 for register in registers]


def signed_register(value: int) -> int:
    _validate_uint16(value, "value")
    return value - 0x10000 if value & 0x8000 else value


class Damx8013Client:
    def __init__(self, settings: TemperatureNtcSettings) -> None:
        self.settings = settings
        self._serial: Any | None = None

    def __enter__(self) -> "Damx8013Client":
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is required for DAMX-8013 acquisition.") from exc

        self._serial = serial.Serial(
            port=self.settings.port,
            baudrate=self.settings.baudrate,
            bytesize=self.settings.data_bits,
            parity=self.settings.parity.upper(),
            stopbits=self.settings.stop_bits,
            timeout=self.settings.timeout_s,
            write_timeout=self.settings.timeout_s,
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def sync_ntc_parameters(self) -> None:
        desired_r, desired_b = desired_ntc_parameter_registers(self.settings)
        current_r, current_b = self.read_holding_registers(NTC_R_VALUE_ADDRESS, 2)
        if (current_r, current_b) == (desired_r, desired_b):
            return

        self.write_single_register(NTC_R_VALUE_ADDRESS, desired_r)
        self.write_single_register(NTC_B_VALUE_ADDRESS, desired_b)
        updated_r, updated_b = self.read_holding_registers(NTC_R_VALUE_ADDRESS, 2)
        if (updated_r, updated_b) != (desired_r, desired_b):
            raise RuntimeError(
                "DAMX-8013 R/B register verification failed: "
                f"expected {(desired_r, desired_b)}, got {(updated_r, updated_b)}."
            )

    def read_temperatures(self) -> list[float]:
        registers = self.read_holding_registers(TEMPERATURE_START_ADDRESS, DAMX8013_CHANNEL_COUNT)
        return registers_to_temperatures(registers)

    def read_holding_registers(self, start_address: int, register_count: int) -> list[int]:
        request = build_read_holding_registers_request(
            self.settings.slave_id,
            start_address,
            register_count,
        )
        response = self._request(request, expected_response_length=5 + register_count * 2)
        return parse_read_holding_registers_response(
            response,
            self.settings.slave_id,
            register_count,
        )

    def write_single_register(self, address: int, value: int) -> None:
        request = build_write_single_register_request(self.settings.slave_id, address, value)
        response = self._request(request, expected_response_length=8)
        parse_write_single_register_response(response, self.settings.slave_id, address, value)

    def _request(self, request: bytes, expected_response_length: int) -> bytes:
        if self._serial is None:
            raise RuntimeError("DAMX-8013 serial port is not open.")
        self._serial.reset_input_buffer()
        self._serial.write(request)
        response = self._serial.read(expected_response_length)
        if len(response) != expected_response_length:
            raise TimeoutError(
                f"DAMX-8013 response timeout on {self.settings.port}: "
                f"expected {expected_response_length} bytes, got {len(response)}."
            )
        return response


def _verify_crc(response: bytes) -> None:
    if len(response) < 4:
        raise ValueError("Response is too short for Modbus CRC.")
    payload = response[:-2]
    received = int.from_bytes(response[-2:], byteorder="little")
    expected = modbus_crc16(payload)
    if received != expected:
        raise ValueError(f"Modbus CRC mismatch: expected 0x{expected:04X}, got 0x{received:04X}.")


def _raise_modbus_exception(response: bytes) -> None:
    if response[1] & 0x80:
        code = response[2] if len(response) > 2 else 0
        raise ValueError(f"Modbus exception 0x{code:02X}.")


def _validate_uint16(value: int, label: str) -> None:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{label} must be in 0..65535.")


def _string(payload: dict[str, Any], key: str, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value.strip()


def _integer(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    return value


def _number(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number.")
    return float(value)


def _boolean(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false.")
    return value
