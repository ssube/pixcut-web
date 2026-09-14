"""Explicit, bounded read-only property queries; never a production adapter."""

import json
import secrets
import time

PROTOCOL_COMMIT = "bc243ce63d9f458b818cb9e19b3c93f281b85f7a"
QUERIES = (
    ("identity", ("firmware-revision", "hardware-revision", "model", "sku")),
    ("media", ("serial-number", "media-size")),
    ("status", ("printer-state", "printer-sub-state", "printer-state-alerts")),
)
PREFIX = b"cmd json\n"
LIMIT = 65536


class Frames:
    def __init__(self):
        self.buffer = b""
        self.total = 0

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > LIMIT:
            raise ValueError("USB response exceeds the 64 KiB probe limit")
        self.buffer += bytes(chunk)
        messages = []
        while self.buffer:
            self.buffer = self.buffer.lstrip(b" \t\r\n\0")
            if not self.buffer:
                break
            if PREFIX.startswith(self.buffer):
                break
            if self.buffer.startswith(PREFIX):
                self.buffer = self.buffer[len(PREFIX) :]
                continue
            if not self.buffer.startswith(b"{"):
                raise ValueError("Unexpected USB response framing")
            try:
                text = self.buffer.decode("utf-8")
            except UnicodeDecodeError as exc:
                if exc.reason == "unexpected end of data":
                    break
                raise ValueError("Invalid UTF-8 USB response") from exc
            try:
                obj, end = json.JSONDecoder().raw_decode(text)
            except json.JSONDecodeError:
                break  # A fragmented JSON object may need another USB read.
            messages.append(obj)
            self.buffer = self.buffer[len(text[:end].encode("utf-8")) :]
        return messages


def query(device, properties, request_id, timeout_error, timeout_s=3):
    if properties not in [p for _, p in QUERIES]:
        raise ValueError("Only the fixed read-only property queries are permitted")
    request = {"id": request_id, "method": "get-prop", "params": list(properties)}
    wire = PREFIX + json.dumps(request, separators=(",", ":")).encode()
    if device.write(0x06, wire, timeout=1000) != len(wire):
        raise RuntimeError("Short USB command write; query was not repeated")
    frames = Frames()
    other = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            chunk = device.read(
                0x86,
                4096,
                timeout=max(1, min(500, int((deadline - time.monotonic()) * 1000))),
            )
        except timeout_error:
            continue
        matched = None
        for message in frames.feed(chunk):
            if message.get("id") == request_id:
                if "error" in message or "result" not in message:
                    raise RuntimeError(f"Printer rejected property query: {message}")
                matched = message
            else:
                other.append(message)
        if matched is not None:
            return {"request": request, "response": matched, "other_messages": other}
    raise TimeoutError(
        f"No correlated response to read-only request {request_id}; no retry sent"
    )


def probe_device(device, core, util):
    """Claim only the already-configured command interface; never change settings."""
    claimed = False
    results = {}
    try:
        device.default_timeout = 1000
        configuration = device.get_active_configuration()
        interface = configuration[(2, 0)]
        endpoints = {e.bEndpointAddress: e.bmAttributes & 3 for e in interface}
        if endpoints.get(0x06) != 2 or endpoints.get(0x86) != 2:
            raise RuntimeError(
                "Command interface does not expose the expected bulk endpoints"
            )
        if device.is_kernel_driver_active(2):
            raise RuntimeError(
                "Command interface 2 has a kernel driver; probe will not detach it"
            )
        util.claim_interface(device, 2)
        claimed = True
        initial_id = secrets.randbelow(1_000_000) + 1000
        for offset, (name, properties) in enumerate(QUERIES):
            results[name] = query(
                device, properties, initial_id + offset, core.USBTimeoutError
            )
        return results
    finally:
        try:
            if claimed:
                util.release_interface(device, 2)
        finally:
            util.dispose_resources(device)


def run_probe():
    from datetime import datetime, timezone
    from .discovery import scan_usb
    from .db import data_dir
    from .worker import worker_lock

    report = {
        "schema_version": "1.0",
        "read_only": True,
        "hardware_validated": False,
        "protocol_reference_commit": PROTOCOL_COMMIT,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
    }
    try:
        import usb.core
        import usb.util

        root = data_dir()
        root.mkdir(parents=True, exist_ok=True)
        with worker_lock(root):
            discovery = scan_usb()
            candidates = discovery["devices"]
            if len(candidates) != 1:
                raise RuntimeError(
                    "Probe requires exactly one enumerated PixCut S1; no device was selected"
                )
            selected = candidates[0]
            report["device"] = selected
            device = usb.core.find(
                idVendor=0x302C,
                idProduct=0x3101,
                bus=selected["bus"],
                address=selected["address"],
            )
            if device is None:
                raise RuntimeError(
                    "Selected PixCut disappeared or is not visible to libusb"
                )
            report["queries"] = probe_device(device, usb.core, usb.util)
            report["status"] = "responded"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def probe_cli(json_output=False):
    report = run_probe()
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        print("PixCut read-only USB identity/status probe:", report["status"])
        for name, value in report.get("queries", {}).items():
            print(f"  {name}: {json.dumps(value['response']['result'])}")
        if "error" in report:
            print("  " + report["error"])
        print(
            "No print job, media feed, cut command, reset, or configuration change was requested."
        )
    return 0 if report["status"] == "responded" else 1
