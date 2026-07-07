from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Optional


class SpindleProtocolError(Exception):
    pass


@dataclass(frozen=True)
class ValueResponse:
    response_id: int
    value: int
    raw: bytes


def checksum(frame_without_checksum: bytes) -> int:
    data = frame_without_checksum[1:] if frame_without_checksum and frame_without_checksum[0] == 0xAA else frame_without_checksum
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


class SerialProtocol:
    def __init__(
        self,
        port: str,
        baudrate: int,
        device_id: int,
        timeout: float,
        write_timeout: float,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.device_id = device_id
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._serial = None

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def open(self) -> None:
        if self.is_open:
            return
        import serial

        self._serial = serial.Serial(
            self.port,
            self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self.timeout,
            write_timeout=self.write_timeout,
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
        assert self._serial is not None
        self._serial.write(build_write_frame(address, value, opcode, self.device_id))
        self._serial.flush()

    def read_value(self, address: int, expected_response_id: Optional[int]) -> int:
        self.open()
        assert self._serial is not None
        self.clear()
        self._serial.write(build_read_frame(address, self.device_id))
        self._serial.flush()

        deadline = time.monotonic() + self.timeout
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

    def _read_one_frame(self, deadline: float) -> Optional[bytes]:
        assert self._serial is not None
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
