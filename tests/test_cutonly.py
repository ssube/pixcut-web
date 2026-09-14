import json

import pytest
from sqlalchemy import select, update

from pixcut import db
from pixcut.cli import migrate
from pixcut.cutonly import (
    cut_declaration,
    execute_test,
    prepare_test,
    record_observation,
    test_profile as cut_only_profile,
)
from pixcut.service import Problem, Service
from pixcut.printcut import PROFILE as STICKER_PROFILE


@pytest.fixture
def cut_only_job(tmp_path):
    migrate(tmp_path)
    service = Service(tmp_path)
    job = prepare_test(service, "standalone-zero", "fresh", "cut-only-key")
    yield service, job
    service.engine.dispose()


class Session:
    def __init__(self, outcome="completed", printed_before=12, printed_after=12):
        self.outcome = outcome
        self.printed_values = iter((printed_before, printed_after))
        self.requests = []
        self.chunks = []

    def properties(self, names):
        if names[0] == "firmware-revision":
            return {"result": ["fixture-fw", "A", "DHP700", "fixture-sku"]}
        if names[0] == "serial-number":
            return {"result": ["fixture", {"media-size": 5013}]}
        if names[0] == "big-data":
            return {"result": [{"printed": next(self.printed_values), "finished_5013": 4}]}
        return {"result": ["20", "2000", "::0"]}

    def rpc(self, request):
        self.requests.append(request)
        if request["method"] == "cut-job":
            if self.outcome == "rejected":
                return {"result": {"error-code": 8011}}
            return {"result": {"job_id": 71}}
        if self.outcome == "discarded":
            return {
                "result": [
                    {
                        "job-id": 0,
                        "job-state": 0,
                        "job-sub-state": 0,
                        "job-state-reason": 0,
                    }
                ]
            }
        if self.outcome == "printing":
            return {
                "result": [
                    {
                        "job-id": 71,
                        "job-state": 3,
                        "job-sub-state": 3005,
                        "job-state-reason": 30001,
                        "printing-page-number": 1,
                    }
                ]
            }
        return {
            "result": [
                {
                    "job-id": 71,
                    "job-state": 9,
                    "job-sub-state": 9000,
                    "job-state-reason": 90001,
                    "printing-page-number": 0,
                    "cutting-progress": 1,
                    "cut-contours": 1,
                }
            ]
        }

    def send_chunk(self, data, job_id):
        assert job_id == 71
        self.chunks.append(data)


def test_two_bounded_standalone_declarations():
    plot = b"PLT"
    assert cut_declaration(plot, "standalone-zero", "job-id", 123) == {
        "method": "cut-job",
        "params": {
            "channel": 2,
            "copies": 1,
            "media-size": 5013,
            "media-type": 2030,
            "job-type": 0,
            "plot-file-size": 3,
        },
    }
    full = cut_declaration(plot, "standalone-600", "job-id", 123)
    assert full["method"] == "cut-job"
    assert full["params"] == {
        "channel": 2,
        "copies": 1,
        "media-size": 5013,
        "media-type": 2030,
        "job-type": 600,
        "file-size": 3,
        "document-format": 18,
        "document-name": "jobid.plt",
        "hash-method": 1,
        "hash-value": "83618951efd5e2d2c5c9fb6c1477485364e59136",
        "user-account": "12345678",
        "job-send-time": 123,
    }


def test_profile_uses_one_pass_at_default_pressure():
    profile = cut_only_profile("standalone-zero")
    assert profile["cut_passes"] == 1
    assert profile["knife_pressure"] == STICKER_PROFILE["knife_pressure"] == 42
    with pytest.raises(ValueError, match="Unknown cut-only"):
        cut_only_profile("unknown")


def test_prepare_retains_only_plt_and_review_artifacts(cut_only_job):
    service, job = cut_only_job
    render = service.get(db.renders, job["render_id"])["data"]
    artifacts = render["pages"][0]["artifacts"]
    assert set(artifacts) == {
        "cut.plt",
        "cut-geometry.json",
        "thumbnail.png",
        "manifest.json",
    }
    assert job["state"] == "held"
    assert job["request"]["no_image_document"] is True
    assert job["request"]["mode"] == "cut_only"
    manifest = json.loads(service.store.read(artifacts["manifest.json"]["hash"]))
    assert manifest["data_order"] == ["cut.plt"]
    assert not any(name.endswith((".jpg", ".jpeg", ".png")) for name in manifest["data_order"])


def test_prepare_is_idempotent_and_rejects_key_reuse(cut_only_job):
    service, job = cut_only_job
    same = prepare_test(service, "standalone-zero", "fresh", "cut-only-key")
    assert same["id"] == job["id"]
    with pytest.raises(Problem) as error:
        prepare_test(service, "standalone-600", "fresh", "cut-only-key")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_printed_recut_requires_completed_sticker_lineage(cut_only_job):
    service, job = cut_only_job
    with pytest.raises(ValueError, match="require a completed source"):
        prepare_test(
            service,
            "standalone-zero",
            "printed",
            "printed-without-source",
        )
    with service.engine.begin() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(state="completed", request={**job["request"], "profile": STICKER_PROFILE})
        )
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="completed")
        )
    source = service.job(job["id"])
    source_render = service.get(db.renders, source["render_id"])["data"]
    source_plot = service.store.read(
        source_render["pages"][0]["artifacts"]["cut.plt"]["hash"]
    )
    recut = prepare_test(
        service,
        "standalone-zero",
        "printed",
        "printed-source-key",
        source["id"],
    )
    assert recut["reprint_of_job_id"] == source["id"]
    assert recut["request"]["source_sheet_id"] == source["sheets"][0]["id"]
    render = service.get(db.renders, recut["render_id"])["data"]
    plot = service.store.read(render["pages"][0]["artifacts"]["cut.plt"]["hash"])
    assert plot == source_plot
    assert recut["request"]["source_plot_sha256"] == render["pages"][0]["artifacts"][
        "cut.plt"
    ]["hash"]
    assert recut["request"]["profile"]["cut_passes"] == 1
    assert recut["request"]["profile"]["knife_pressure"] == 42


def test_glitter_laminate_requires_source_and_description(cut_only_job):
    service, job = cut_only_job
    with pytest.raises(ValueError, match="require a completed source"):
        prepare_test(
            service,
            "standalone-zero",
            "glitter-laminate",
            "laminate-without-source",
            laminate_description="Adhesive glitter vinyl, 0.08 mm",
        )
    with service.engine.begin() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(
                state="completed",
                request={**job["request"], "profile": STICKER_PROFILE},
            )
        )
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="completed")
        )
    source = service.job(job["id"])
    with pytest.raises(ValueError, match="Describe the adhesive glitter laminate"):
        prepare_test(
            service,
            "standalone-zero",
            "glitter-laminate",
            "laminate-without-description",
            source["id"],
        )


def test_glitter_laminate_reuses_exact_default_pressure_plot(cut_only_job):
    service, job = cut_only_job
    with service.engine.begin() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(
                state="completed",
                request={**job["request"], "profile": STICKER_PROFILE},
            )
        )
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="completed")
        )
    source = service.job(job["id"])
    source_render = service.get(db.renders, source["render_id"])["data"]
    source_plot = service.store.read(
        source_render["pages"][0]["artifacts"]["cut.plt"]["hash"]
    )
    recut = prepare_test(
        service,
        "standalone-zero",
        "glitter-laminate",
        "laminate-source-key",
        source["id"],
        "Adhesive glitter vinyl, 0.08 mm",
    )
    recut_render = service.get(db.renders, recut["render_id"])["data"]
    recut_plot = service.store.read(
        recut_render["pages"][0]["artifacts"]["cut.plt"]["hash"]
    )
    assert recut_plot == source_plot
    assert recut_plot.startswith(b"IN VER0.1.0 KP42 ")
    assert recut["request"]["laminate_description"] == (
        "Adhesive glitter vinyl, 0.08 mm"
    )
    assert recut["request"]["profile"]["cut_passes"] == 1


def test_printed_artwork_bounds_make_one_padded_outline(cut_only_job):
    service, job = cut_only_job
    with service.engine.begin() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(
                state="completed",
                request={**job["request"], "profile": STICKER_PROFILE},
            )
        )
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="completed")
        )
    source = service.job(job["id"])
    outlined = prepare_test(
        service,
        "standalone-zero",
        "printed",
        "artwork-outline-key",
        source["id"],
        outline_mode="artwork-bounds",
    )
    render = service.get(db.renders, outlined["render_id"])["data"]
    artifacts = render["pages"][0]["artifacts"]
    geometry = json.loads(service.store.read(artifacts["cut-geometry.json"]["hash"]))
    assert outlined["request"]["outline_mode"] == "artwork-bounds"
    assert len(geometry["shapes"]) == 1
    assert geometry["shapes"][0]["outer"]["points"] == [
        [38.8, 76.9],
        [62.8, 76.9],
        [62.8, 100.9],
        [38.8, 100.9],
    ]
    plot = service.store.read(artifacts["cut.plt"]["hash"])
    assert plot.startswith(b"IN VER0.1.0 KP42 ")


def test_known_rectangular_commissioning_job_can_source_outline(cut_only_job):
    service, job = cut_only_job
    rectangular_profile = {
        **STICKER_PROFILE,
        "id": "sticker-4x7-rect-jpeg-commissioning-v1",
        "cut_format": "pixcut-native-rect-plt-v1",
    }
    with service.engine.begin() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(
                state="completed",
                request={**job["request"], "profile": rectangular_profile},
            )
        )
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.job_id == job["id"])
            .values(state="completed")
        )
    source = service.job(job["id"])
    outlined = prepare_test(
        service,
        "standalone-zero",
        "printed",
        "rectangular-source-key",
        source["id"],
        outline_mode="artwork-bounds",
    )
    assert outlined["request"]["source_job_id"] == source["id"]


def test_glitter_laminate_rejects_artwork_bounds(cut_only_job):
    service, job = cut_only_job
    with pytest.raises(ValueError, match="require exact source contours"):
        prepare_test(
            service,
            "standalone-zero",
            "glitter-laminate",
            "glitter-bounds-key",
            job["id"],
            "Adhesive glitter vinyl, 0.08 mm",
            "artwork-bounds",
        )


def test_completed_attempt_transfers_only_plot(cut_only_job, monkeypatch):
    service, job = cut_only_job
    monkeypatch.setattr("pixcut.cutonly.time.sleep", lambda _: None)
    session = Session()
    result = execute_test(service, job, session, progress=lambda *args, **kwargs: None)
    assert result["state"] == "completed"
    assert b"".join(session.chunks).startswith(b"IN VER0.1.0 KP42")
    assert [request["method"] for request in session.requests] == [
        "cut-job",
        "get-job-info",
    ]
    assert result["sheets"][0]["evidence"]["printed_counter_unchanged"] is True


@pytest.mark.parametrize(
    ("outcome", "expected_state", "reason"),
    [
        ("rejected", "failed", "explicit_creation_rejection"),
        ("discarded", "failed", "firmware_discarded_cut_only_job"),
    ],
)
def test_explicit_nonworking_outcomes_are_not_uncertain(
    cut_only_job, monkeypatch, outcome, expected_state, reason
):
    service, job = cut_only_job
    monkeypatch.setattr("pixcut.cutonly.time.sleep", lambda _: None)
    result = execute_test(
        service, job, Session(outcome=outcome), progress=lambda *args, **kwargs: None
    )
    assert result["state"] == expected_state
    assert result["sheets"][0]["evidence"]["reason"] == reason
    with pytest.raises(ValueError, match="never-started"):
        execute_test(service, result, Session())


def test_unexpected_print_activity_is_uncertain_and_never_replayed(
    cut_only_job, monkeypatch
):
    service, job = cut_only_job
    monkeypatch.setattr("pixcut.cutonly.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Unexpected print phase"):
        execute_test(
            service,
            job,
            Session(outcome="printing"),
            progress=lambda *args, **kwargs: None,
        )
    current = service.job(job["id"])
    assert current["state"] == "uncertain"
    assert current["sheets"][0]["evidence"]["no_image_document"] is True
    with pytest.raises(ValueError, match="never-started"):
        execute_test(service, current, Session())


def test_changed_print_counter_is_uncertain(cut_only_job, monkeypatch):
    service, job = cut_only_job
    monkeypatch.setattr("pixcut.cutonly.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Printed counter changed"):
        execute_test(
            service,
            job,
            Session(printed_before=12, printed_after=13),
            progress=lambda *args, **kwargs: None,
        )
    assert service.job(job["id"])["state"] == "uncertain"


def test_operator_observation_requires_attempt_and_is_retained(
    cut_only_job, monkeypatch
):
    service, job = cut_only_job
    with pytest.raises(ValueError, match="only after a physical attempt"):
        record_observation(
            service,
            job["id"],
            cutter_moved=True,
            print_passes=False,
            visible_overcoat=False,
            jam=False,
            through_backing=False,
        )
    monkeypatch.setattr("pixcut.cutonly.time.sleep", lambda _: None)
    completed = execute_test(
        service, job, Session(), progress=lambda *args, **kwargs: None
    )
    observation = record_observation(
        service,
        completed["id"],
        cutter_moved=True,
        print_passes=False,
        visible_overcoat=False,
        jam=False,
        through_backing=False,
        max_offset_mm=0.4,
        notes="Centered square cut cleanly.",
    )
    assert observation["maximum_offset_mm"] == 0.4
    current = service.job(completed["id"])
    assert current["sheets"][0]["evidence"]["operator_observation"] == observation
    with service.engine.connect() as connection:
        event = connection.execute(
            select(db.events)
            .where(db.events.c.job_id == completed["id"])
            .order_by(db.events.c.id.desc())
        ).mappings().first()
    assert event["state"] == "observation"
    assert event["data"]["cutter_moved"] is True
