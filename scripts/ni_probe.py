from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nidata_collector.hardware.ni import get_system_snapshot, reserve_network_devices, unreserve_network_devices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List NI-DAQmx devices and physical channels.")
    parser.add_argument(
        "--no-reserve",
        action="store_true",
        help="Do not reserve TCP/IP NI devices before listing modules/channels.",
    )
    parser.add_argument(
        "--override-reservation",
        action="store_true",
        help="Override an existing network device reservation.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--keep-reservation",
        action="store_true",
        help="Keep network device reservation after probing.",
    )
    return parser


def print_text(snapshot: dict) -> None:
    driver = snapshot["driver_version"]
    print(f"NI-DAQmx driver: {driver}")

    reservations = snapshot.get("reservations", [])
    if reservations:
        print("Network reservations:")
        for item in reservations:
            status = "OK" if item["ok"] else "FAILED"
            message = f": {item['message']}" if item["message"] else ""
            print(f"  - {item['device']}: {status}{message}")

    print("Devices:")
    for device in snapshot["devices"]:
        print(f"  - {device['name']} ({device['product_type']})")
        if device.get("bus_type"):
            print(f"    bus: {device['bus_type']}")
        if device.get("tcpip_hostname"):
            print(f"    hostname: {device['tcpip_hostname']}")
        if device.get("tcpip_ethernet_ip"):
            print(f"    ip: {device['tcpip_ethernet_ip']}")
        if device.get("chassis"):
            print(f"    chassis: {device['chassis']}")
        if device.get("slot") not in (None, ""):
            print(f"    slot: {device['slot']}")
        if device["modules"]:
            print(f"    modules: {', '.join(device['modules'])}")
        if device["ai_channels"]:
            print(f"    ai: {', '.join(device['ai_channels'])}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    reservations = []
    reserved_devices = []
    if not args.no_reserve:
        reservations = reserve_network_devices(override=args.override_reservation)
        reserved_devices = [item.device for item in reservations if item.ok]

    try:
        snapshot = get_system_snapshot()
        snapshot["reservations"] = [item.__dict__ for item in reservations]

        if args.json:
            print(json.dumps(snapshot, indent=2))
        else:
            print_text(snapshot)
    finally:
        if reserved_devices and not args.keep_reservation:
            releases = unreserve_network_devices(reserved_devices)
            if not args.json:
                print("Network releases:")
                for item in releases:
                    status = "OK" if item.ok else "FAILED"
                    message = f": {item.message}" if item.message else ""
                    print(f"  - {item.device}: {status}{message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
