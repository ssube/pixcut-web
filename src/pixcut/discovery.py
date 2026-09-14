"""Read Linux USB enumeration metadata without opening or claiming a device."""

import os
from pathlib import Path

PIXCUT_ID = ("302c", "3101")
EXPECTED_ENDPOINTS = {2: {"06", "86"}, 3: {"04", "84"}}


def scan_usb(
    sysfs=Path("/sys/bus/usb/devices"), devfs=Path("/dev/bus/usb"), include_all=False
):
    """Return an enumeration report, not a hardware readiness/printing assertion.

    Injectable roots support recorded fixtures. Only sysfs text and symlink targets
    are read; usbfs device nodes are inspected with exists/access, never opened.
    Hot-unplug and missing optional descriptor strings are tolerated.
    """
    report = {
        "schema_version": "1.0",
        "scan": "linux-sysfs",
        "read_only": True,
        "devices": [],
        "warnings": [],
        "hardware_validated": False,
    }
    try:
        entries = sorted(sysfs.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        report.update(
            status="scan_unavailable", error=f"Cannot enumerate {sysfs}: {exc.strerror}"
        )
        return report

    def read(path, required=False):
        try:
            return path.read_text().strip()
        except OSError as exc:
            if required or not isinstance(exc, FileNotFoundError):
                report["warnings"].append(
                    f"Cannot read {path}: {exc.strerror}; device may have disconnected"
                )
            return None

    def number(path, base=10):
        value = read(path, required=True)
        try:
            return int(value, base) if value is not None else None
        except ValueError:
            report["warnings"].append(f"Invalid numeric descriptor at {path}")
            return None

    def driver(path):
        try:
            return Path(os.readlink(path / "driver")).name
        except FileNotFoundError:
            return None
        except OSError as exc:
            report["warnings"].append(
                f"Cannot inspect driver at {path}: {exc.strerror}"
            )
            return "unknown"

    for entry in entries:
        if ":" in entry.name:
            continue  # Interfaces are reported under their parent physical device.
        vendor, product = read(entry / "idVendor"), read(entry / "idProduct")
        if vendor is None or product is None:
            continue
        vendor, product = vendor.lower(), product.lower()
        matched = (vendor, product) == PIXCUT_ID
        if not matched and not include_all:
            continue
        bus, address = number(entry / "busnum"), number(entry / "devnum")
        node = (
            devfs / f"{bus:03d}" / f"{address:03d}"
            if bus is not None and address is not None
            else None
        )
        device = {
            "vendor_id": vendor,
            "product_id": product,
            "matches_pixcut": matched,
            "manufacturer": read(entry / "manufacturer"),
            "product": read(entry / "product"),
            "serial": read(entry / "serial"),
            "usb_device_release": read(entry / "bcdDevice"),
            "sysfs_path": str(entry),
            "port_path": entry.name,
            "bus": bus,
            "address": address,
            "authorized": read(entry / "authorized"),
            "device_driver": driver(entry),
            "device_node": str(node) if node else None,
            "node_visible": node.exists() if node else False,
            "process_read_access": os.access(node, os.R_OK) if node else False,
            "process_write_access": os.access(node, os.W_OK) if node else False,
            "interfaces": [],
        }
        try:
            interfaces = sorted(entry.glob(f"{entry.name}:*"))
            for interface in interfaces:
                endpoints = []
                for endpoint in sorted(interface.glob("ep_*")):
                    value = read(endpoint / "bEndpointAddress", required=True)
                    if value:
                        endpoints.append(value.lower().removeprefix("0x"))
                device["interfaces"].append(
                    {
                        "number": number(interface / "bInterfaceNumber", 16),
                        "alternate_setting": number(interface / "bAlternateSetting"),
                        "class": read(interface / "bInterfaceClass"),
                        "driver": driver(interface),
                        "endpoints": endpoints,
                    }
                )
        except OSError as exc:
            report["warnings"].append(
                f"Cannot enumerate interfaces for {entry}: {exc.strerror}"
            )
        observed = {i["number"]: set(i["endpoints"]) for i in device["interfaces"]}
        device["expected_endpoints_present"] = (
            all(
                endpoints <= observed.get(index, set())
                for index, endpoints in EXPECTED_ENDPOINTS.items()
            )
            if matched
            else None
        )
        report["devices"].append(device)
    report["matched_count"] = sum(d["matches_pixcut"] for d in report["devices"])
    report["status"] = "found" if report["matched_count"] else "not_found"
    return report


def format_report(report):
    lines = ["PixCut USB self-test — read-only OS enumeration"]
    if report["status"] == "scan_unavailable":
        lines.append(report["error"])
    else:
        lines.append(f"Found {report['matched_count']} PixCut S1 device(s).")
    for device in report["devices"]:
        label = device["product"] or "Unnamed USB device"
        lines.extend(
            [
                "",
                f"{label} [{device['vendor_id']}:{device['product_id']}]"
                + (" — PixCut match" if device["matches_pixcut"] else ""),
                f"  Manufacturer: {device['manufacturer'] or 'unavailable'}; serial: {device['serial'] or 'unavailable'}",
                f"  Port: {device['port_path']}; node: {device['device_node'] or 'unavailable'}",
                f"  Device driver: {device['device_driver'] or 'none'}; authorized: {device['authorized'] or 'unknown'}",
                f"  Node visible to this process: {device['node_visible']}; read access: {device['process_read_access']}; write access: {device['process_write_access']}",
            ]
        )
        for interface in device["interfaces"]:
            lines.append(
                f"  Interface {interface['number']}: driver={interface['driver'] or 'unbound'}, "
                f"endpoints={', '.join('0x' + ep for ep in interface['endpoints']) or 'unavailable'}"
            )
        if device["matches_pixcut"]:
            lines.append(
                f"  Expected command/data endpoint addresses present: {device['expected_endpoints_present']}"
            )
    lines.extend(f"Warning: {warning}" for warning in report["warnings"])
    if report["status"] == "not_found":
        lines.append(
            "Check USB power/cabling and host visibility; use --all to list other enumerated USB devices."
        )
    lines.append(
        "No device was opened, claimed, reset, or sent commands. Enumeration does not validate USB printing."
    )
    return "\n".join(lines)


def self_test(include_all=False, json_output=False):
    import json

    report = scan_usb(include_all=include_all)
    print(json.dumps(report, indent=2) if json_output else format_report(report))
    return {"found": 0, "not_found": 1, "scan_unavailable": 2}[report["status"]]
