from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from PIL import Image
import numpy as np
import pytest
from sqlalchemy import update

from pixcut import db, photo
from pixcut.api import create_app
from pixcut.cli import migrate
from pixcut.printcut import HardwareWorker


def settings(**extra):
    return {
        "crop": {"x": 0, "y": 0, "width": 1, "height": 1},
        "layout": "single",
        "orientation": "portrait",
        "framing": "fill",
        "preset": "original",
        "film_look": "none",
        "adjustments": dict(zero_adjustments()),
        **extra,
    }


def zero_adjustments():
    return {
        "exposure": 0,
        "contrast": 0,
        "highlights": 0,
        "shadows": 0,
        "temperature": 0,
        "tint": 0,
        "saturation": 0,
        "sharpening": 0,
        "grain": 0,
    }


def test_free_aspect_framing_orientation_and_payload_limit():
    source = Image.new("RGB", (100, 100), "red")
    contained = photo.render(source, settings(framing="contain"), preview=True)
    assert contained.size == (480, 720)
    assert contained.getpixel((0, 0)) == (255, 255, 255)
    assert contained.getpixel((240, 360)) == (255, 0, 0)
    filled = photo.render(source, settings(), preview=True)
    assert filled.getpixel((0, 0)) == (255, 0, 0)
    assert photo.resolved_orientation(settings(orientation="auto")) == "landscape"
    assert photo.resolved_orientation(settings(orientation="portrait")) == "portrait"
    analog = settings(
        film_look="warm_negative",
        adjustments={**zero_adjustments(), "grain": 55},
    )
    assert photo.render(source, analog, preview=True).tobytes() == photo.render(
        source, analog, preview=True
    ).tobytes()

    noise = np.random.default_rng(42).integers(
        0, 256, size=(1800, 1200, 3), dtype=np.uint8
    )
    with pytest.raises(ValueError, match="Lower Print quality"):
        photo.exact_jpeg(Image.fromarray(noise, "RGB"), settings())

    smooth = np.zeros((1800, 1200, 3), dtype=np.uint8)
    smooth[:, :, 0] = np.arange(1200, dtype=np.uint16) * 255 // 1199
    smooth[:, :, 1] = (np.arange(1800, dtype=np.uint16) * 255 // 1799)[:, None]
    detailed = Image.fromarray(smooth, "RGB")
    low = photo.exact_jpeg(detailed, settings(print_quality=60))
    high = photo.exact_jpeg(detailed, settings(print_quality=95))
    assert len(low) < len(high)


def test_yearbook_and_photobooth_sheet_layouts():
    source = Image.new("RGB", (100, 150), "red")

    yearbook = photo.render(
        source, settings(layout="yearbook_2x3", orientation="landscape"), preview=True
    )
    assert yearbook.size == (480, 720)
    assert photo.resolved_orientation(
        settings(layout="yearbook_2x3", orientation="landscape")
    ) == "portrait"
    assert yearbook.getpixel((0, 0)) == (255, 0, 0)

    mini = photo.render(
        source, settings(layout="yearbook_1_5x2"), preview=True
    )
    assert mini.size == (480, 720)
    assert mini.getpixel((0, 100)) == (255, 255, 255)
    assert mini.getpixel((70, 100)) == (255, 0, 0)
    assert mini.getpixel((410, 100)) == (255, 0, 0)
    assert mini.getpixel((479, 100)) == (255, 255, 255)

    gradient = Image.new("RGB", (120, 90))
    gradient.putdata(
        [(x * 2, y * 2, (x + y) % 256) for y in range(90) for x in range(120)]
    )
    booth_settings = settings(
        layout="photobooth_2x6",
        film_look="faded_print",
        adjustments={**zero_adjustments(), "grain": 40},
    )
    booth = photo.render(gradient, booth_settings, preview=True)
    assert booth.size == (480, 720)
    assert booth.crop((0, 0, 240, 720)).tobytes() == booth.crop(
        (240, 0, 480, 720)
    ).tobytes()
    assert booth.crop((0, 0, 240, 180)).tobytes() == booth.crop(
        (0, 180, 240, 360)
    ).tobytes()
    assert photo.layout_spec(booth_settings) == {
        "id": "photobooth_2x6",
        "copies": 2,
        "strip_size_in": [2, 6],
        "frames_per_strip": 4,
        "frame_size_in": [2, 1.5],
    }
    assert photo.render(gradient, booth_settings).size == (1200, 1800)


def upload(client):
    image = Image.new("RGB", (300, 180))
    image.putdata(
        [
            (round(x / 299 * 255), round(y / 179 * 255), 100)
            for y in range(180)
            for x in range(300)
        ]
    )
    raw = BytesIO()
    image.save(raw, "PNG")
    response = client.post(
        "/api/v1/assets",
        files={"file": ("gradient.png", raw.getvalue(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_project(client, asset):
    response = client.post(
        "/api/v1/photo-projects",
        json={"name": "Garden", "asset_id": asset["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_photo_crop_preview_presets_revision_and_exact_artifacts(client):
    asset = upload(client)
    project = create_project(client, asset)
    assert project["kind"] == "photo"
    assert project["layout"] == "single"
    assert project["framing"] == "fill"
    assert project["print_quality"] == 95
    assert client.get("/api/v1/projects?kind=photo").json()[0]["id"] == project["id"]
    assert client.get("/api/v1/projects?kind=sticker").json() == []

    settings = {
        "crop": {"x": 0.1, "y": 0.1, "width": 0.7, "height": 0.6},
        "orientation": "auto",
        "framing": "contain",
        "preset": "auto_balance",
        "adjustments": {
            "exposure": 0.2,
            "contrast": 5,
            "highlights": -10,
            "shadows": 12,
            "temperature": 4,
            "tint": -3,
            "saturation": 6,
            "sharpening": 10,
            "grain": 12,
        },
        "film_look": "warm_negative",
        "print_quality": 92,
    }
    first = client.post(
        f"/api/v1/photo-projects/{project['id']}/draft-preview", json=settings
    )
    second = client.post(
        f"/api/v1/photo-projects/{project['id']}/draft-preview", json=settings
    )
    assert first.status_code == 200 and first.content == second.content
    preview = Image.open(BytesIO(first.content))
    assert preview.mode == "RGB" and preview.size == (720, 480)
    assert client.post(
        f"/api/v1/photo-projects/{project['id']}/draft-preview",
        json={**settings, "crop": {"x": 0.8, "y": 0, "width": 0.3, "height": 1}},
    ).status_code == 422
    assert client.post(
        f"/api/v1/photo-projects/{project['id']}/draft-preview",
        json={**settings, "print_quality": 59},
    ).status_code == 422

    saved = client.patch(
        f"/api/v1/photo-projects/{project['id']}",
        json={
            "name": project["name"],
            "asset_id": asset["id"],
            **settings,
            "expected_revision": 1,
        },
    )
    assert saved.status_code == 200
    saved = saved.json()
    assert saved["revision"] == 2
    assert client.patch(
        f"/api/v1/photo-projects/{project['id']}",
        json={
            "name": project["name"],
            "asset_id": asset["id"],
            **settings,
            "expected_revision": 1,
        },
    ).status_code == 409

    rendered = client.post(
        f"/api/v1/photo-projects/{project['id']}/renders", json={"revision": 2}
    )
    assert rendered.status_code == 201, rendered.text
    rendered = rendered.json()
    files = rendered["pages"][0]["artifacts"]
    assert rendered["profile"]["id"] == "photo-4x6-jpeg"
    assert set(files) == {
        "image.jpg",
        "thumbnail.png",
        "cut-geometry.json",
        "overlay.svg",
        "manifest.json",
    }
    exact = client.get(
        f"/api/v1/renders/{rendered['id']}/artifacts/0/image.jpg"
    ).content
    decoded = Image.open(BytesIO(exact))
    assert decoded.mode == "RGB" and decoded.size == (1200, 1800)
    geometry = json.loads(
        client.get(
            f"/api/v1/renders/{rendered['id']}/artifacts/0/cut-geometry.json"
        ).content
    )
    assert geometry["shapes"] == []
    manifest = json.loads(
        client.get(
            f"/api/v1/renders/{rendered['id']}/artifacts/0/manifest.json"
        ).content
    )
    assert manifest["source_asset_id"] == asset["id"]
    assert manifest["project_revision"] == 2
    assert manifest["adjustment_pipeline_version"] == photo.PIPELINE_VERSION
    assert manifest["layout"] == "single"
    assert manifest["sheet_layout"]["photo_size_in"] == [4, 6]
    assert manifest["film_look"] == "warm_negative"
    assert manifest["print_quality"] == 92
    assert manifest["encoder"] == {
        "format": "JPEG",
        "quality": 92,
        "subsampling": 0,
        "optimize": True,
        "dpi": [300, 300],
    }
    assert manifest["adjustments"]["grain"] == 12


def test_photo_hardware_job_is_one_copy_print_only_and_idempotent(tmp_path, monkeypatch):
    migrate(tmp_path)
    monkeypatch.setenv("PIXCUT_EXECUTION_MODE", "hardware")
    monkeypatch.setattr(
        "pixcut.discovery.scan_usb",
        lambda: {"matched_count": 1, "devices": []},
    )
    app = create_app(tmp_path)
    with TestClient(app) as client:
        project = create_project(client, upload(client))
        render = client.post(
            f"/api/v1/photo-projects/{project['id']}/renders", json={"revision": 1}
        ).json()
        payload = {
            "render_id": render["id"],
            "printer_id": "pixcut-s1-usb",
            "mode": "print_only",
            "pages": [0],
            "copies": 1,
            "accepted_profile_id": "photo-4x6-jpeg",
            "confirmed": True,
            "confirmed_media": "4x6 photo paper",
            "idempotency_key": "hardware-photo-key",
            "name": "Garden",
        }
        unavailable = client.post("/api/v1/jobs", json=payload)
        assert unavailable.status_code == 409
        assert unavailable.json()["code"] == "PRINTER_NOT_READY"
        assert client.get("/api/v1/printers").json()[0]["status"] == "worker_offline"
        app.state.service.worker_heartbeat("hardware", "idle")
        available = client.get("/api/v1/printers").json()[0]
        assert available["ready"] is True and available["status"] == "ready"
        job = client.post("/api/v1/jobs", json=payload)
        assert job.status_code == 201, job.text
        assert job.json()["state"] == "queued"
        assert client.post("/api/v1/jobs", json=payload).json()["id"] == job.json()["id"]
        assert client.get("/api/v1/jobs").json()["queue_length"] == 1
        assert HardwareWorker(app.state.service).next_job()["id"] == job.json()["id"]
        assert client.post("/api/v1/jobs", json={**payload, "copies": 2}).status_code == 422
        assert client.post(
            "/api/v1/jobs", json={**payload, "confirmed_media": None}
        ).status_code == 422
        profiles = client.get("/api/v1/printers").json()[0]["profiles"]
        assert [item["id"] for item in profiles] == [
            "photo-4x6-jpeg",
            "sticker-4x7-jpeg",
        ]

        current = job.json()
        sheet = current["sheets"][0]
        with app.state.service.engine.begin() as connection:
            connection.execute(
                update(db.sheets)
                .where(db.sheets.c.id == sheet["id"])
                .values(
                    state="uncertain",
                    evidence={
                        "recovery": {
                            "action": "power_cycle_printer",
                            "automatic_retry": False,
                        }
                    },
                )
            )
            app.state.service.event(
                connection,
                current["id"],
                "uncertain",
                sheet["id"],
                "device",
            )
            app.state.service.aggregate(connection, current["id"])
        uncertain = app.state.service.job(current["id"])
        resolution = {
            "sheet_id": sheet["id"],
            "outcome": "failed",
            "expected_event_id": uncertain["last_event_id"],
            "note": "No sheet printed",
        }
        missing_confirmation = client.post(
            f"/api/v1/jobs/{current['id']}/resolutions", json=resolution
        )
        assert missing_confirmation.status_code == 409
        assert (
            missing_confirmation.json()["code"]
            == "RECOVERY_CONFIRMATION_REQUIRED"
        )
        resolved = client.post(
            f"/api/v1/jobs/{current['id']}/resolutions",
            json={**resolution, "printer_restarted": True},
        )
        assert resolved.status_code == 200
        evidence = resolved.json()["sheets"][0]["evidence"]
        assert evidence["printer_restarted"] is True
    app.state.service.engine.dispose()


def test_photo_matrix_script_generates_reproducible_reports(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (40, 60), "orange").save(source)
    output = tmp_path / "matrix"
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(root / "scripts/photo-test-matrix.py"),
        str(source),
        "--output",
        str(output),
        "--sizes",
        "300x450,1200x1800",
        "--qualities",
        "70,90",
        "--formats",
        "jpeg,png",
    ]
    subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    first = json.loads((output / "matrix.json").read_text())
    subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    second = json.loads((output / "matrix.json").read_text())
    assert first == second
    assert len(first["points"]) == 7
    assert [item["bytes"] for item in first["points"]] == sorted(
        (item["bytes"] for item in first["points"]), reverse=True
    )
    assert first["order"] == "payload_bytes_descending"
    assert {item["format"] for item in first["points"]} == {"jpeg", "png"}
    assert all(
        item["status"] == "within_1_mib"
        for item in first["points"]
        if item["format"] == "jpeg"
    )
    assert all(
        item["status"] == "under_2_mib"
        for item in first["points"]
        if item["format"] == "png"
    )
    assert {item["png_strategy"] for item in first["points"] if item["format"] == "png"} == {
        "filtered",
        "huffman",
        "rle",
    }
    assert (output / "matrix.csv").is_file()
