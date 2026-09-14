import json
import os
from pathlib import Path
import subprocess
import sys

from pixcut.discovery import scan_usb, format_report


def write_fields(path, **fields):
    path.mkdir(parents=True, exist_ok=True)
    for name, value in fields.items():
        (path / name).write_text(str(value))


def device(root, name="1-2", vid="302c", pid="3101", address=11):
    path = root / name
    write_fields(
        path,
        idVendor=vid,
        idProduct=pid,
        busnum=1,
        devnum=address,
        manufacturer="Liene",
        product="PixCut S1",
        authorized=1,
    )
    for number, endpoints in [(0, ["01", "81"]), (2, ["06", "86"]), (3, ["04", "84"])]:
        interface = path / f"{name}:1.{number}"
        write_fields(
            interface,
            bInterfaceNumber=f"{number:02x}",
            bAlternateSetting=0,
            bInterfaceClass="ff",
        )
        for endpoint in endpoints:
            write_fields(interface / f"ep_{endpoint}", bEndpointAddress=endpoint)
        if number == 0:
            (interface / "driver").symlink_to("/sys/bus/usb/drivers/usblp")
        # sysfs also lists interfaces as sibling symlinks; do not double-count.
        (root / interface.name).symlink_to(interface)
    return path


def test_multiple_printers_interfaces_and_no_node_open(tmp_path):
    root, devfs = tmp_path / "sys", tmp_path / "dev"
    first = device(root)
    device(root, "1-3", address=12)
    write_fields(devfs / "001", **{"011": "not a USB handle"})
    result = scan_usb(root, devfs)
    assert result["status"] == "found" and result["matched_count"] == 2
    assert result["hardware_validated"] is False
    assert all(d["expected_endpoints_present"] for d in result["devices"])
    found = result["devices"][0]
    assert found["interfaces"][0]["driver"] == "usblp"
    assert found["interfaces"][1]["driver"] is None
    assert found["node_visible"] is True
    assert result["devices"][1]["node_visible"] is False
    assert "Found 2 PixCut" in format_report(result)
    # Discovery must not treat absent optional strings or absent drivers as failure.
    assert found["serial"] is None
    assert not result["warnings"]
    (first / "1-2:1.3" / "ep_84" / "bEndpointAddress").unlink()
    result = scan_usb(root, devfs)
    assert result["devices"][0]["expected_endpoints_present"] is False
    assert result["warnings"]


def test_no_match_all_devices_and_missing_sysfs(tmp_path):
    root = tmp_path / "sys"
    device(root, vid="1234")
    assert scan_usb(root)["devices"] == []
    assert scan_usb(root)["status"] == "not_found"
    result = scan_usb(root, include_all=True)
    assert len(result["devices"]) == 1
    assert not result["devices"][0]["matches_pixcut"]
    assert scan_usb(tmp_path / "missing")["status"] == "scan_unavailable"


def test_disappearing_or_malformed_descriptors(tmp_path, monkeypatch):
    root = tmp_path / "sys"
    path = device(root)
    (path / "devnum").write_text("gone")
    original = Path.read_text

    def read_text(p, *args, **kwargs):
        if p.name == "serial":
            raise PermissionError(13, "Permission denied")
        return original(p, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    result = scan_usb(root)
    assert result["matched_count"] == 1
    assert result["devices"][0]["device_node"] is None
    assert len(result["warnings"]) == 2


def test_cli_has_no_database_or_third_party_dependency(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "PIXCUT_DATA_DIR": str(tmp_path / "must-not-exist"),
    }
    result = subprocess.run(
        [sys.executable, "-S", "-m", "pixcut.cli", "self-test", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode in (0, 1, 2), result.stderr
    report = json.loads(result.stdout)
    assert report["read_only"] is True
    assert not (tmp_path / "must-not-exist").exists()
