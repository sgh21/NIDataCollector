from __future__ import annotations

import threading
from dataclasses import dataclass

from .config import AppConfig, SignalConfig
from .protocol import SerialProtocol


@dataclass(frozen=True)
class Reading:
    speed_rpm: float
    current_a: float
    speed_ok: bool
    current_ok: bool


class SpindleDevice:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.protocol = SerialProtocol(
            port=config.serial.port,
            baudrate=config.serial.baudrate,
            device_id=config.serial.device_id,
            timeout=config.serial.timeout,
            write_timeout=config.serial.write_timeout,
        )
        self._lock = threading.Lock()
        self._last_speed = config.speed.default
        self._last_current = config.current.default
        self._target_rpm = 0

    def connect(self) -> None:
        with self._lock:
            self.protocol.open()

    def close(self) -> None:
        with self._lock:
            self.protocol.close()

    def prepare_control_mode(self) -> None:
        with self._lock:
            self._prepare_control_mode_unlocked()

    def set_speed_rpm(self, rpm: float, prepare: bool = True) -> None:
        rpm_int = int(round(rpm))
        if not 0 <= rpm_int <= self.config.safety.max_rpm:
            raise ValueError(f"转速必须在 0 到 {self.config.safety.max_rpm} rpm 之间")
        c = self.config.control
        with self._lock:
            if prepare:
                self._prepare_control_mode_unlocked()
            if rpm_int > 0:
                self.protocol.write_value(c.run_enable_address, c.run_enable_value, c.run_enable_opcode)
                self.protocol.write_value(c.speed_setpoint_address, rpm_int, c.speed_setpoint_opcode)
            else:
                self._stop_unlocked()
            self._target_rpm = rpm_int

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()
            self._target_rpm = 0

    @property
    def target_rpm(self) -> int:
        return self._target_rpm

    def keepalive(self) -> bool:
        c = self.config.control
        with self._lock:
            if self._target_rpm <= 0:
                return False
            self.protocol.write_value(c.run_enable_address, c.run_enable_value, c.run_enable_opcode)
            self.protocol.write_value(c.speed_setpoint_address, self._target_rpm, c.speed_setpoint_opcode)
            return True

    def read(self) -> Reading:
        speed, speed_ok = self._read_signal(self.config.speed, self._last_speed)
        if abs(speed) < self.config.speed.deadband:
            speed = 0.0
        self._last_speed = speed

        current, current_ok = self._read_signal(self.config.current, self._last_current)
        self._last_current = current
        return Reading(speed_rpm=speed, current_a=current, speed_ok=speed_ok, current_ok=current_ok)

    def _read_signal(self, signal: SignalConfig, fallback: float) -> tuple[float, bool]:
        try:
            with self._lock:
                raw = self.protocol.read_value(signal.address, signal.response_id)
            return raw * signal.scale, True
        except Exception:
            return fallback, False

    def _prepare_control_mode_unlocked(self) -> None:
        c = self.config.control
        self.protocol.write_value(c.speed_setpoint_address, 0, c.speed_setpoint_opcode)
        self.protocol.write_value(c.run_enable_address, c.run_disable_value, c.run_enable_opcode)
        self.protocol.write_value(c.run_mode_address, c.run_mode_value, c.run_mode_opcode)

    def _stop_unlocked(self) -> None:
        c = self.config.control
        self.protocol.write_value(c.speed_setpoint_address, 0, c.speed_setpoint_opcode)
        self.protocol.write_value(c.run_enable_address, c.run_disable_value, c.run_enable_opcode)
        self.protocol.write_value(c.run_mode_address, c.run_mode_value, c.run_mode_opcode)
