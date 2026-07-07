from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "spindle_gui_config.json"


def parse_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


@dataclass
class SerialConfig:
    port: str = "COM4"
    baudrate: int = 19200
    device_id: int = 1
    timeout: float = 0.35
    write_timeout: float = 0.5


@dataclass
class ControlConfig:
    speed_setpoint_address: int = 0x0008
    speed_setpoint_opcode: int = 0x77
    run_enable_address: int = 0x0501
    run_enable_opcode: int = 0x77
    run_enable_value: int = 111
    run_disable_value: int = -111
    run_mode_address: int = 0x0505
    run_mode_opcode: int = 0x57
    run_mode_value: int = 0


@dataclass
class SignalConfig:
    address: int
    response_id: int
    scale: float = 1.0
    default: float = 0.0
    deadband: float = 0.0


@dataclass
class SafetyConfig:
    min_run_rpm: int = 0
    max_rpm: int = 24000


@dataclass
class UiConfig:
    poll_interval_ms: int = 100
    keepalive_interval_ms: int = 200
    plot_window_seconds: int = 30


@dataclass
class AppConfig:
    serial: SerialConfig
    control: ControlConfig
    speed: SignalConfig
    current: SignalConfig
    safety: SafetyConfig
    ui: UiConfig

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        serial = raw.get("serial", {})
        control = raw.get("control", {})
        signals = raw.get("signals", {})
        safety = raw.get("safety", {})
        ui = raw.get("ui", {})

        return cls(
            serial=SerialConfig(
                port=serial.get("port", "COM4"),
                baudrate=int(serial.get("baudrate", 19200)),
                device_id=int(serial.get("device_id", 1)),
                timeout=float(serial.get("timeout", 0.35)),
                write_timeout=float(serial.get("write_timeout", 0.5)),
            ),
            control=ControlConfig(
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
            speed=_signal(signals.get("speed", {}), "0x3008", "0x02", 0.01),
            current=_signal(signals.get("current", {}), "0x3002", "0x03", 0.01),
            safety=SafetyConfig(
                min_run_rpm=int(safety.get("min_run_rpm", 0)),
                max_rpm=int(safety.get("max_rpm", 24000)),
            ),
            ui=UiConfig(
                poll_interval_ms=int(ui.get("poll_interval_ms", 100)),
                keepalive_interval_ms=int(ui.get("keepalive_interval_ms", 200)),
                plot_window_seconds=int(ui.get("plot_window_seconds", 30)),
            ),
        )

    def save(self, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
        Path(path).write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    def to_json(self) -> dict[str, Any]:
        return {
            "serial": {
                "port": self.serial.port,
                "baudrate": self.serial.baudrate,
                "device_id": self.serial.device_id,
                "timeout": self.serial.timeout,
                "write_timeout": self.serial.write_timeout,
            },
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
                "speed": _signal_json(self.speed),
                "current": _signal_json(self.current),
            },
            "safety": {
                "min_run_rpm": self.safety.min_run_rpm,
                "max_rpm": self.safety.max_rpm,
            },
            "ui": {
                "poll_interval_ms": self.ui.poll_interval_ms,
                "keepalive_interval_ms": self.ui.keepalive_interval_ms,
                "plot_window_seconds": self.ui.plot_window_seconds,
            },
        }


def _signal(raw: dict[str, Any], default_address: str, default_response_id: str, default_scale: float) -> SignalConfig:
    return SignalConfig(
        address=parse_int(raw.get("address", default_address)),
        response_id=parse_int(raw.get("response_id", default_response_id)),
        scale=float(raw.get("scale", default_scale)),
        default=float(raw.get("default", 0.0)),
        deadband=float(raw.get("deadband", 0.0)),
    )


def _signal_json(signal: SignalConfig) -> dict[str, Any]:
    return {
        "address": hex(signal.address),
        "response_id": hex(signal.response_id),
        "scale": signal.scale,
        "default": signal.default,
        "deadband": signal.deadband,
    }
