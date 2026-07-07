from __future__ import annotations

import csv
import json
import os
import struct
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class SpindleProtocolError(Exception):
    pass


@dataclass(frozen=True)
class SpindleSerialConfig:
    port: str = "COM10"
    baudrate: int = 19200
    device_id: int = 1
    timeout: float = 0.35
    write_timeout: float = 0.5


@dataclass(frozen=True)
class SpindleControlConfig:
    speed_setpoint_address: int = 0x0008
    speed_setpoint_opcode: int = 0x77
    run_enable_address: int = 0x0501
    run_enable_opcode: int = 0x77
    run_enable_value: int = 111
    run_disable_value: int = -111
    run_mode_address: int = 0x0505
    run_mode_opcode: int = 0x57
    run_mode_value: int = 0


@dataclass(frozen=True)
class SpindleSignalConfig:
    address: int
    response_id: int
    scale: float = 1.0
    default: float = 0.0
    deadband: float = 0.0


@dataclass(frozen=True)
class SpindleSafetyConfig:
    min_run_rpm: int = 0
    max_rpm: int = 24000


@dataclass(frozen=True)
class SpindleUiConfig:
    poll_interval_ms: int = 100
    keepalive_interval_ms: int = 200
    plot_window_seconds: int = 10


@dataclass(frozen=True)
class SpindleConfig:
    serial: SpindleSerialConfig
    control: SpindleControlConfig
    speed: SpindleSignalConfig
    current: SpindleSignalConfig
    safety: SpindleSafetyConfig
    ui: SpindleUiConfig

    def to_json(self) -> dict[str, Any]:
        return {
            "serial": asdict(self.serial),
            "control": {
                "speed_setpoint_address": hex(self.control.speed_setpoint_address),
                "speed_setpoint_opcode": hex(self.control.speed_setpoint_opcode),
                "run_enable_address": hex(self.control.run_enable_address),
                "run_enable_opcode": hex(self.control.run_enable_opcode),
                "run_enable_value": self.control.run_enable_value,
                "run_disable_value": self.control.run_disable_value,
                "run_mode_address": hex(self.control.run_mode_address),
                "run_mode_opcode": hex(self.control.run_mode_opcode),
                "run_mode_value": self.control.run_mode_value,
            },
            "signals": {
                "speed": signal_to_json(self.speed),
                "current": signal_to_json(self.current),
            },
            "safety": asdict(self.safety),
            "ui": asdict(self.ui),
        }


@dataclass(frozen=True)
class SpindleReading:
    speed_rpm: float
    current_a: float
    speed_ok: bool
    current_ok: bool


@dataclass(frozen=True)
class ValueResponse:
    response_id: int
    value: int
    raw: bytes


def default_spindle_config() -> SpindleConfig:
    return SpindleConfig(
        serial=SpindleSerialConfig(),
        control=SpindleControlConfig(),
        speed=SpindleSignalConfig(address=0x3008, response_id=0x02, scale=0.01, deadband=0.5),
        current=SpindleSignalConfig(address=0x3002, response_id=0x03, scale=0.01),
        safety=SpindleSafetyConfig(),
        ui=SpindleUiConfig(),
    )


def load_spindle_config(path: Path) -> SpindleConfig:
    if not path.exists():
        return default_spindle_config()

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    default = default_spindle_config()
    serial = raw.get("serial", {})
    control = raw.get("control", {})
    signals = raw.get("signals", {})
    safety = raw.get("safety", {})
    ui = raw.get("ui", {})

    config = SpindleConfig(
        serial=SpindleSerialConfig(
            port=string_value(serial, "port", default.serial.port),
            baudrate=int_value(serial, "baudrate", default.serial.baudrate),
            device_id=int_value(serial, "device_id", default.serial.device_id),
            timeout=float_value(serial, "timeout", default.serial.timeout),
            write_timeout=float_value(serial, "write_timeout", default.serial.write_timeout),
        ),
        control=SpindleControlConfig(
            speed_setpoint_address=parse_int(control.get("speed_setpoint_address", "0x0008")),
            speed_setpoint_opcode=parse_int(control.get("speed_setpoint_opcode", "0x77")),
            run_enable_address=parse_int(control.get("run_enable_address", "0x0501")),
            run_enable_opcode=parse_int(control.get("run_enable_opcode", "0x77")),
            run_enable_value=int(control.get("run_enable_value", 111)),
            run_disable_value=int(control.get("run_disable_value", -111)),
            run_mode_address=parse_int(control.get("run_mode_address", "0x0505")),
            run_mode_opcode=parse_int(control.get("run_mode_opcode", "0x57")),
            run_mode_value=int(control.get("run_mode_value", 0)),
        ),
        speed=signal_from_json(signals.get("speed", {}), default.speed),
        current=signal_from_json(signals.get("current", {}), default.current),
        safety=SpindleSafetyConfig(
            min_run_rpm=int_value(safety, "min_run_rpm", default.safety.min_run_rpm),
            max_rpm=int_value(safety, "max_rpm", default.safety.max_rpm),
        ),
        ui=SpindleUiConfig(
            poll_interval_ms=int_value(ui, "poll_interval_ms", default.ui.poll_interval_ms),
            keepalive_interval_ms=int_value(ui, "keepalive_interval_ms", default.ui.keepalive_interval_ms),
            plot_window_seconds=int_value(ui, "plot_window_seconds", default.ui.plot_window_seconds),
        ),
    )
    validate_spindle_config(config)
    return config


def save_spindle_config(path: Path, config: SpindleConfig) -> None:
    atomic_write_json(path, config.to_json())


def validate_spindle_config(config: SpindleConfig) -> None:
    if not config.serial.port.strip():
        raise ValueError("Spindle port must not be empty.")
    if config.serial.baudrate <= 0:
        raise ValueError("Spindle baudrate must be positive.")
    if not 0 <= config.serial.device_id <= 255:
        raise ValueError("Spindle device_id must be in 0..255.")
    if config.serial.timeout <= 0:
        raise ValueError("Spindle timeout must be positive.")
    if config.serial.write_timeout <= 0:
        raise ValueError("Spindle write_timeout must be positive.")
    if config.safety.min_run_rpm < 0:
        raise ValueError("Spindle min_run_rpm must be non-negative.")
    if config.safety.max_rpm <= config.safety.min_run_rpm:
        raise ValueError("Spindle max_rpm must be greater than min_run_rpm.")
    if config.ui.poll_interval_ms <= 0:
        raise ValueError("Spindle poll_interval_ms must be positive.")
    if config.ui.keepalive_interval_ms <= 0:
        raise ValueError("Spindle keepalive_interval_ms must be positive.")
    if config.ui.plot_window_seconds <= 0:
        raise ValueError("Spindle plot_window_seconds must be positive.")


def parse_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def signal_from_json(raw: Any, default: SpindleSignalConfig) -> SpindleSignalConfig:
    if not isinstance(raw, dict):
        raw = {}
    return SpindleSignalConfig(
        address=parse_int(raw.get("address", default.address)),
        response_id=parse_int(raw.get("response_id", default.response_id)),
        scale=float(raw.get("scale", default.scale)),
        default=float(raw.get("default", default.default)),
        deadband=float(raw.get("deadband", default.deadband)),
    )


def signal_to_json(signal: SpindleSignalConfig) -> dict[str, Any]:
    return {
        "address": hex(signal.address),
        "response_id": hex(signal.response_id),
        "scale": signal.scale,
        "default": signal.default,
        "deadband": signal.deadband,
    }


def checksum(frame_without_checksum: bytes) -> int:
    data = (
        frame_without_checksum[1:]
        if frame_without_checksum and frame_without_checksum[0] == 0xAA
        else frame_without_checksum
    )
    return (sum(data) + 1) & 0xFF


def with_checksum(frame_without_checksum: bytes) -> bytes:
    return frame_without_checksum + bytes([checksum(frame_without_checksum)])


def build_read_frame(address: int, device_id: int = 1) -> bytes:
    return with_checksum(
        bytes([0xAA, 0xA5, device_id & 0xFF, 0x52, (address >> 8) & 0xFF, address & 0xFF, 0x5A])
    )


def build_write_frame(address: int, value: int, opcode: int = 0x77, device_id: int = 1) -> bytes:
    payload = struct.pack(">i", int(value))
    return with_checksum(
        bytes([0xAA, 0xA5, device_id & 0xFF, opcode & 0xFF, (address >> 8) & 0xFF, address & 0xFF])
        + payload
        + b"\x5A"
    )


def parse_value_response(frame: bytes) -> ValueResponse:
    if len(frame) != 11 or frame[:3] != b"\xA5\x01\x41" or frame[9] != 0x5A:
        raise SpindleProtocolError(f"unsupported response: {frame.hex(' ')}")
    if checksum(frame[:-1]) != frame[-1]:
        raise SpindleProtocolError(f"checksum mismatch: {frame.hex(' ')}")
    return ValueResponse(response_id=frame[3], value=struct.unpack(">i", frame[5:9])[0], raw=frame)


class SpindleSerialProtocol:
    def __init__(self, config: SpindleSerialConfig) -> None:
        self.config = config
        self._serial: Any | None = None

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def open(self) -> None:
        if self.is_open:
            return
        import serial

        self._serial = serial.Serial(
            self.config.port,
            self.config.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self.config.timeout,
            write_timeout=self.config.write_timeout,
        )
        self.clear()

    def close(self) -> None:
        if self._serial:
            self._serial.close()

    def clear(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()

    def write_value(self, address: int, value: int, opcode: int) -> None:
        self.open()
        if self._serial is None:
            raise RuntimeError("Spindle serial port is not open.")
        self._serial.write(build_write_frame(address, value, opcode, self.config.device_id))
        self._serial.flush()

    def read_value(self, address: int, expected_response_id: int | None) -> int:
        self.open()
        if self._serial is None:
            raise RuntimeError("Spindle serial port is not open.")
        self.clear()
        self._serial.write(build_read_frame(address, self.config.device_id))
        self._serial.flush()

        deadline = time.monotonic() + self.config.timeout
        while time.monotonic() < deadline:
            frame = self._read_one_frame(deadline)
            if frame is None:
                break
            try:
                response = parse_value_response(frame)
            except SpindleProtocolError:
                continue
            if expected_response_id is None or response.response_id == expected_response_id:
                return response.value
        raise SpindleProtocolError(f"read timeout for address 0x{address:04X}")

    def _read_one_frame(self, deadline: float) -> bytes | None:
        if self._serial is None:
            raise RuntimeError("Spindle serial port is not open.")
        while time.monotonic() < deadline:
            first = self._serial.read(1)
            if first == b"\xA5":
                header_rest = self._serial.read(3)
                if len(header_rest) != 3:
                    return None
                if header_rest[:2] != b"\x01\x41":
                    continue
                if header_rest[2] == 0x5A:
                    checksum_byte = self._serial.read(1)
                    if len(checksum_byte) == 1:
                        return first + header_rest + checksum_byte
                    return None
                payload_rest = self._serial.read(7)
                if len(payload_rest) == 7:
                    return first + header_rest + payload_rest
                return None
        return None


class SpindleDevice:
    def __init__(self, config: SpindleConfig) -> None:
        self.config = config
        self.protocol = SpindleSerialProtocol(config.serial)
        self._last_speed = config.speed.default
        self._last_current = config.current.default
        self._target_rpm = 0
        self._lock = threading.Lock()

    def connect(self) -> None:
        with self._lock:
            self.protocol.open()

    def close(self) -> None:
        with self._lock:
            self.protocol.close()

    @property
    def target_rpm(self) -> int:
        return self._target_rpm

    def set_speed_rpm(self, rpm: float, prepare: bool = True) -> None:
        rpm_int = int(round(rpm))
        if not 0 <= rpm_int <= self.config.safety.max_rpm:
            raise ValueError(f"Spindle speed must be in 0..{self.config.safety.max_rpm} rpm.")
        control = self.config.control
        with self._lock:
            if prepare:
                self._prepare_control_mode()
            if rpm_int > 0:
                self.protocol.write_value(
                    control.run_enable_address,
                    control.run_enable_value,
                    control.run_enable_opcode,
                )
                self.protocol.write_value(
                    control.speed_setpoint_address,
                    rpm_int,
                    control.speed_setpoint_opcode,
                )
            else:
                self._stop()
            self._target_rpm = rpm_int

    def stop(self) -> None:
        with self._lock:
            self._stop()
            self._target_rpm = 0

    def keepalive(self) -> bool:
        control = self.config.control
        with self._lock:
            if self._target_rpm <= 0:
                return False
            self.protocol.write_value(control.run_enable_address, control.run_enable_value, control.run_enable_opcode)
            self.protocol.write_value(control.speed_setpoint_address, self._target_rpm, control.speed_setpoint_opcode)
            return True

    def read(self) -> SpindleReading:
        speed, speed_ok = self._read_signal(self.config.speed, self._last_speed)
        if abs(speed) < self.config.speed.deadband:
            speed = 0.0
        self._last_speed = speed

        current, current_ok = self._read_signal(self.config.current, self._last_current)
        self._last_current = current
        return SpindleReading(speed_rpm=speed, current_a=current, speed_ok=speed_ok, current_ok=current_ok)

    def _read_signal(self, signal: SpindleSignalConfig, fallback: float) -> tuple[float, bool]:
        try:
            with self._lock:
                raw = self.protocol.read_value(signal.address, signal.response_id)
            return raw * signal.scale, True
        except Exception:
            return fallback, False

    def _prepare_control_mode(self) -> None:
        control = self.config.control
        self.protocol.write_value(control.speed_setpoint_address, 0, control.speed_setpoint_opcode)
        self.protocol.write_value(control.run_enable_address, control.run_disable_value, control.run_enable_opcode)
        self.protocol.write_value(control.run_mode_address, control.run_mode_value, control.run_mode_opcode)

    def _stop(self) -> None:
        control = self.config.control
        self.protocol.write_value(control.speed_setpoint_address, 0, control.speed_setpoint_opcode)
        self.protocol.write_value(control.run_enable_address, control.run_disable_value, control.run_enable_opcode)
        self.protocol.write_value(control.run_mode_address, control.run_mode_value, control.run_mode_opcode)


class SpindleTelemetryRecorder:
    def __init__(self, run_dir: Path, config: SpindleConfig) -> None:
        self.run_dir = run_dir
        self.config = config
        self.csv_path = run_dir / "spindle_telemetry.csv"
        self.json_path = run_dir / "spindle_telemetry.json"
        self._start_monotonic = time.monotonic()
        self._sample_index = 0
        self._handle = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._handle)
        self._writer.writerow(
            [
                "sample_index",
                "time_s",
                "target_rpm",
                "actual_speed_rpm",
                "current_a",
                "speed_ok",
                "current_ok",
                "keepalive_enabled",
            ]
        )
        atomic_write_json(
            self.json_path,
            {
                "source": "spindle_control",
                "configuration": config.to_json(),
                "csv": self.csv_path.name,
            },
        )

    def write(
        self,
        timestamp_monotonic: float,
        reading: SpindleReading,
        target_rpm: int,
        keepalive_enabled: bool,
    ) -> None:
        time_s = timestamp_monotonic - self._start_monotonic
        self._writer.writerow(
            [
                self._sample_index,
                time_s,
                target_rpm,
                reading.speed_rpm,
                reading.current_a,
                reading.speed_ok,
                reading.current_ok,
                keepalive_enabled,
            ]
        )
        self._sample_index += 1

    def close(self) -> None:
        self._handle.flush()
        self._handle.close()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def string_value(payload: Any, key: str, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value.strip()


def int_value(payload: Any, key: str, default: int) -> int:
    if not isinstance(payload, dict):
        return default
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer.")
    return int(value)


def float_value(payload: Any, key: str, default: float) -> float:
    if not isinstance(payload, dict):
        return default
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number.")
    return float(value)
