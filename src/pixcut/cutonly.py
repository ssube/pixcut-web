"""Bounded cut-only commissioning for the PixCut S1.

This module intentionally does not register a production printer profile or API
route.  Standalone cut jobs are not known to work on the DHP700 firmware, so the
only entry point is the operator-driven commissioning script.
"""

import argparse
import hashlib
import json
import time
from io import BytesIO

from PIL import Image, ImageDraw
from sqlalchemy import select, update

from . import db
from .artifacts import canonical, digest
from .commissioning import PhotoSession, checkpoint, transfer_recovery_evidence
from .printcut import PROFILE as STICKER_PROFILE, geometry_to_plt
from .probe import QUERIES
from .service import Problem, Service, now, uid
from .worker import worker_lock


VARIANTS = {
    "standalone-zero": {
        "job_type": 0,
        "size_field": "plot-file-size",
        "metadata": "minimal",
    },
    "standalone-600": {
        "job_type": 600,
        "size_field": "file-size",
        "metadata": "full",
    },
}

SOURCE_PROFILE_IDS = {
    STICKER_PROFILE["id"],
    "sticker-4x7-rect-jpeg-commissioning-v1",
}

BASE_PROFILE = {
    "id": "sticker-4x7-cut-only-test",
    "execution_mode": "hardware",
    "commissioning": True,
    "hardware_validated": False,
    "validation": {
        "status": "standalone cut-job protocol experiment",
        "required_trials": 3,
        "maximum_registration_error_mm": 0.5,
    },
    "mode": "cut_only",
    "media_size": 5013,
    "media_type": 2030,
    "document_format": 18,
    "cut_format": STICKER_PROFILE["cut_format"],
    "knife_pressure": STICKER_PROFILE["knife_pressure"],
    "data_order": ["cut.plt"],
    "chunk_payload": STICKER_PROFILE["chunk_payload"],
    "usb_write_size": STICKER_PROFILE["usb_write_size"],
    "chunk_delay_s": STICKER_PROFILE["chunk_delay_s"],
    "protocol_references": {
        "standalone_request": "eastbay-pixcut-s1 create_cut_job",
        "discard_observation": "PixCut-App pixcut-usb-protocol.md section 5.1",
    },
}


def test_profile(variant):
    if variant not in VARIANTS:
        raise ValueError("Unknown cut-only declaration variant")
    return {
        **BASE_PROFILE,
        "variant": variant,
        "cut_passes": 1,
        "job_type": VARIANTS[variant]["job_type"],
    }


def is_test_profile(value):
    if not isinstance(value, dict):
        return False
    try:
        return value["cut_passes"] == 1 and value == test_profile(value["variant"])
    except (KeyError, TypeError, ValueError):
        return False


def cut_declaration(plot, variant, job_id, send_time):
    config = VARIANTS[variant]
    params = {
        "channel": STICKER_PROFILE["channel"],
        "copies": 1,
        "media-size": STICKER_PROFILE["media_size"],
        "media-type": STICKER_PROFILE["media_type"],
        "job-type": config["job_type"],
        config["size_field"]: len(plot),
    }
    if config["metadata"] == "full":
        params.update(
            {
                "document-format": 18,
                "document-name": f"{job_id.replace('-', '')}.plt",
                "hash-method": 1,
                "hash-value": hashlib.sha1(plot).hexdigest(),
                "user-account": "12345678",
                "job-send-time": send_time,
            }
        )
    return {"method": "cut-job", "params": params}


def calibration_geometry():
    # A centered 20 mm square keeps the first motion far from every sheet edge.
    points = [[40.8, 78.9], [60.8, 78.9], [60.8, 98.9], [40.8, 98.9]]
    return {
        "schema_version": "1.0",
        "page_mm": STICKER_PROFILE["page_mm"],
        "coordinate_space": "sheet_design",
        "origin": "top_left",
        "units": "mm",
        "shapes": [
            {
                "id": "cut-pass-1",
                "sticker_id": "cut-only-calibration",
                "sticker_revision": 1,
                "placement_id": "cut-pass-1",
                "outer": {"closed": True, "points": points},
                "holes": [],
                "cut_policy": {"cut_padding_mm": 0, "minimum_cut_width_mm": 0},
            }
        ],
        "provenance": {
            "kind": "render",
            "project_id": "cut-only-calibration",
            "project_revision": 1,
            "render_id": None,
        },
    }


def artwork_bounds_geometry(source_geometry, padding_mm=2.0):
    page_width, page_height = source_geometry["page_mm"]
    points = [
        point
        for shape in source_geometry["shapes"]
        for point in shape["outer"]["points"]
    ]
    if not points:
        raise ValueError("Source sticker geometry has no outer contours")
    left = max(0.0, min(point[0] for point in points) - padding_mm)
    top = max(0.0, min(point[1] for point in points) - padding_mm)
    right = min(page_width, max(point[0] for point in points) + padding_mm)
    bottom = min(page_height, max(point[1] for point in points) + padding_mm)
    if right <= left or bottom <= top:
        raise ValueError("Source sticker artwork bounds are empty")
    return {
        **source_geometry,
        "shapes": [
            {
                "id": "artwork-outline",
                "sticker_id": "cut-only-artwork-outline",
                "sticker_revision": source_geometry["provenance"]["project_revision"],
                "placement_id": "artwork-outline",
                "outer": {
                    "closed": True,
                    "points": [
                        [left, top],
                        [right, top],
                        [right, bottom],
                        [left, bottom],
                    ],
                },
                "holes": [],
                "cut_policy": {
                    "cut_padding_mm": padding_mm,
                    "minimum_cut_width_mm": 0,
                },
            }
        ],
        "provenance": {
            **source_geometry["provenance"],
            "kind": "cut-only-artwork-outline",
        },
    }


def preview_png(geometry=None):
    geometry = geometry or calibration_geometry()
    image = Image.new("RGB", (240, 420), "white")
    draw = ImageDraw.Draw(image)
    scale_x = image.width / geometry["page_mm"][0]
    scale_y = image.height / geometry["page_mm"][1]
    for shape in geometry["shapes"]:
        points = [
            (round(point[0] * scale_x), round(point[1] * scale_y))
            for point in shape["outer"]["points"]
        ]
        draw.line(points + [points[0]], fill=(225, 38, 102), width=2)
    out = BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def _source_job_artifacts(service, source_job_id):
    source_job = service.job(source_job_id)
    source_profile = source_job["request"].get("profile")
    supported_profile = (
        isinstance(source_profile, dict)
        and source_profile.get("id") in SOURCE_PROFILE_IDS
        and source_profile.get("execution_mode") == "hardware"
        and source_profile.get("mode") == "print_cut"
        and source_profile.get("media_size") == STICKER_PROFILE["media_size"]
        and source_profile.get("media_type") == STICKER_PROFILE["media_type"]
        and source_profile.get("page_mm") == STICKER_PROFILE["page_mm"]
        and source_profile.get("raster") == STICKER_PROFILE["raster"]
        and source_profile.get("knife_pressure") == STICKER_PROFILE["knife_pressure"]
        and source_profile.get("data_order") == "plt_then_jpeg"
    )
    if (
        source_job["execution_mode"] != "hardware"
        or source_job["state"] != "completed"
        or not supported_profile
        or len(source_job["sheets"]) != 1
    ):
        raise ValueError(
            "Recut tests require one completed hardware sticker job"
        )
    source_render = service.get(db.renders, source_job["render_id"])["data"]
    files = source_render["pages"][0]["artifacts"]
    if "cut.plt" not in files or "cut-geometry.json" not in files:
        raise ValueError("Source sticker history does not retain cut geometry")
    source_plot = service.store.read(files["cut.plt"]["hash"])
    if not source_plot.startswith(
        f'IN VER0.1.0 KP{STICKER_PROFILE["knife_pressure"]} '.encode()
    ):
        raise ValueError("Source sticker PLT does not use the default knife pressure")
    geometry_bytes = service.store.read(files["cut-geometry.json"]["hash"])
    geometry = json.loads(geometry_bytes)
    return {
        "job": source_job,
        "render": source_render,
        "geometry": geometry,
        "geometry_bytes": geometry_bytes,
        "plot": source_plot,
        "thumbnail": files.get("thumbnail.png"),
        "source_plot_sha256": digest(source_plot),
    }


def prepare_test(
    service,
    variant,
    sheet_kind,
    key,
    source_job_id=None,
    laminate_description=None,
    outline_mode="exact",
):
    if sheet_kind not in ("fresh", "printed", "glitter-laminate"):
        raise ValueError("Sheet kind must be fresh, printed, or glitter-laminate")
    if source_job_id and sheet_kind == "fresh":
        raise ValueError(
            "A retained sticker job requires printed or glitter-laminate media"
        )
    if not source_job_id and sheet_kind != "fresh":
        raise ValueError(
            "Printed and glitter-laminate tests require a completed source sticker job"
        )
    if sheet_kind == "glitter-laminate" and not laminate_description:
        raise ValueError("Describe the adhesive glitter laminate used for this test")
    if sheet_kind != "glitter-laminate" and laminate_description:
        raise ValueError("Laminate description only applies to glitter-laminate tests")
    if outline_mode not in ("exact", "artwork-bounds"):
        raise ValueError("Outline mode must be exact or artwork-bounds")
    if outline_mode == "artwork-bounds" and not source_job_id:
        raise ValueError("Artwork bounds require a completed source sticker job")
    if sheet_kind == "glitter-laminate" and outline_mode != "exact":
        raise ValueError("Glitter-laminate recuts require exact source contours")
    profile = test_profile(variant)
    if source_job_id:
        source = _source_job_artifacts(service, source_job_id)
        if outline_mode == "artwork-bounds":
            geometry = artwork_bounds_geometry(source["geometry"])
            geometry_bytes = canonical(geometry)
            plot = geometry_to_plt(geometry)
        else:
            geometry = source["geometry"]
            geometry_bytes = source["geometry_bytes"]
            plot = source["plot"]
    else:
        source = None
        geometry = calibration_geometry()
        geometry_bytes = canonical(geometry)
        plot = geometry_to_plt(geometry)
    request_base = {
        "profile": profile,
        "mode": "cut_only",
        "copies": 1,
        "sheet_kind": sheet_kind,
        "operator_confirmed_media": "4x7 sticker paper",
        "no_image_document": True,
        "source_job_id": source_job_id,
        "source_sheet_id": source["job"]["sheets"][0]["id"] if source else None,
        "source_plot_sha256": source["source_plot_sha256"] if source else None,
        "laminate_description": laminate_description,
        "outline_mode": outline_mode,
    }
    request_hash = digest(canonical(request_base))
    with service.engine.connect() as connection:
        existing = (
            connection.execute(
                select(db.jobs).where(db.jobs.c.idempotency_key == key)
            )
            .mappings()
            .first()
        )
    if existing:
        if existing["request_hash"] != request_hash:
            raise Problem("IDEMPOTENCY_CONFLICT", "Key belongs to another test", 409)
        return service.job(existing["id"])

    job_id, render_id, sheet_id = uid(), uid(), uid()
    declaration = cut_declaration(plot, variant, job_id, int(time.time()))
    files = {
        "cut.plt": service.store.put(plot, "application/vnd.hp-hpgl"),
        "cut-geometry.json": service.store.put(geometry_bytes, "application/json"),
    }
    if source and source["thumbnail"] and outline_mode == "exact":
        files["thumbnail.png"] = source["thumbnail"]
    else:
        files["thumbnail.png"] = service.store.put(preview_png(geometry), "image/png")
    manifest = {
        "schema_version": "1.0",
        "profile": profile,
        "declaration": declaration,
        "data_order": ["cut.plt"],
        "artifacts": files,
        "hashes": {
            "plot_sha1": hashlib.sha1(plot).hexdigest(),
            "plot_sha256": digest(plot),
        },
        "operator_authorization": request_base,
        "source_job_id": source_job_id,
    }
    files["manifest.json"] = service.store.put(canonical(manifest), "application/json")
    render = {
        "id": render_id,
        "project_id": source["render"]["project_id"] if source else "cut-only-calibration",
        "revision": source["render"]["revision"] if source else 1,
        "created_at": now(),
        "profile": profile,
        "pages": [
            {
                "index": 0,
                "sticker_count": 1,
                "raster_hash": digest(plot),
                "artifacts": files,
            }
        ],
        "hardware_eligible": False,
        "commissioning": True,
        "warnings": ["Private cut-only protocol test; no image is transferred."],
    }
    request = {**request_base, "declaration": declaration}
    with service.engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.execute(
            db.renders.insert().values(
                id=render_id,
                project_id=render["project_id"],
                revision=render["revision"],
                data=render,
            )
        )
        for name, artifact in files.items():
            service.register(connection, artifact)
            connection.execute(
                db.render_files.insert().values(
                    render_id=render_id, name=f"0/{name}", hash=artifact["hash"]
                )
            )
        connection.execute(
            db.jobs.insert().values(
                id=job_id,
                created_at=now(),
                name=(
                    f"Cut-only recut test · {variant}"
                    if source_job_id
                    else f"Cut-only test · {variant}"
                ),
                reprint_of_job_id=source_job_id,
                render_id=render_id,
                idempotency_key=key,
                request_hash=request_hash,
                execution_mode="hardware",
                state="held",
                request=request,
            )
        )
        connection.execute(
            db.sheets.insert().values(
                id=sheet_id,
                job_id=job_id,
                ordinal=0,
                page=0,
                copy=0,
                state="held",
            )
        )
        service.event(
            connection,
            job_id,
            "held",
            sheet_id,
            "operator",
            {
                "commissioning": True,
                "cut_only": True,
                "declaration": declaration,
                "physical_attempt_created": False,
            },
        )
    return service.job(job_id)


def _first_result(response):
    result = response.get("result")
    if isinstance(result, list) and len(result) == 1:
        return result[0]
    return result


def _discarded_job(value):
    if not isinstance(value, dict):
        return False
    fields = ("job-id", "job-state", "job-sub-state", "job-state-reason")
    return all(value.get(field) in (None, 0, "0", "") for field in fields)


def _printed_counter(response):
    value = _first_result(response)
    if not isinstance(value, dict):
        return None
    try:
        return int(value["printed"])
    except (KeyError, TypeError, ValueError):
        return None


def execute_test(service, job, session, progress=print, heartbeat=lambda: None):
    profile = job["request"].get("profile")
    if (
        job["execution_mode"] != "hardware"
        or not is_test_profile(profile)
        or job["request"].get("mode") != "cut_only"
        or not job["request"].get("no_image_document")
        or len(job["sheets"]) != 1
        or job["state"] != "held"
    ):
        raise ValueError("Not a never-started cut-only commissioning job")
    with service.engine.connect() as connection:
        if connection.execute(
            select(db.attempts.c.id).where(
                db.attempts.c.sheet_id == job["sheets"][0]["id"]
            )
        ).first():
            raise RuntimeError("This cut-only sheet already has a device attempt")
        if connection.execute(
            select(db.jobs.c.id).where(
                db.jobs.c.execution_mode == "hardware",
                db.jobs.c.state.in_(
                    ["creating", "transferring", "processing", "uncertain"]
                ),
            )
        ).first():
            raise RuntimeError("Unresolved hardware activity holds dispatch")

    render = service.get(db.renders, job["render_id"])["data"]
    files = render["pages"][0]["artifacts"]
    if set(files) != {"cut.plt", "cut-geometry.json", "thumbnail.png", "manifest.json"}:
        raise ValueError("Cut-only test must contain PLT and review artifacts only")
    plot = service.store.read(files["cut.plt"]["hash"])
    manifest = json.loads(service.store.read(files["manifest.json"]["hash"]))
    if manifest["declaration"] != job["request"]["declaration"]:
        raise ValueError("Frozen cut declaration integrity mismatch")
    if manifest["hashes"]["plot_sha256"] != digest(plot):
        raise ValueError("Frozen PLT integrity mismatch")
    pressure_prefix = f'IN VER0.1.0 KP{profile["knife_pressure"]} '.encode()
    if not plot.startswith(pressure_prefix):
        raise ValueError("Cut-only PLT does not use the profile knife pressure")

    identity = session.properties(QUERIES[0][1])
    media = session.properties(QUERIES[1][1])
    status = session.properties(QUERIES[2][1])
    counters_before = session.properties(("big-data",))
    if identity.get("result", [None, None, None])[2] != "DHP700":
        raise RuntimeError(f"Unexpected printer identity: {identity}")
    if not (
        isinstance(status.get("result"), list)
        and str(status["result"][0]) in ("20", "30")
        and status["result"][2] == "::0"
    ):
        raise RuntimeError(f"Printer is not idle/asleep without alerts: {status}")

    intent = False
    device_id = None
    acknowledged = 0
    try:
        checkpoint(
            service,
            job,
            "creating",
            {
                "identity": identity,
                "media": media,
                "status": status,
                "big_data_before": counters_before,
                "profile": profile,
                "no_image_document": True,
            },
            intent=True,
        )
        intent = True
        progress("Creation intent committed; sending standalone cut-job", flush=True)
        response = session.rpc(manifest["declaration"])
        result = _first_result(response)
        if isinstance(result, dict) and "error-code" in result and "job_id" not in result:
            checkpoint(
                service,
                job,
                "failed",
                {"creation_response": response, "reason": "explicit_creation_rejection"},
            )
            return service.job(job["id"])
        device_id = result.get("job_id") if isinstance(result, dict) else None
        if type(device_id) is not int or not 0 < device_id < 2**32:
            raise RuntimeError(f"No valid device job ID: {response}")
        checkpoint(
            service,
            job,
            "transferring",
            {"creation_response": response, "no_image_document": True},
            device_id=device_id,
        )
        for offset in range(0, len(plot), profile["chunk_payload"]):
            chunk = plot[offset : offset + profile["chunk_payload"]]
            session.send_chunk(chunk, device_id)
            acknowledged += len(chunk)
            time.sleep(profile["chunk_delay_s"])
        checkpoint(
            service,
            job,
            "processing",
            {"acknowledged_plot_bytes": acknowledged, "uploaded_artifacts": ["cut.plt"]},
        )

        deadline = time.monotonic() + 180
        previous = None
        while time.monotonic() < deadline:
            heartbeat()
            info = session.rpc(
                {"method": "get-job-info", "params": {"job-id": device_id}}
            )
            value = _first_result(info)
            if _discarded_job(value):
                checkpoint(
                    service,
                    job,
                    "failed",
                    {"job_info": info, "reason": "firmware_discarded_cut_only_job"},
                )
                return service.job(job["id"])
            if not isinstance(value, dict):
                raise RuntimeError(f"Unexpected cut-only job observation: {info}")
            printing_page = value.get("printing-page-number")
            if printing_page not in (None, 0, "0", ""):
                raise RuntimeError(
                    f"Unexpected print phase during cut-only test: {info}"
                )
            state = tuple(
                str(value.get(key))
                for key in ("job-state", "job-sub-state", "job-state-reason")
            )
            cut = (value.get("cutting-progress"), value.get("cut-contours"))
            observation = (state, cut)
            if observation != previous:
                checkpoint(service, job, "processing", {"job_info": info})
                progress(f"Device state {state}; cutting {cut}", flush=True)
                previous = observation
            if state == ("9", "9000", "90001"):
                counters_after = session.properties(("big-data",))
                before = _printed_counter(counters_before)
                after = _printed_counter(counters_after)
                if before is not None and after is not None and after != before:
                    raise RuntimeError(
                        f"Printed counter changed during cut-only test: {before} -> {after}"
                    )
                checkpoint(
                    service,
                    job,
                    "completed",
                    {
                        "job_info": info,
                        "big_data_before": counters_before,
                        "big_data_after": counters_after,
                        "printed_counter_unchanged": (
                            before == after if before is not None and after is not None else None
                        ),
                        "operator_observation_required": True,
                    },
                )
                return service.job(job["id"])
            if state[0] == "7":
                checkpoint(service, job, "failed", {"job_info": info})
                return service.job(job["id"])
            current = session.properties(QUERIES[2][1])
            if (
                isinstance(current.get("result"), list)
                and str(current["result"][0]) == "60"
            ):
                raise RuntimeError(f"Device error during cut-only test: {current}")
            time.sleep(1)
        raise TimeoutError("Cut-only completion not observed within 180 seconds")
    except BaseException as exc:
        if intent:
            evidence = {
                "error": str(exc),
                "automatic_retry": False,
                "acknowledged_plot_bytes": acknowledged,
                "total_plot_bytes": len(plot),
                "no_image_document": True,
            }
            evidence.update(transfer_recovery_evidence(exc))
            if device_id is not None:
                evidence["device_job_id"] = device_id
            checkpoint(service, job, "uncertain", evidence)
        raise


def record_observation(
    service,
    job_id,
    *,
    cutter_moved,
    print_passes,
    visible_overcoat,
    jam,
    through_backing,
    max_offset_mm=None,
    notes="",
):
    job = service.job(job_id)
    if not is_test_profile(job["request"].get("profile")):
        raise ValueError("Observations can only be attached to a cut-only test")
    if max_offset_mm is not None and max_offset_mm < 0:
        raise ValueError("Maximum offset cannot be negative")
    observation = {
        "cutter_moved": bool(cutter_moved),
        "print_passes_observed": bool(print_passes),
        "visible_overcoat_added": bool(visible_overcoat),
        "paper_jam": bool(jam),
        "cut_through_backing": bool(through_backing),
        "maximum_offset_mm": max_offset_mm,
        "notes": notes,
        "recorded_at": now(),
    }
    sheet_id = job["sheets"][0]["id"]
    with service.engine.begin() as connection:
        attempted = connection.execute(
            select(db.attempts.c.id).where(db.attempts.c.sheet_id == sheet_id)
        ).first()
        if attempted is None:
            raise ValueError("Record an observation only after a physical attempt")
        existing = job["sheets"][0].get("evidence") or {}
        connection.execute(
            update(db.sheets)
            .where(db.sheets.c.id == sheet_id)
            .values(evidence={**existing, "operator_observation": observation})
        )
        service.event(
            connection,
            job_id,
            "observation",
            sheet_id,
            "operator",
            observation,
        )
    return observation


def _yes_no(value):
    return value == "yes"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Private PLT-only PixCut S1 commissioning; dry-run by default"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    test = subparsers.add_parser("test", help="prepare or execute one cut-only attempt")
    test.add_argument("--variant", choices=sorted(VARIANTS), required=True)
    test.add_argument(
        "--sheet-kind",
        choices=("fresh", "printed", "glitter-laminate"),
        default="fresh",
    )
    test.add_argument("--idempotency-key", required=True)
    test.add_argument(
        "--source-job-id",
        help="completed physical sticker job whose exact retained contours will be reused",
    )
    test.add_argument(
        "--laminate-description",
        help="product and thickness of the fully adhered glitter laminate vinyl",
    )
    test.add_argument(
        "--outline",
        choices=("exact", "artwork-bounds"),
        default="exact",
        help="exact source contours, or one rectangle 2 mm beyond all source artwork",
    )
    test.add_argument("--execute", action="store_true")

    observe = subparsers.add_parser("observe", help="record the physical result")
    observe.add_argument("job_id")
    for name in (
        "cutter-moved",
        "print-passes",
        "visible-overcoat",
        "jam",
        "through-backing",
    ):
        observe.add_argument(f"--{name}", choices=("yes", "no"), required=True)
    observe.add_argument("--max-offset-mm", type=float)
    observe.add_argument("--notes", default="")

    args = parser.parse_args(argv)
    service = Service(db.data_dir())
    if args.command == "observe":
        result = record_observation(
            service,
            args.job_id,
            cutter_moved=_yes_no(args.cutter_moved),
            print_passes=_yes_no(args.print_passes),
            visible_overcoat=_yes_no(args.visible_overcoat),
            jam=_yes_no(args.jam),
            through_backing=_yes_no(args.through_backing),
            max_offset_mm=args.max_offset_mm,
            notes=args.notes,
        )
        print(json.dumps(result, indent=2))
        return

    with worker_lock(db.data_dir()):
        job = prepare_test(
            service,
            args.variant,
            args.sheet_kind,
            args.idempotency_key,
            args.source_job_id,
            args.laminate_description,
            args.outline,
        )
        render = service.get(db.renders, job["render_id"])["data"]
        summary = {
            "job_id": job["id"],
            "state": job["state"],
            "variant": args.variant,
            "passes": 1,
            "knife_pressure": job["request"]["profile"]["knife_pressure"],
            "sheet_kind": args.sheet_kind,
            "laminate_description": args.laminate_description,
            "outline": args.outline,
            "artifacts": render["pages"][0]["artifacts"],
            "declaration": job["request"]["declaration"],
            "transfers": ["cut.plt"],
        }
        print(json.dumps(summary, indent=2), flush=True)
        if not args.execute:
            return
        phrase = (
            f"CUT ONLY {args.variant} {args.sheet_kind} {args.outline} KP42"
        )
        if input(f"Type {phrase!r} to create one irreversible device attempt: ") != phrase:
            raise RuntimeError("Confirmation did not match; no device request was sent")

        import usb.core
        import usb.util

        from .discovery import scan_usb

        candidates = scan_usb()["devices"]
        if len(candidates) != 1:
            raise RuntimeError("Exactly one PixCut must be connected")
        selected = candidates[0]
        device = usb.core.find(
            idVendor=0x302C,
            idProduct=0x3101,
            bus=selected["bus"],
            address=selected["address"],
        )
        if device is None:
            raise RuntimeError("PixCut not visible to libusb")
        with PhotoSession(device, usb.core, usb.util) as session:
            result = execute_test(service, job, session)
        print(
            json.dumps(
                {
                    "job_id": result["id"],
                    "state": result["state"],
                    "sheets": result["sheets"],
                },
                indent=2,
            )
        )
        print(
            "Record what physically happened with: "
            f"scripts/cut-only-test.py observe {job['id']} ...",
            flush=True,
        )


if __name__ == "__main__":
    main()
