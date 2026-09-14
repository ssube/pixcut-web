import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select, update

from pixcut import db
from pixcut.cli import migrate
from pixcut.service import Problem, Service
from pixcut.commissioning import (
    PROFILE,
    PhotoSession,
    execute_photo,
    prepare_photo,
    photo_test_profile,
    transfer_recovery_evidence,
)
from pixcut.worker import SimulatorWorker
from pixcut.printcut import HardwareWorker


@pytest.fixture
def setup_photo(tmp_path):
    migrate(tmp_path)
    service = Service(tmp_path)
    path = tmp_path / "photo.png"
    Image.new("RGB", (40, 60), "orange").save(path)
    job = prepare_photo(service, path, "photo-test-key")
    yield service, path, job
    service.engine.dispose()


class Session:
    def __init__(self, fail_ack=False, reject=False, image_format="jpeg"):
        self.requests = []
        self.fail_ack = fail_ack
        self.reject = reject
        self.image_format = image_format

    def properties(self, names):
        if names[0] == "firmware-revision":
            return {"result": ["fixture-fw", "A", "DHP700", "fixture-sku"]}
        if names[0] == "serial-number":
            return {"result": ["fixture", {"media-size": 5009}]}
        return {"result": ["20", "2000", "::0"]}

    def rpc(self, request):
        self.requests.append(request)
        if request["method"] == "print-job":
            assert request["params"]["job-type"] == 0
            assert request["params"]["media-size"] == 5012
            assert request["params"]["media-type"] == 2010
            assert request["params"]["document-format"] == (
                10 if self.image_format == "png" else 9
            )
            assert request["params"]["copies"] == 1
            assert set(request["params"]) == {
                "media-size",
                "media-type",
                "job-type",
                "channel",
                "file-size",
                "document-format",
                "document-name",
                "hash-method",
                "hash-value",
                "user-account",
                "job-send-time",
                "copies",
            }
            assert request["params"]["channel"] == 14864
            assert request["params"]["user-account"] == "12345678"
            assert request["params"]["document-name"].endswith(
                ".png" if self.image_format == "png" else ".jpg"
            )
            return {"result": {"error-code": 8011} if self.reject else {"job_id": 42}}
        return {
            "result": [
                {
                    "job-id": 42,
                    "job-state": 9,
                    "job-sub-state": 9000,
                    "job-state-reason": 90001,
                }
            ]
        }

    def send_chunk(self, data, job_id):
        assert job_id == 42 and len(data) <= PROFILE["chunk_payload"]
        if self.fail_ack:
            raise RuntimeError("Unexpected data ACK: b'unsupported cmd'")


def test_worker_heartbeat_reports_readiness_and_staleness(setup_photo):
    service, _, _ = setup_photo
    assert service.worker_health("hardware")["state"] == "offline"
    service.worker_heartbeat("hardware", "idle")
    health = service.worker_health("hardware")
    assert health["fresh"] is True and health["state"] == "idle"
    with service.engine.begin() as connection:
        connection.execute(
            update(db.worker_status)
            .where(db.worker_status.c.id == "printer")
            .values(last_seen_at="2000-01-01T00:00:00+00:00")
        )
    stale = service.worker_health("hardware")
    assert stale["fresh"] is False and stale["state"] == "offline"


def test_hardware_worker_blocks_then_uses_a_fresh_session_after_recovery(
    setup_photo, monkeypatch
):
    service, path, first = setup_photo
    service.resume_hardware_job(first["id"])
    monkeypatch.setattr(
        "pixcut.discovery.scan_usb",
        lambda: {"devices": [{"bus": 1, "address": 2}]},
    )
    monkeypatch.setattr("usb.core.find", lambda **_: object())
    monkeypatch.setattr("pixcut.commissioning.time.sleep", lambda _: None)
    sessions = [Session(fail_ack=True), Session()]
    entered = []

    class Context:
        def __init__(self, *_):
            self.session = sessions[len(entered)]

        def __enter__(self):
            entered.append(self.session)
            return self.session

        def __exit__(self, *_):
            return False

    monkeypatch.setattr("pixcut.printcut.PhotoSession", Context)
    worker = HardwareWorker(service)
    assert worker.run_once() is False
    assert service.job(first["id"])["state"] == "uncertain"
    assert service.worker_health("hardware")["state"] == "blocked"
    assert worker.next_job() is None

    uncertain_sheet = service.job(first["id"])["sheets"][0]
    with service.engine.begin() as connection:
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.id == uncertain_sheet["id"])
            .values(state="failed", evidence={"origin": "operator"})
        )
        service.aggregate(connection, first["id"])
    second = prepare_photo(service, path, "fresh-session-job")
    service.resume_hardware_job(second["id"])
    assert worker.run_once() is True
    assert service.job(second["id"])["state"] == "completed"
    assert entered == sessions
    assert entered[0] is not entered[1]


def test_photo_freeze_intent_and_idempotency(setup_photo, monkeypatch):
    service, path, job = setup_photo
    assert job["state"] == "held" and job["execution_mode"] == "hardware"
    assert prepare_photo(service, path, "photo-test-key")["id"] == job["id"]
    monkeypatch.setattr("pixcut.commissioning.time.sleep", lambda _: None)
    session = Session()
    result = execute_photo(service, job, session, progress=lambda *a, **kw: None)
    assert result["state"] == "completed"
    with service.engine.connect() as c:
        assert c.execute(select(db.attempts.c.device_job_id)).scalar() == "42"
        records = c.execute(select(db.events).order_by(db.events.c.id)).mappings().all()
    assert [r["state"] for r in records][:3] == ["held", "creating", "transferring"]
    assert records[-1]["data"]["physical_output_verified"] is False
    with pytest.raises(RuntimeError, match="attempt already exists"):
        execute_photo(service, job, session)
    assert len([r for r in session.requests if r["method"] == "print-job"]) == 1
    manifest_hash = service.get(db.renders, job["render_id"])["data"]["pages"][0][
        "artifacts"
    ]["manifest.json"]["hash"]
    manifest = json.loads(service.store.read(manifest_hash))
    assert manifest["profile"]["hardware_validated"] is True
    assert not any("plt" in name for name in manifest["artifacts"])
    assert "image.jpg" in manifest["artifacts"]
    image_artifact = manifest["artifacts"]["image.jpg"]
    assert len(service.store.read(image_artifact["hash"])) < 1024 * 1024


def test_held_hardware_job_can_be_resumed_before_device_intent(
    setup_photo, monkeypatch
):
    service, _, job = setup_photo
    resumed = service.resume_hardware_job(job["id"])
    assert resumed["state"] == "queued"
    assert resumed["sheets"][0]["state"] == "queued"
    assert HardwareWorker(service).next_job()["id"] == job["id"]
    with pytest.raises(Problem) as error:
        service.resume_hardware_job(job["id"])
    assert error.value.code == "JOB_NOT_RESUMABLE"

    monkeypatch.setattr("pixcut.discovery.scan_usb", lambda: {"devices": []})
    assert HardwareWorker(service).run_once() is False
    held = service.job(job["id"])
    assert held["state"] == "held"
    assert held["sheets"][0]["evidence"]["resume_required"] is True
    assert service.resume_hardware_job(job["id"])["state"] == "queued"


def test_one_experimental_quality_size_point(setup_photo, monkeypatch):
    service, path, _ = setup_photo
    profile = photo_test_profile([600, 900], 80)
    assert profile["id"] == "photo-4x6-jpeg-test"
    assert profile["hardware_validated"] is False
    job = prepare_photo(
        service,
        path,
        "photo-matrix-point-key",
        raster=[600, 900],
        quality=80,
    )
    assert job["request"]["profile"] == profile
    image_hash = service.get(db.renders, job["render_id"])["data"]["pages"][0][
        "artifacts"
    ]["image.jpg"]["hash"]
    with Image.open(service.store.root / image_hash) as encoded:
        assert encoded.size == (600, 900)
    monkeypatch.setattr("pixcut.commissioning.time.sleep", lambda _: None)
    result = execute_photo(service, job, Session(), progress=lambda *a, **kw: None)
    assert result["state"] == "completed"


def test_one_png_payload_point(setup_photo, monkeypatch):
    service, path, _ = setup_photo
    profile = photo_test_profile([1200, 1800], None, "png", "huffman")
    assert profile["id"] == "photo-4x6-png-test"
    assert profile["document_format"] == 10
    assert profile["max_image_bytes"] == 4 * 1024 * 1024
    job = prepare_photo(
        service,
        path,
        "photo-png-point-key",
        raster=[1200, 1800],
        image_format="png",
        png_strategy="huffman",
    )
    assert job["request"]["profile"] == profile
    artifacts = service.get(db.renders, job["render_id"])["data"]["pages"][0][
        "artifacts"
    ]
    assert "image.png" in artifacts and "image.jpg" not in artifacts
    with Image.open(service.store.root / artifacts["image.png"]["hash"]) as encoded:
        assert encoded.format == "PNG"
        assert encoded.mode == "RGB"
        assert encoded.size == (1200, 1800)
    monkeypatch.setattr("pixcut.commissioning.time.sleep", lambda _: None)
    result = execute_photo(
        service,
        job,
        Session(image_format="png"),
        progress=lambda *a, **kw: None,
    )
    assert result["state"] == "completed"


@pytest.mark.parametrize(
    "raster,quality,image_format",
    [
        ([301, 450], 80, "jpeg"),
        ([300, 450], 49, "jpeg"),
        ([2500, 3750], 80, "jpeg"),
        ([300, 450], 80, "png"),
        ([300, 450], None, "gif"),
    ],
)
def test_experimental_photo_point_bounds(raster, quality, image_format):
    with pytest.raises(ValueError):
        photo_test_profile(raster, quality, image_format)


@pytest.mark.parametrize("reject", [False, True])
def test_no_retry_on_ambiguous_transfer_or_rejection(setup_photo, reject):
    service, _, job = setup_photo
    session = Session(fail_ack=True, reject=reject)
    if reject:
        assert (
            execute_photo(service, job, session, progress=lambda *a, **kw: None)[
                "state"
            ]
            == "failed"
        )
    else:
        with pytest.raises(RuntimeError, match="Unexpected data ACK"):
            execute_photo(service, job, session, progress=lambda *a, **kw: None)
        current = service.job(job["id"])
        assert current["state"] == "uncertain"
        assert current["sheets"][0]["evidence"]["acknowledged_image_bytes"] == 0
        assert current["sheets"][0]["evidence"]["device_job_id"] == 42
        assert current["sheets"][0]["evidence"]["recovery"] == {
            "action": "power_cycle_printer",
            "automatic_retry": False,
        }
    with pytest.raises(RuntimeError):
        execute_photo(service, job, session)
    assert len(session.requests) == 1


def test_power_cycle_recovery_is_limited_to_observed_transfer_signature():
    assert transfer_recovery_evidence("Unexpected data ACK: b'unsupported cmd'")
    assert transfer_recovery_evidence("Device error event: -8013")
    assert transfer_recovery_evidence("Missing data ACK") == {}


def test_simulator_never_recovers_or_dispatches_hardware(setup_photo):
    service, _, job = setup_photo
    with service.engine.begin() as c:
        c.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="creating")
        )
    simulator = SimulatorWorker(service)
    simulator.recover()
    assert service.job(job["id"])["sheets"][0]["state"] == "creating"
    with service.engine.begin() as c:
        c.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="queued")
        )
    assert simulator.run_once() is False


def test_packet_framing_and_exact_ack():
    class Device:
        writes = []

        def write(self, endpoint, wire, timeout):
            assert endpoint == 4
            assert len(wire) <= 1024
            self.writes.append(wire)
            return len(wire)

        def read(self, *args, **kwargs):
            return b"cmd data EXTLEN=3 OK\r\n"

    session = PhotoSession(
        Device(), SimpleNamespace(USBTimeoutError=TimeoutError), None
    )
    session.send_chunk(b"PNG", 42)
    assert b"".join(session.device.writes) == (
        b"cmd data EXTLEN=3\n" + struct.pack("<I", 42) + b"PNG"
    )
    session.device.read = lambda *a, **kw: b"cmd data EXTLEN=8 OK"
    with pytest.raises(RuntimeError, match="Unexpected data ACK"):
        session.send_chunk(b"PNG", 42)
