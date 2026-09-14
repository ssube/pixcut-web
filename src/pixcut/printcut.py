"""One-sheet 4x7 sticker print-and-cut support.

Protocol and native rectangle transform are reimplemented from observations in
eastbay-pixcut-s1 a73cd65b7374e5b9e1eb7a0c54594f3276c14f8f.
"""

import hashlib
import json
import math
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from . import db
from .artifacts import canonical, digest
from .commissioning import PhotoSession, checkpoint, jpeg
from .probe import QUERIES
from .service import Problem, Service, now, uid
from .worker import worker_lock

PROFILE = {
    "id": "sticker-4x7-jpeg",
    "execution_mode": "hardware",
    "commissioning": True,
    "hardware_validated": False,
    "validation": {
        "printer_model": "DHP700",
        "firmware": "1.0.34_0073",
        "rectangular_baseline": {
            "device_job_id": 17,
            "validated_at": "2026-09-10 America/Chicago",
            "result": "12 rectangular contours completed and operator confirmed correct output",
        },
        "arbitrary_path": "outer contours",
    },
    "mode": "print_cut",
    "media_size": 5013,
    "media_type": 2030,
    "job_type": 600,
    "channel": 2,
    "raster": [1200, 2100],
    "page_mm": [101.6, 177.8],
    "image_format": "jpeg",
    "jpeg_quality": 92,
    "jpeg_subsampling": 0,
    "document_format": 9,
    "max_image_bytes": 1024 * 1024,
    "cut_format": "pixcut-native-polygon-plt-v2",
    "knife_pressure": 42,
    "overcut": {"steps": 3, "max_degree": 45, "max_length_px": 15},
    "data_order": "plt_then_jpeg",
    "chunk_payload": 10215,
    "usb_write_size": 1024,
    "chunk_delay_s": 0.02,
    "protocol_reference_commit": "a73cd65b7374e5b9e1eb7a0c54594f3276c14f8f",
}

PLOT_X_ORIGIN = 7153.032085400178
PLOT_X_SCALE = 3.430810507522827
PLOT_Y_ORIGIN = -24.928517197806514
PLOT_Y_SCALE = 3.4315979710374447
PLOT_TRAILER = (6476, 0)


def image_point_to_plot(x_px, y_px):
    return (
        round(PLOT_X_ORIGIN - y_px * PLOT_X_SCALE),
        round(PLOT_Y_ORIGIN + x_px * PLOT_Y_SCALE),
    )


def _unit(vector):
    length = math.hypot(*vector)
    return (0.0, 0.0) if length == 0 else (vector[0] / length, vector[1] / length)


def _perpendicular_toward(along, toward):
    dot = along[0] * toward[0] + along[1] * toward[1]
    rejected = (toward[0] - along[0] * dot, toward[1] - along[1] * dot)
    if math.hypot(*rejected) < 1e-9:
        return (-along[1], along[0])
    return _unit(rejected)


def _ramp(anchor, along, toward, reverse=False):
    settings = PROFILE["overcut"]
    perpendicular = _perpendicular_toward(along, toward)
    points = []
    for step in range(1, settings["steps"] + 1):
        angle = math.radians(settings["max_degree"] * step / settings["steps"])
        radius = settings["max_length_px"] * step / settings["steps"]
        points.append(
            (
                math.floor(
                    anchor[0]
                    + radius
                    * (along[0] * math.cos(angle) + perpendicular[0] * math.sin(angle))
                ),
                math.floor(
                    anchor[1]
                    + radius
                    * (along[1] * math.cos(angle) + perpendicular[1] * math.sin(angle))
                ),
            )
        )
    return list(reversed(points)) if reverse else points


def overcut_ramp(contour):
    """Add stock-style exterior lead ramps around a closed contour seam."""
    if len(contour) < 2:
        return list(contour)
    first, previous, next_point = contour[0], contour[-1], contour[1]
    closing = _unit((first[0] - previous[0], first[1] - previous[1]))
    back_outgoing = _unit((first[0] - next_point[0], first[1] - next_point[1]))
    if closing == (0.0, 0.0):
        return [*contour, first]
    inward = (-closing[0], -closing[1])
    return [
        *_ramp(first, inward, back_outgoing, reverse=True),
        *contour,
        first,
        *_ramp(first, closing, back_outgoing),
    ]


def geometry_to_plt(snapshot):
    if (
        snapshot.get("page_mm") != PROFILE["page_mm"]
        or snapshot.get("coordinate_space") != "sheet_design"
        or snapshot.get("origin") != "top_left"
        or snapshot.get("units") != "mm"
    ):
        raise ValueError("Cut geometry is not the approved 4x7 sheet coordinate space")
    sx = PROFILE["raster"][0] / PROFILE["page_mm"][0]
    sy = PROFILE["raster"][1] / PROFILE["page_mm"][1]
    paths = []
    for shape in snapshot.get("shapes", []):
        points = shape.get("outer", {}).get("points", [])
        if shape.get("holes"):
            raise ValueError("Hardware contour cutting does not support holes yet")
        if not 3 <= len(points) <= 2000:
            raise ValueError("Hardware contours require 3 to 2000 points")
        xs = sorted({round(float(p[0]), 9) for p in points})
        ys = sorted({round(float(p[1]), 9) for p in points})
        normalized = {
            (round(float(p[0]), 9), round(float(p[1]), 9)) for p in points
        }
        rectangle = (
            len(points) == 4
            and len(xs) == 2
            and len(ys) == 2
            and normalized
            == {
                (xs[0], ys[0]),
                (xs[1], ys[0]),
                (xs[1], ys[1]),
                (xs[0], ys[1]),
            }
        )
        if rectangle:
            x0, x1 = round(xs[0] * sx), round(xs[1] * sx)
            y0, y1 = round(ys[0] * sy), round(ys[1] * sy)
            if not (0 <= x0 < x1 <= 1200 and 0 <= y0 < y1 <= 2100):
                raise ValueError("Cut rectangle falls outside the 4x7 raster")
            raster = [
                (x0, y1),
                (x0, y0),
                (x1, y0),
                (x1, y1),
            ]
        else:
            raster = [
                (round(float(x) * sx), round(float(y) * sy)) for x, y in points
            ]
            raster = [
                point
                for index, point in enumerate(raster)
                if not index or point != raster[index - 1]
            ]
            if len(raster) >= 2 and raster[0] == raster[-1]:
                raster.pop()
            if len(set(raster)) < 3 or any(
                not (0 <= x <= 1200 and 0 <= y <= 2100) for x, y in raster
            ):
                raise ValueError(
                    "Cut contour is degenerate or falls outside the 4x7 raster"
                )
            start_index = min(
                range(len(raster)),
                key=lambda index: (raster[index][0], -raster[index][1]),
            )
            raster = raster[start_index:] + raster[:start_index]
        seam = image_point_to_plot(*raster[0])
        cut = [image_point_to_plot(x, y) for x, y in overcut_ramp(raster)]
        paths.append((seam, cut))
    if not paths:
        raise ValueError("At least one cut contour is required")
    parts = [f'IN VER0.1.0 KP{PROFILE["knife_pressure"]} ']
    for _, points in sorted(paths):
        parts.append(f"U{points[0][0]},{points[0][1]} ")
        parts.extend(f"D{x},{y} " for x, y in points)
    parts.append(f"U{PLOT_TRAILER[0]},{PLOT_TRAILER[1]}  @ ")
    return "".join(parts).encode("ascii")


def combo_declaration(image, plot):
    common = {
        "channel": PROFILE["channel"],
        "copies": 1,
        "media-size": PROFILE["media_size"],
        "media-type": PROFILE["media_type"],
        "job-type": PROFILE["job_type"],
    }
    return {
        "method": "combo-job",
        "params": [
            {
                "method": "print-job",
                "params": {**common, "file-size": len(image)},
            },
            {
                "method": "cut-job",
                "params": {"file-size": len(plot), "job-type": PROFILE["job_type"]},
            },
        ],
    }


def prepare(service, source_render_id, key, dispatch=False):
    source = service.get(db.renders, source_render_id)["data"]
    if (
        source.get("profile", {}).get("id") != "simulator-v1"
        or len(source.get("pages", [])) != 1
        or source["pages"][0].get("sticker_count", 0) < 1
    ):
        raise ValueError("Select one nonempty reviewed simulator page")
    request = {
        "source_render_id": source_render_id,
        "source_render_revision": source["revision"],
        "profile": PROFILE,
        "copies": 1,
        "operator_confirmed_preview": True,
        "operator_confirmed_media": "4x7 sticker paper",
    }
    request_hash = digest(canonical(request))
    with service.engine.connect() as c:
        old = c.execute(
            select(db.jobs).where(db.jobs.c.idempotency_key == key)
        ).mappings().first()
    if old:
        if old["request_hash"] != request_hash:
            raise Problem("IDEMPOTENCY_CONFLICT", "Key belongs to another test", 409)
        return service.job(old["id"])
    page = source["pages"][0]
    image_png = service.store.read(page["artifacts"]["image.png"]["hash"])
    geometry_bytes = service.store.read(
        page["artifacts"]["cut-geometry.json"]["hash"]
    )
    geometry = json.loads(geometry_bytes)
    with Image.open(BytesIO(image_png)) as decoded:
        decoded.load()
        if decoded.mode != "RGB" or list(decoded.size) != PROFILE["raster"]:
            raise ValueError("Reviewed raster is not RGB 1200x2100")
        image = jpeg(
            decoded,
            quality=PROFILE["jpeg_quality"],
            subsampling=PROFILE["jpeg_subsampling"],
        )
    if len(image) > PROFILE["max_image_bytes"]:
        raise ValueError("Sticker JPEG exceeds the 1 MiB printer limit")
    plot = geometry_to_plt(geometry)
    declaration = combo_declaration(image, plot)
    files = {
        "image.jpg": service.store.put(image, "image/jpeg"),
        "cut.plt": service.store.put(plot, "application/vnd.hp-hpgl"),
        "cut-geometry.json": service.store.put(
            geometry_bytes, "application/json"
        ),
    }
    if "thumbnail.png" in page["artifacts"]:
        files["thumbnail.png"] = page["artifacts"]["thumbnail.png"]
    manifest = {
        "schema_version": "1.0",
        "profile": PROFILE,
        "source_render_id": source_render_id,
        "declaration": declaration,
        "data_order": ["cut.plt", "image.jpg"],
        "artifacts": files,
        "hashes": {
            "image_sha1": hashlib.sha1(image).hexdigest(),
            "plot_sha1": hashlib.sha1(plot).hexdigest(),
            "combined_sha256": digest(plot + image),
        },
        "operator_authorization": request,
    }
    files["manifest.json"] = service.store.put(
        canonical(manifest), "application/json"
    )
    job_id, render_id, sheet_id = uid(), uid(), uid()
    render = {
        "id": render_id,
        "project_id": source["project_id"],
        "revision": source["revision"],
        "created_at": now(),
        "profile": PROFILE,
        "pages": [
            {
                "index": 0,
                "sticker_count": page["sticker_count"],
                "raster_hash": digest(image),
                "artifacts": files,
            }
        ],
        "hardware_eligible": False,
        "commissioning": True,
        "source_render_id": source_render_id,
        "warnings": [],
    }
    initial_state = "queued" if dispatch else "held"
    with service.engine.begin() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        c.execute(
            db.renders.insert().values(
                id=render_id,
                project_id=source["project_id"],
                revision=source["revision"],
                data=render,
            )
        )
        for name, artifact in files.items():
            service.register(c, artifact)
            c.execute(
                db.render_files.insert().values(
                    render_id=render_id, name=f"0/{name}", hash=artifact["hash"]
                )
            )
        c.execute(
            db.jobs.insert().values(
                id=job_id,
                created_at=now(),
                name="4×7 sticker sheet",
                render_id=render_id,
                idempotency_key=key,
                request_hash=request_hash,
                execution_mode="hardware",
                state=initial_state,
                request=request,
            )
        )
        c.execute(
            db.sheets.insert().values(
                id=sheet_id,
                job_id=job_id,
                ordinal=0,
                page=0,
                copy=0,
                state=initial_state,
            )
        )
        service.event(
            c,
            job_id,
            initial_state,
            sheet_id,
            "operator",
            {"commissioning": True, "declaration": declaration},
        )
    return service.job(job_id)


def execute(service, job, session, progress=print, heartbeat=lambda: None):
    if (
        job["execution_mode"] != "hardware"
        or job["request"].get("profile") != PROFILE
        or len(job["sheets"]) != 1
        or job["state"] not in ("held", "queued")
    ):
        raise ValueError("Not a never-started queued print-and-cut job")
    with service.engine.connect() as c:
        if c.execute(
            select(db.attempts.c.id).where(
                db.attempts.c.sheet_id == job["sheets"][0]["id"]
            )
        ).first():
            raise RuntimeError("This physical sheet already has an attempt")
        if c.execute(
            select(db.jobs.c.id).where(
                db.jobs.c.execution_mode == "hardware",
                db.jobs.c.state.in_(["creating", "transferring", "processing", "uncertain"]),
            )
        ).first():
            raise RuntimeError("Unresolved hardware activity holds dispatch")
    render = service.get(db.renders, job["render_id"])["data"]
    files = render["pages"][0]["artifacts"]
    image = service.store.read(files["image.jpg"]["hash"])
    plot = service.store.read(files["cut.plt"]["hash"])
    manifest = json.loads(service.store.read(files["manifest.json"]["hash"]))
    if manifest["declaration"] != combo_declaration(image, plot):
        raise ValueError("Frozen combo declaration integrity mismatch")
    if manifest["hashes"]["combined_sha256"] != digest(plot + image):
        raise ValueError("Frozen combined payload integrity mismatch")
    identity = session.properties(QUERIES[0][1])
    media = session.properties(QUERIES[1][1])
    status = session.properties(QUERIES[2][1])
    if identity.get("result", [None, None, None])[2] != "DHP700":
        raise RuntimeError(f"Unexpected printer identity: {identity}")
    if not (
        isinstance(status.get("result"), list)
        and str(status["result"][0]) in ("20", "30")
        and status["result"][2] == "::0"
    ):
        raise RuntimeError(f"Printer is not idle/asleep without alerts: {status}")
    intent = False
    try:
        checkpoint(
            service,
            job,
            "creating",
            {
                "identity": identity,
                "media": media,
                "status": status,
                "operator_confirmed_media": "4x7 sticker paper",
                "profile": PROFILE,
            },
            intent=True,
        )
        intent = True
        progress("Creation intent committed; sending one combo-job", flush=True)
        response = session.rpc(manifest["declaration"])
        result = response["result"]
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
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
            {"creation_response": response},
            device_id=device_id,
        )
        payload = plot + image
        started = last_report = last_heartbeat = time.monotonic()
        for offset in range(0, len(payload), PROFILE["chunk_payload"]):
            if time.monotonic() - started > 360:
                raise TimeoutError("Bounded upload deadline reached")
            chunk = payload[offset : offset + PROFILE["chunk_payload"]]
            session.send_chunk(chunk, device_id)
            time.sleep(PROFILE["chunk_delay_s"])
            if time.monotonic() - last_heartbeat >= 3:
                heartbeat()
                last_heartbeat = time.monotonic()
            if time.monotonic() - last_report >= 10:
                sent = offset + len(chunk)
                checkpoint(
                    service,
                    job,
                    "transferring",
                    {"acknowledged_bytes": sent, "total_bytes": len(payload)},
                )
                progress(f"Combo transfer {sent}/{len(payload)} bytes acknowledged", flush=True)
                last_report = time.monotonic()
        checkpoint(service, job, "processing", {"acknowledged_bytes": len(payload)})
        progress(f"Combo transferred; observing device job {device_id}", flush=True)
        deadline, previous = time.monotonic() + 480, None
        while time.monotonic() < deadline:
            heartbeat()
            info = session.rpc({"method": "get-job-info", "params": {"job-id": device_id}})
            value = info["result"]
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            if not isinstance(value, dict) or str(value.get("job-id")) != str(device_id):
                raise RuntimeError(f"Unexpected job observation: {info}")
            state = tuple(
                str(value.get(k))
                for k in ("job-state", "job-sub-state", "job-state-reason")
            )
            cut = (value.get("cutting-progress"), value.get("cut-contours"))
            observation = (state, cut)
            if observation != previous:
                checkpoint(service, job, "processing", {"job_info": info})
                progress(f"Device state {state}; cutting {cut}", flush=True)
                previous = observation
            if state == ("9", "9000", "90001"):
                checkpoint(
                    service,
                    job,
                    "completed",
                    {
                        "job_info": info,
                        "physical_output_verified": False,
                        "profile_still_unvalidated": not PROFILE["hardware_validated"],
                    },
                )
                return service.job(job["id"])
            if state[0] == "7":
                checkpoint(service, job, "failed", {"job_info": info})
                return service.job(job["id"])
            current = session.properties(QUERIES[2][1])
            if isinstance(current.get("result"), list) and str(current["result"][0]) == "60":
                raise RuntimeError(f"Device error while processing: {current}")
            time.sleep(2)
        raise TimeoutError("Completion not observed within commissioning deadline")
    except BaseException as exc:
        if intent:
            from .commissioning import transfer_recovery_evidence

            evidence = {"error": str(exc), "automatic_retry": False}
            evidence.update(transfer_recovery_evidence(exc))
            checkpoint(
                service,
                job,
                "uncertain",
                evidence,
            )
        raise


class HardwareWorker:
    """Exclusive dispatcher for validated browser-created hardware jobs."""

    def __init__(self, service):
        self.service = service

    def next_job(self):
        from .commissioning import PROFILE as PHOTO_PROFILE

        with self.service.engine.connect() as c:
            if c.execute(
                select(db.jobs.c.id)
                .where(
                    db.jobs.c.execution_mode == "hardware",
                    db.jobs.c.state == "uncertain",
                )
                .limit(1)
            ).first():
                return None
            rows = c.execute(
                select(db.jobs)
                .where(
                    db.jobs.c.execution_mode == "hardware",
                    db.jobs.c.state == "queued",
                )
                .order_by(db.jobs.c.created_at, db.jobs.c.id)
            ).mappings()
            for row in rows:
                if row["request"].get("profile") in (PROFILE, PHOTO_PROFILE):
                    return self.service.job(row["id"])
        return None

    def run_once(self):
        job = self.next_job()
        if job is None:
            return False
        import usb.core
        import usb.util

        from .discovery import scan_usb
        from .commissioning import PROFILE as PHOTO_PROFILE, execute_photo

        self.service.worker_heartbeat("hardware", "working", job["id"])
        keep_state = False
        try:
            candidates = scan_usb()["devices"]
            if len(candidates) != 1:
                raise RuntimeError(
                    "Hardware worker requires exactly one connected PixCut"
                )
            selected = candidates[0]
            device = usb.core.find(
                idVendor=0x302C,
                idProduct=0x3101,
                bus=selected["bus"],
                address=selected["address"],
            )
            if device is None:
                raise RuntimeError("PixCut is not visible to libusb")
            with PhotoSession(device, usb.core, usb.util) as session:
                if job["request"].get("profile") == PHOTO_PROFILE:
                    execute_photo(
                        self.service,
                        job,
                        session,
                        heartbeat=lambda: self.service.worker_heartbeat(
                            "hardware", "working", job["id"]
                        ),
                    )
                else:
                    execute(
                        self.service,
                        job,
                        session,
                        heartbeat=lambda: self.service.worker_heartbeat(
                            "hardware", "working", job["id"]
                        ),
                    )
        except Exception as exc:
            if self.service.hold_hardware_job(job["id"], str(exc)):
                return False
            current = self.service.job(job["id"])
            if current["state"] == "uncertain":
                self.service.worker_heartbeat(
                    "hardware", "blocked", job["id"], str(exc)[:2000]
                )
                keep_state = True
                return False
            raise
        finally:
            if not keep_state:
                self.service.worker_heartbeat("hardware", "idle")
        return True

    def run(self, once=False):
        self.service.worker_heartbeat("hardware", "starting")
        last_heartbeat = 0.0
        try:
            while True:
                attention = self.service.hardware_attention_job()
                current = time.monotonic()
                if attention:
                    if current - last_heartbeat >= 2:
                        self.service.worker_heartbeat(
                            "hardware",
                            "blocked",
                            attention,
                            "Printer restart and sheet resolution required",
                        )
                        last_heartbeat = current
                    worked = False
                else:
                    if current - last_heartbeat >= 2:
                        self.service.worker_heartbeat("hardware", "idle")
                        last_heartbeat = current
                    worked = self.run_once()
                if once:
                    return
                if not worked:
                    time.sleep(0.5)
        finally:
            self.service.worker_heartbeat("hardware", "stopped")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="One-sheet 4x7 rectangular print-and-cut commissioning"
    )
    parser.add_argument("source_render_id")
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--confirm-reviewed-preview", action="store_true", required=True)
    parser.add_argument("--confirm-4x7-sticker-paper", action="store_true", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    service = Service(db.data_dir())
    with worker_lock(db.data_dir()):
        job = prepare(service, args.source_render_id, args.idempotency_key)
        render = service.get(db.renders, job["render_id"])["data"]
        print(
            json.dumps(
                {
                    "job_id": job["id"],
                    "state": job["state"],
                    "render_id": job["render_id"],
                    "source_render_id": args.source_render_id,
                    "profile": PROFILE,
                    "artifacts": render["pages"][0]["artifacts"],
                },
                indent=2,
            ),
            flush=True,
        )
        if not args.execute:
            return
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
            result = execute(service, job, session)
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


if __name__ == "__main__":
    main()
