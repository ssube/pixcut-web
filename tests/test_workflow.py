from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select
from pixcut import db
from pixcut.api import create_app, trusted_hosts
from pixcut.artifacts import digest
from pixcut.cli import migrate
from pixcut.worker import SimulatorWorker, worker_lock
from fastapi.testclient import TestClient


def test_trusted_hosts_adds_only_explicit_lan_entries(monkeypatch):
    monkeypatch.setenv(
        "PIXCUT_TRUSTED_HOSTS", "192.168.1.50, pixcut.local,192.168.1.50"
    )
    assert trusted_hosts() == [
        "127.0.0.1",
        "localhost",
        "[::1]",
        "testserver",
        "192.168.1.50",
        "pixcut.local",
    ]


def post(client, path, body=None):
    response = client.post("/api/v1" + path, json=body)
    assert response.status_code < 300, response.text
    return response.json()


def fixture_project(client, quantity=8):
    image = Image.new("RGBA", (40, 60), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 19, 29), fill=(255, 0, 0, 255))
    draw.rectangle((20, 30, 39, 59), fill=(0, 0, 255, 255))
    raw = BytesIO()
    image.save(raw, "PNG")
    raw = raw.getvalue()
    response = client.post(
        "/api/v1/assets", files={"file": ("original.png", raw, "image/png")}
    )
    assert response.status_code == 201, response.text
    asset = response.json()
    sticker = post(
        client, "/stickers", {"asset_id": asset["id"], "name": "Asymmetric target"}
    )
    project = post(
        client,
        "/projects",
        {"name": "Targets", "bindings": {sticker["id"]: 1}, "pages": []},
    )
    proposal = post(
        client,
        f"/projects/{project['id']}/layouts",
        {
            "revision": 1,
            "sticker_id": sticker["id"],
            "mode": "quantity",
            "quantity": quantity,
            "width_mm": 20,
            "height_mm": 30,
        },
    )
    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": project["name"],
            "bindings": project["bindings"],
            "pages": proposal["pages"],
            "expected_revision": 1,
        },
    )
    assert response.status_code == 200, response.text
    project = response.json()
    op = post(
        client, f"/projects/{project['id']}/renders", {"revision": project["revision"]}
    )
    render = client.get(f"/api/v1/renders/{op['render_id']}").json()
    return raw, asset, sticker, project, render


def job_request(render, **extra):
    return {
        "render_id": render["id"],
        "pages": [p["index"] for p in render["pages"]],
        "copies": 1,
        "accepted_profile_id": "simulator-v1",
        "confirmed": True,
        "idempotency_key": "initial-job-key",
        **extra,
    }


def artifact(client, render, name, page=0):
    response = client.get(f"/api/v1/renders/{render['id']}/artifacts/{page}/{name}")
    assert response.status_code == 200, response.text
    return response.content


def test_first_demonstration_and_exact_reprint(client):
    raw, asset, sticker, project, render = fixture_project(client, 21)
    assert len(project["pages"]) == 2
    assert [len(p["placements"]) for p in project["pages"]] == [20, 1]
    assert all(
        p["width_mm"] == 20 for page in project["pages"] for p in page["placements"]
    )
    assert digest(raw) == asset["original"]["hash"]
    assert client.get(f"/api/v1/assets/{asset['id']}/original").content == raw
    original_files = {
        name: artifact(client, render, name) for name in render["pages"][0]["artifacts"]
    }
    pixels = Image.open(BytesIO(original_files["image.png"]))
    assert pixels.mode == "RGB" and pixels.size == (1200, 2100)
    assert digest(pixels.tobytes()) == render["pages"][0]["raster_hash"]
    # The asymmetric source remains correctly oriented; transparency composites white.
    assert pixels.getpixel((80, 80)) == (255, 0, 0)
    assert pixels.getpixel((250, 80)) == (255, 255, 255)
    assert pixels.getpixel((250, 350)) == (0, 0, 255)
    job = post(client, "/jobs", job_request(render, copies=2))
    duplicate = post(client, "/jobs", job_request(render, copies=2))
    assert job["id"] == duplicate["id"]
    assert [s["page"] for s in job["sheets"]] == [0, 1, 0, 1]
    worker = SimulatorWorker(client.app.state.service)
    while worker.run_once():
        pass
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["state"] == "completed"
    edited = client.patch(
        f"/api/v1/stickers/{sticker['id']}",
        json={
            "name": sticker["name"],
            "asset_id": asset["id"],
            "outline": [[0, 0], [1, 0], [0, 1]],
            "expected_revision": 1,
        },
    ).json()
    assert edited["revision"] == 2
    changed = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": "Edited",
            "bindings": {sticker["id"]: 2},
            "pages": project["pages"],
            "expected_revision": 2,
        },
    )
    assert changed.status_code == 200
    geometry = client.get(f"/api/v1/projects/{project['id']}/geometry").json()
    assert all(
        s["sticker_revision"] == 2 and len(s["outer"]["points"]) == 3
        for page in geometry
        for s in page["shapes"]
    )
    assert all(
        artifact(client, render, name) == data for name, data in original_files.items()
    )
    reprint = post(
        client,
        f"/jobs/{job['id']}/reprints",
        {
            "sheet_ids": [job["sheets"][0]["id"], job["sheets"][2]["id"]],
            "copies": 1,
            "confirmed": True,
            "accepted_profile_id": "simulator-v1",
            "idempotency_key": "reprint-job-key",
        },
    )
    assert (
        reprint["render_id"] == render["id"]
        and reprint["reprint_of_job_id"] == job["id"]
    )
    assert len(reprint["sheets"]) == 2
    assert reprint["execution_mode"] == "simulator"


def test_conflicts_overrides_and_invalid_options(client):
    _, _, sticker, project, render = fixture_project(client)
    job = post(client, "/jobs", job_request(render))
    assert (
        client.post("/api/v1/jobs", json=job_request(render, copies=2)).status_code
        == 409
    )
    stale = {
        "name": "stale",
        "bindings": project["bindings"],
        "pages": [],
        "expected_revision": 1,
    }
    assert (
        client.patch(f"/api/v1/projects/{project['id']}", json=stale).status_code == 409
    )
    p = project["pages"][0]["placements"][0]
    p["asset_id"] = "override"
    assert (
        client.patch(
            f"/api/v1/projects/{project['id']}",
            json={**stale, "expected_revision": 2, "pages": project["pages"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/layouts",
            json={
                "revision": 2,
                "sticker_id": sticker["id"],
                "mode": "fill",
                "quantity": 4,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/jobs", json=job_request(render, printer_id="usb")
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/jobs", json=job_request(render, confirmed=False)
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/renders",
            json={"revision": 2, "image_format": "jpeg"},
        ).status_code
        == 422
    )
    assert client.get("/api/v1/printers/usb").status_code == 409
    assert client.get("/api/v1/jobs").json()["items"][0]["id"] == job["id"]


@pytest.mark.parametrize("fault", ["missing_ack", "status_loss", "crash_after_intent"])
def test_uncertainty_holds_queue_across_restart(client, fault):
    *_, render = fixture_project(client)
    job = post(client, "/jobs", job_request(render, copies=2))
    service = client.app.state.service
    worker = SimulatorWorker(service)
    if fault == "crash_after_intent":
        with pytest.raises(RuntimeError):
            worker.run_once(fault)
    else:
        worker.run_once(fault)
    worker.recover()
    assert not worker.run_once()
    current = service.job(job["id"])
    assert current["counts"]["uncertain"] == 1 and current["counts"]["queued"] == 1
    with service.engine.connect() as c:
        assert len(c.execute(select(db.attempts)).all()) == 1
    post(
        client,
        f"/jobs/{job['id']}/resolutions",
        {
            "sheet_id": current["sheets"][0]["id"],
            "expected_event_id": current["last_event_id"],
            "outcome": "failed",
            "note": "Operator confirmed no usable sheet",
        },
    )
    assert worker.run_once()
    assert service.job(job["id"])["state"] == "partial"
    events = client.get(f"/api/v1/jobs/{job['id']}/events").json()
    assert any(e["state"] == "uncertain" for e in events)
    assert any(e["origin"] == "operator" for e in events)


def test_cancellation_integrity_and_concurrent_deduplication(client):
    *_, render = fixture_project(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(
            pool.map(
                lambda _: post(client, "/jobs", job_request(render))["id"], range(4)
            )
        )
    assert len(set(ids)) == 1
    job = post(client, f"/jobs/{ids[0]}/cancel")
    assert job["state"] == "cancelled"
    assert not SimulatorWorker(client.app.state.service).run_once()
    image_hash = render["pages"][0]["artifacts"]["image.png"]["hash"]
    (client.app.state.service.store.root / image_hash).write_bytes(b"altered")
    assert (
        client.post(
            "/api/v1/jobs", json=job_request(render, idempotency_key="different-key")
        ).status_code
        == 422
    )
    # A repeated accepted request still resolves to the original job after corruption.
    assert post(client, "/jobs", job_request(render))["id"] == job["id"]


def test_storage_limits_and_malicious_uploads(client, monkeypatch):
    assert (
        client.post(
            "/api/v1/assets", files={"file": ("evil.svg", b"<svg/>", "image/svg+xml")}
        ).status_code
        == 422
    )
    monkeypatch.setenv("PIXCUT_MIN_FREE_BYTES", str(10**20))
    with pytest.raises(ValueError, match="STORAGE_LOW"):
        client.app.state.service.store.put(b"bytes", "application/octet-stream")


def test_migration_restart_and_lock(client, tmp_path):
    *_, render = fixture_project(client)
    job = post(client, "/jobs", job_request(render))
    migrate(tmp_path)
    with TestClient(create_app(tmp_path)) as restarted:
        assert restarted.get(f"/api/v1/jobs/{job['id']}").json()["state"] == "queued"
    with worker_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Another printer worker"):
            with worker_lock(tmp_path):
                pass
    with client.app.state.service.engine.connect() as c:
        assert not c.exec_driver_sql("PRAGMA foreign_key_check").all()
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert c.exec_driver_sql("PRAGMA synchronous").scalar() == 2
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with client.app.state.service.engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), db.metadata) == []


def test_auth_and_origin(client, tmp_path, monkeypatch):
    assert (
        client.post(
            "/api/v1/projects",
            headers={"Origin": "https://attacker.example"},
            json={"name": "bad", "bindings": {}},
        ).status_code
        == 403
    )
    monkeypatch.setenv("PIXCUT_API_TOKEN", "test-operator-token")
    with TestClient(create_app(tmp_path)) as auth:
        assert auth.get("/api/v1/jobs").status_code == 401
        assert (
            auth.get(
                "/api/v1/jobs", headers={"Authorization": "Bearer test-operator-token"}
            ).status_code
            == 200
        )


def test_geometry_guards_pinning_rotation_and_contracts(client):
    from pathlib import Path
    import json
    from pixcut.models import CutGeometry, LayoutInput
    from pixcut.imaging import layout

    _, _, sticker, project, _ = fixture_project(client, 1)
    placement = project["pages"][0]["placements"][0]
    placement["rotation"] = 90
    placement["pinned"] = True
    updated = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": project["name"],
            "bindings": project["bindings"],
            "pages": project["pages"],
            "expected_revision": 2,
        },
    )
    assert updated.status_code == 200
    geometry = client.get(f"/api/v1/projects/{project['id']}/geometry").json()[0]
    points = geometry["shapes"][0]["outer"]["points"]
    assert max(p[0] for p in points) - min(p[0] for p in points) == 30
    assert max(p[1] for p in points) - min(p[1] for p in points) == 20
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/layouts",
            json={"revision": 3, "sticker_id": sticker["id"]},
        ).json()["code"]
        == "PINNED_LAYOUT"
    )
    with pytest.raises(ValueError, match="LAYOUT_OVERFLOW"):
        layout(LayoutInput(revision=3, sticker_id=sticker["id"], width_mm=200))
    # Collision edits are rejected before a new project revision is committed.
    placement["pinned"] = False
    project["pages"][0]["placements"].append({**placement, "id": "overlapping"})
    response = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": project["name"],
            "bindings": project["bindings"],
            "pages": project["pages"],
            "expected_revision": 3,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "PLACEMENT_COLLISION"
    root = Path(__file__).resolve().parents[1]
    CutGeometry.model_validate_json(
        (root / "contracts/cut-geometry-v1.json").read_text()
    )
    assert (
        json.loads((root / "contracts/cut-geometry.schema.json").read_text())
        == CutGeometry.model_json_schema()
    )
    assert (
        json.loads((root / "contracts/openapi.json").read_text())
        == client.app.openapi()
    )
