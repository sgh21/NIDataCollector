from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any


@dataclass(frozen=True)
class ReservationResult:
    device: str
    ok: bool
    message: str = ""


def _stringify(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value)


def _safe_get(obj: Any, attr: str) -> Any:
    try:
        return _stringify(getattr(obj, attr))
    except Exception:
        return None


def _safe_names(collection: Any) -> list[str]:
    try:
        return [item.name for item in collection]
    except Exception:
        return []


def reserve_network_devices(override: bool = False) -> list[ReservationResult]:
    import nidaqmx.system
    from nidaqmx.constants import BusType

    results: list[ReservationResult] = []
    system = nidaqmx.system.System.local()
    for device in list(system.devices):
        try:
            is_tcpip = device.bus_type == BusType.TCPIP
        except Exception:
            is_tcpip = False

        if not is_tcpip:
            continue

        try:
            device.reserve_network_device(override)
            results.append(ReservationResult(device=device.name, ok=True))
        except Exception as exc:
            results.append(
                ReservationResult(device=device.name, ok=False, message=f"{type(exc).__name__}: {exc}")
            )

    return results


def unreserve_network_devices(device_names: Iterable[str] | None = None) -> list[ReservationResult]:
    import nidaqmx.system
    from nidaqmx.constants import BusType

    requested = set(device_names or [])
    results: list[ReservationResult] = []
    system = nidaqmx.system.System.local()
    for device in list(system.devices):
        if requested and device.name not in requested:
            continue

        try:
            is_tcpip = device.bus_type == BusType.TCPIP
        except Exception:
            is_tcpip = False

        if not is_tcpip:
            continue

        try:
            device.unreserve_network_device()
            results.append(ReservationResult(device=device.name, ok=True))
        except Exception as exc:
            results.append(
                ReservationResult(device=device.name, ok=False, message=f"{type(exc).__name__}: {exc}")
            )

    return results


def get_system_snapshot() -> dict:
    import nidaqmx.system

    system = nidaqmx.system.System.local()
    driver = system.driver_version
    devices = []

    for device in system.devices:
        devices.append(
            {
                "name": device.name,
                "product_type": _safe_get(device, "product_type"),
                "product_num": _safe_get(device, "product_num"),
                "serial_num": _safe_get(device, "serial_num"),
                "bus_type": _safe_get(device, "bus_type"),
                "tcpip_hostname": _safe_get(device, "tcpip_hostname"),
                "tcpip_ethernet_ip": _safe_get(device, "tcpip_ethernet_ip"),
                "chassis": _safe_get(device, "compact_daq_chassis_device"),
                "slot": _safe_get(device, "compact_daq_slot_num"),
                "modules": _safe_names(_safe_collection(device, "chassis_module_devices")),
                "ai_channels": _safe_names(_safe_collection(device, "ai_physical_chans")),
            }
        )

    return {
        "driver_version": f"{driver.major_version}.{driver.minor_version}.{driver.update_version}",
        "devices": devices,
    }


def _safe_collection(obj: Any, attr: str) -> Any:
    try:
        return getattr(obj, attr)
    except Exception:
        return []


def find_ai_channels(product_type_hint: str = "") -> list[str]:
    snapshot = get_system_snapshot()
    hint = product_type_hint.lower()
    candidates = []

    for device in snapshot["devices"]:
        ai_channels = device["ai_channels"]
        if not ai_channels:
            continue
        product_type = str(device.get("product_type") or "").lower()
        if hint and hint not in product_type:
            candidates.extend(ai_channels)
            continue
        return ai_channels

    return candidates
