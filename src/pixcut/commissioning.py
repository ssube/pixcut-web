"""One-sheet, operator-authorized photo payload testing, isolated from production.

Request schema: PixCut-App bc243ce63d9f458b818cb9e19b3c93f281b85f7a.
USB transfer: eastbay-pixcut-s1 a73cd65b7374e5b9e1eb7a0c54594f3276c14f8f.
No cut command, PLT upload, fallback encoder, or automatic attempt replay exists.
"""

import hashlib
import json
import secrets
import struct
import time
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import select, update

from . import db
from .artifacts import canonical, digest
from .imaging import png
from .probe import Frames, PREFIX, PROTOCOL_COMMIT, QUERIES
from .service import Service, Problem, now, uid
from .worker import worker_lock

PROFILE = {
    "id": "photo-4x6-jpeg",
    "execution_mode": "hardware",
    "commissioning": False,
    "hardware_validated": True,
    "validation": {
        "printer_model": "DHP700",
        "firmware": "1.0.34_0073",
        "device_job_id": 16,
        "validated_at": "2026-09-10 America/Chicago",
        "result": "device completed and operator confirmed correct physical print",
    },
    "mode": "print_only",
    "media_size": 5012,
    "media_type": 2010,
    "job_type": 0,
    "raster": [1200, 1800],
    "page_mm": [101.6, 152.4],
    "document_format": 9,
    "image_format": "jpeg",
    "jpeg_quality": 95,
    "jpeg_subsampling": 0,
    "max_image_bytes": 1024 * 1024,
    "framing": "official-sdk-extlen-excludes-job-id",
    "chunk_payload": 10215,
    "usb_write_size": 1024,
    "chunk_delay_s": 0.02,
    "protocol_references": {
        "request": PROTOCOL_COMMIT,
        "transport": "a73cd65b7374e5b9e1eb7a0c54594f3276c14f8f",
    },
}


def declaration(data, job_id, job_send_time, profile=None):
    profile = profile or PROFILE
    extension = "png" if profile["image_format"] == "png" else "jpg"
    return {
        "method": "print-job",
        "params": {
            "media-size": 5012,
            "media-type": 2010,
            "job-type": 0,
            "channel": 14864,
            "file-size": len(data),
            "document-format": profile["document_format"],
            "document-name": f"{job_id.replace('-', '')}.{extension}",
            "hash-method": 1,
            "hash-value": hashlib.sha1(data).hexdigest(),
            "user-account": "12345678",
            "job-send-time": job_send_time,
            "copies": 1,
        },
    }


def jpeg(image, quality=None, subsampling=None):
    out = BytesIO()
    image.save(
        out,
        format="JPEG",
        quality=quality if quality is not None else PROFILE["jpeg_quality"],
        subsampling=(
            subsampling if subsampling is not None else PROFILE["jpeg_subsampling"]
        ),
        optimize=True,
        dpi=(300, 300),
    )
    encoded = out.getvalue()
    with Image.open(BytesIO(encoded)) as decoded:
        if (
            decoded.format != "JPEG"
            or decoded.mode != "RGB"
            or decoded.size != image.size
        ):
            raise ValueError("JPEG commissioning encoder verification failed")
    return encoded


PNG_COMPRESSION_TYPES = {
    "filtered": zlib.Z_FILTERED,
    "huffman": zlib.Z_HUFFMAN_ONLY,
    "rle": zlib.Z_RLE,
}


def lossless_png(image, strategy="filtered"):
    if strategy not in PNG_COMPRESSION_TYPES:
        raise ValueError("PNG strategy must be filtered, huffman, or rle")
    out = BytesIO()
    image.save(
        out,
        format="PNG",
        compress_level=6,
        compress_type=PNG_COMPRESSION_TYPES[strategy],
        dpi=(300, 300),
    )
    encoded = out.getvalue()
    with Image.open(BytesIO(encoded)) as decoded:
        if (
            decoded.format != "PNG"
            or decoded.mode != image.mode
            or decoded.size != image.size
            or decoded.tobytes() != image.tobytes()
        ):
            raise ValueError("PNG payload encoder verification failed")
    return encoded


def photo_test_profile(
    raster, quality=None, image_format="jpeg", png_strategy=None
):
    raster = [int(value) for value in raster]
    image_format = str(image_format).lower()
    if (
        len(raster) != 2
        or not 300 <= raster[0] <= 2400
        or not 450 <= raster[1] <= 3600
        or raster[0] * 3 != raster[1] * 2
    ):
        raise ValueError(
            "Photo test raster must be a 2:3 portrait size from 300x450 to 2400x3600"
        )
    if image_format not in ("jpeg", "png"):
        raise ValueError("Photo test format must be JPEG or PNG")
    if image_format == "jpeg":
        quality = int(quality)
        if not 50 <= quality <= 100:
            raise ValueError("Photo test JPEG quality must be between 50 and 100")
    elif quality is not None:
        raise ValueError("PNG test points do not have a JPEG quality")
    if image_format == "png":
        if raster != PROFILE["raster"]:
            raise ValueError("PNG payload tests must be 1200x1800 at 300 DPI")
        png_strategy = png_strategy or "huffman"
        if png_strategy not in PNG_COMPRESSION_TYPES:
            raise ValueError("PNG strategy must be filtered, huffman, or rle")
    elif png_strategy is not None:
        raise ValueError("PNG strategy only applies to PNG test points")
    if (
        image_format == "jpeg"
        and raster == PROFILE["raster"]
        and quality == PROFILE["jpeg_quality"]
    ):
        return PROFILE
    if image_format == "png":
        return {
            **PROFILE,
            "id": "photo-4x6-png-test",
            "commissioning": True,
            "hardware_validated": False,
            "validation": {
                "base_profile": PROFILE["id"],
                "status": "single-point PNG payload test; requires physical review",
            },
            "raster": raster,
            "image_format": "png",
            "document_format": 10,
            "jpeg_quality": None,
            "jpeg_subsampling": None,
            "png_strategy": png_strategy,
            "max_image_bytes": 4 * 1024 * 1024,
        }
    return {
        **PROFILE,
        "id": "photo-4x6-jpeg-test",
        "commissioning": True,
        "hardware_validated": False,
        "validation": {
            "base_profile": PROFILE["id"],
            "status": "single-point JPEG payload test; requires physical review",
        },
        "raster": raster,
        "jpeg_quality": quality,
        "max_image_bytes": 4 * 1024 * 1024,
    }


def is_photo_test_profile(profile):
    if not isinstance(profile, dict):
        return False
    if profile == PROFILE:
        return True
    if profile.get("id") not in (
        "photo-4x6-jpeg-test",
        "photo-4x6-png-test",
    ):
        return False
    try:
        expected = photo_test_profile(
            profile["raster"],
            profile.get("jpeg_quality"),
            profile.get("image_format", "jpeg"),
            profile.get("png_strategy"),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return profile == expected


def prepare_photo(
    service,
    path,
    key,
    raster=None,
    quality=None,
    image_format="jpeg",
    png_strategy=None,
):
    profile = photo_test_profile(
        raster or PROFILE["raster"],
        (
            PROFILE["jpeg_quality"]
            if quality is None and image_format == "jpeg"
            else quality
        ),
        image_format,
        png_strategy,
    )
    with path.open("rb") as source:
        raw = source.read(25 * 1024 * 1024 + 1)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("Photo source exceeds 25 MiB")
    request = {
        "source_hash": digest(raw),
        "profile": profile,
        "mode": "print_only",
        "commissioning": True,
        "operator_confirmed_media": "4x6 photo paper",
        "copies": 1,
    }
    request_hash = digest(canonical(request))
    with service.engine.connect() as c:
        old = (
            c.execute(select(db.jobs).where(db.jobs.c.idempotency_key == key))
            .mappings()
            .first()
        )
    if old:
        if old["request_hash"] != request_hash:
            raise Problem(
                "IDEMPOTENCY_CONFLICT", "Key belongs to a different test", 409
            )
        return service.job(old["id"])
    asset = service.upload(raw)
    with Image.open(
        BytesIO(service.store.read(asset["normalized"]["hash"]))
    ) as original:
        # Contain without distortion; white letterboxing for other aspect ratios.
        target = tuple(profile["raster"])
        art = ImageOps.contain(original, target, Image.Resampling.LANCZOS)
        master = Image.new("RGB", target, "white")
        master.paste(
            art,
            ((target[0] - art.width) // 2, (target[1] - art.height) // 2),
            art.getchannel("A"),
        )
    payload = (
        jpeg(master, quality=profile["jpeg_quality"])
        if profile["image_format"] == "jpeg"
        else lossless_png(master, profile["png_strategy"])
    )
    if len(payload) > profile["max_image_bytes"]:
        raise ValueError(
            f"{profile['image_format'].upper()} exceeds the "
            f"{profile['max_image_bytes'] // (1024 * 1024)} MiB payload-test limit"
        )
    job_id, render_id, project_id, sheet_id = uid(), uid(), uid(), uid()
    print_request = declaration(payload, job_id, int(time.time()), profile)
    geometry = {
        "schema_version": "1.0",
        "geometry_engine": "photo-no-cuts-v1",
        "provenance": {
            "kind": "render",
            "project_id": project_id,
            "project_revision": 1,
            "render_id": render_id,
        },
        "page_id": "photo",
        "page_mm": [101.6, 152.4],
        "units": "mm",
        "coordinate_space": "sheet_design",
        "origin": "top_left",
        "x_direction": "right",
        "y_direction": "down",
        "geometry_stage": "finished_cut_pre_device",
        "shapes": [],
    }
    thumb = master.copy()
    thumb.thumbnail((240, 360))
    image_name = "image.png" if profile["image_format"] == "png" else "image.jpg"
    image_mime = "image/png" if profile["image_format"] == "png" else "image/jpeg"
    outputs = {
        image_name: (payload, image_mime),
        "thumbnail.png": (png(thumb), "image/png"),
        "cut-geometry.json": (canonical(geometry), "application/json"),
        "overlay.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 101.6 152.4"/>',
            "image/svg+xml",
        ),
    }
    files = {
        name: service.store.put(data, mime) for name, (data, mime) in outputs.items()
    }
    manifest = {
        "schema_version": "1.0",
        "profile": profile,
        "source_asset": asset,
        "render_id": render_id,
        "project_id": project_id,
        "revision": 1,
        "declaration": print_request,
        "artifacts": dict(files),
        "raster_hash": digest(master.tobytes()),
        "raster_mode": "RGB",
        "renderer": "pixcut-photo-v1",
        "pillow_version": Image.__version__,
        "sizing": "contain-centered-white",
        "operator_authorization": request,
    }
    files["manifest.json"] = service.store.put(canonical(manifest), "application/json")
    render = {
        "id": render_id,
        "project_id": project_id,
        "revision": 1,
        "profile": profile,
        "hardware_eligible": False,
        "commissioning": True,
        "created_at": now(),
        "pages": [
            {
                "index": 0,
                "sticker_count": 0,
                "raster_hash": manifest["raster_hash"],
                "artifacts": files,
            }
        ],
        "warnings": ["Photo payload test; print only, no cuts."],
    }
    project = {
        "id": project_id,
        "name": "Photo commissioning",
        "revision": 1,
        "bindings": {},
        "pages": [],
        "commissioning": True,
        "source_asset_id": asset["id"],
    }
    with service.engine.begin() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        c.execute(db.projects.insert().values(id=project_id, revision=1, data=project))
        c.execute(
            db.renders.insert().values(
                id=render_id, project_id=project_id, revision=1, data=render
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
                name=(
                    f"{path.stem} — {profile['raster'][0]}x{profile['raster'][1]} "
                    + (
                        f"Q{profile['jpeg_quality']} JPEG photo test"
                        if profile["image_format"] == "jpeg"
                        else "PNG photo test"
                    )
                ),
                render_id=render_id,
                idempotency_key=key,
                request_hash=request_hash,
                execution_mode="hardware",
                state="held",
                request=request,
            )
        )
        c.execute(
            db.sheets.insert().values(
                id=sheet_id, job_id=job_id, ordinal=0, page=0, copy=0, state="held"
            )
        )
        service.event(
            c,
            job_id,
            "held",
            sheet_id,
            "operator",
            {"commissioning": True, "declaration": print_request},
        )
    return service.job(job_id)


def prepare_render(service, render_id, key, name="4x6 photo", dispatch=False):
    """Freeze one already-reviewed public photo render for hardware dispatch."""
    render = service.verify_render(render_id)
    if (
        render.get("kind") != "photo"
        or render.get("profile") != PROFILE
        or not render.get("hardware_eligible")
        or len(render.get("pages", [])) != 1
    ):
        raise Problem(
            "PHOTO_RENDER_NOT_ELIGIBLE",
            "Prepare and review an exact 4x6 photo render before printing",
            409,
        )
    files = render["pages"][0]["artifacts"]
    geometry = json.loads(service.store.read(files["cut-geometry.json"]["hash"]))
    if geometry.get("shapes") != [] or "cut.plt" in files:
        raise Problem(
            "PHOTO_RENDER_HAS_CUTS",
            "Photo printing cannot contain cut commands",
            409,
        )
    payload = service.store.read(files["image.jpg"]["hash"])
    if len(payload) > PROFILE["max_image_bytes"]:
        raise Problem(
            "PHOTO_PAYLOAD_TOO_LARGE",
            "Exact JPEG exceeds the 1 MiB printer limit",
        )
    base_request = {
        "source_render_id": render_id,
        "source_project_revision": render["revision"],
        "name": name,
        "profile": PROFILE,
        "mode": "print_only",
        "operator_confirmed_preview": True,
        "operator_confirmed_media": "4x6 photo paper",
        "copies": 1,
    }
    request_hash = digest(canonical(base_request))
    with service.engine.connect() as c:
        old = c.execute(
            select(db.jobs).where(db.jobs.c.idempotency_key == key)
        ).mappings().first()
    if old:
        if old["request_hash"] != request_hash:
            raise Problem("IDEMPOTENCY_CONFLICT", "Key belongs to another print", 409)
        return service.job(old["id"])
    job_id, sheet_id = uid(), uid()
    request = base_request | {
        "declaration": declaration(payload, job_id, int(time.time()))
    }
    initial_state = "queued" if dispatch else "held"
    with service.engine.begin() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        old = c.execute(
            select(db.jobs).where(db.jobs.c.idempotency_key == key)
        ).mappings().first()
        if old:
            if old["request_hash"] != request_hash:
                raise Problem(
                    "IDEMPOTENCY_CONFLICT", "Key belongs to another print", 409
                )
            job_id = old["id"]
        else:
            c.execute(
                db.jobs.insert().values(
                    id=job_id,
                    created_at=now(),
                    name=name,
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
                {"print_only": True, "declaration": request["declaration"]},
            )
    return service.job(job_id)


class PhotoSession:
    def __init__(self, device, core, util):
        self.device, self.core, self.util = device, core, util
        self.claimed = []
        self.sequence = secrets.randbelow(1_000_000) + 1000
        self.frames = Frames()

    def __enter__(self):
        try:
            self.device.default_timeout = 1000
            configuration = self.device.get_active_configuration()
            for number, required in ((2, (0x06, 0x86)), (3, (0x04, 0x84))):
                endpoints = {
                    e.bEndpointAddress: e.bmAttributes & 3
                    for e in configuration[(number, 0)]
                }
                if any(endpoints.get(address) != 2 for address in required):
                    raise RuntimeError("Unexpected active bulk endpoints")
                if self.device.is_kernel_driver_active(number):
                    raise RuntimeError(
                        "Required interface is bound; no driver will be detached"
                    )
                self.util.claim_interface(self.device, number)
                self.claimed.append(number)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            for number in reversed(self.claimed):
                self.util.release_interface(self.device, number)
        finally:
            self.util.dispose_resources(self.device)

    def rpc(self, request, timeout=5):
        # Private bounded use only; there is no public raw-command endpoint.
        if request["method"] not in (
            "get-prop",
            "get-job-info",
            "print-job",
            "combo-job",
            "cut-job",
        ):
            raise ValueError("Unsupported commissioning method")
        self.sequence += 1
        obj = {**request, "id": self.sequence}
        wire = PREFIX + canonical(obj)
        if self.device.write(0x06, wire, timeout=1000) != len(wire):
            raise RuntimeError("Short command write; will not retry")
        deadline = time.monotonic() + timeout
        self.frames.total = len(self.frames.buffer)
        while time.monotonic() < deadline:
            try:
                incoming = self.device.read(0x86, 4096, timeout=500)
            except self.core.USBTimeoutError:
                continue
            for response in self.frames.feed(incoming):
                if response.get("id") == self.sequence:
                    if "error" in response or "result" not in response:
                        raise RuntimeError(f"Device rejected request: {response}")
                    return response
                if response.get("method") == "event.rpt_err":
                    raise RuntimeError(f"Device error event: {response}")
        raise TimeoutError("No correlated reply; will not repeat command")

    def properties(self, names):
        return self.rpc({"method": "get-prop", "params": list(names)})

    def send_chunk(self, data, device_job_id):
        extlen = len(data)
        packet = (
            f"cmd data EXTLEN={extlen}\n".encode()
            + struct.pack("<I", device_job_id)
            + data
        )
        for offset in range(0, len(packet), PROFILE["usb_write_size"]):
            part = packet[offset : offset + PROFILE["usb_write_size"]]
            if self.device.write(0x04, part, timeout=10000) != len(part):
                raise RuntimeError("Short USB sub-write; frame will not be resent")
        expected = f"cmd data EXTLEN={extlen} OK".encode()
        received = b""
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                received += bytes(self.device.read(0x84, 4096, timeout=500))
            except self.core.USBTimeoutError:
                continue
            clean = received.strip(b"\r\n\0 ")
            if clean == expected:
                return
            if len(received) > 4096 or not expected.startswith(clean):
                raise RuntimeError(
                    f"Unexpected data ACK: {received[:200]!r}; chunk not repeated"
                )
        raise TimeoutError("Missing data ACK; chunk not repeated")


def transfer_recovery_evidence(error):
    """Identify the DHP700 transfer failure cleared by a full power cycle."""
    message = str(error).lower()
    if "unexpected data ack" not in message and "-8013" not in message:
        return {}
    return {
        "recovery": {
            "action": "power_cycle_printer",
            "automatic_retry": False,
        }
    }


def checkpoint(service, job, state, evidence, intent=False, device_id=None):
    sheet = job["sheets"][0]
    with service.engine.begin() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        if intent:
            existing = c.execute(
                select(db.attempts.c.id).where(db.attempts.c.sheet_id == sheet["id"])
            ).first()
            if existing:
                raise RuntimeError(
                    "A creation intent already exists; physical job will never be replayed"
                )
            c.execute(
                db.attempts.insert().values(
                    id=uid(), sheet_id=sheet["id"], created_at=now()
                )
            )
        if device_id is not None:
            c.execute(
                update(db.attempts)
                .where(db.attempts.c.sheet_id == sheet["id"])
                .values(device_job_id=str(device_id))
            )
        c.execute(
            update(db.sheets)
            .where(db.sheets.c.id == sheet["id"])
            .values(state=state, evidence={"origin": "device", **evidence})
        )
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(state=state))
        service.event(c, job["id"], state, sheet["id"], "device", evidence)


def execute_photo(service, job, session, progress=print, heartbeat=lambda: None):
    job_profile = job["request"].get("profile", {})
    if (
        job["execution_mode"] != "hardware"
        or not is_photo_test_profile(job_profile)
        or len(job["sheets"]) != 1
    ):
        raise ValueError("Not a bounded photo commissioning job")
    with service.engine.connect() as c:
        if c.execute(
            select(db.attempts.c.id).where(
                db.attempts.c.sheet_id == job["sheets"][0]["id"]
            )
        ).first():
            raise RuntimeError(
                "An attempt already exists; use history to reconcile, not resubmit"
            )
        if c.execute(
            select(db.jobs.c.id).where(
                db.jobs.c.execution_mode == "hardware",
                db.jobs.c.state.in_(
                    ["creating", "transferring", "processing", "uncertain"]
                ),
            )
        ).first():
            raise RuntimeError("Unresolved hardware activity holds dispatch")
    if job["state"] not in ("held", "queued"):
        raise RuntimeError("Only a never-started held or queued job may be executed")
    render = service.get(db.renders, job["render_id"])["data"]
    files = render["pages"][0]["artifacts"]
    for artifact in files.values():
        service.store.read(artifact["hash"])
    image_name = (
        "image.png" if job_profile["image_format"] == "png" else "image.jpg"
    )
    payload = service.store.read(files[image_name]["hash"])
    with Image.open(BytesIO(payload)) as image:
        expected_format = (
            "PNG" if job_profile["image_format"] == "png" else "JPEG"
        )
        if (
            image.format != expected_format
            or image.mode != "RGB"
            or list(image.size) != job_profile["raster"]
        ):
            raise ValueError(
                f"Approved {job_profile['image_format'].upper()} integrity mismatch"
            )
    if len(payload) > job_profile["max_image_bytes"]:
        raise ValueError("Payload exceeds commissioning limit")
    identity = session.properties(QUERIES[0][1])
    media = session.properties(QUERIES[1][1])
    status = session.properties(QUERIES[2][1])
    status_values = status["result"]
    if (
        not isinstance(status_values, list)
        or len(status_values) != 3
        or str(status_values[0]) not in ("20", "30")
        or status_values[2] != "::0"
    ):
        raise RuntimeError(f"Printer not idle/asleep without alerts: {status}")
    if (
        not isinstance(identity.get("result"), list)
        or len(identity["result"]) != 4
        or identity["result"][2] != "DHP700"
    ):
        raise RuntimeError("Unexpected printer identity")
    intent = False
    device_id = None
    acknowledged_bytes = 0
    try:
        checkpoint(
            service,
            job,
            "creating",
            {
                "identity": identity,
                "media": media,
                "status": status,
                "operator_confirmed_media": "4x6 photo",
                "profile": job_profile,
            },
            intent=True,
        )
        intent = True
        progress(
            f"Creation intent committed for {job['id']}; sending one "
            f"{job_profile['image_format'].upper()} print-only job",
            flush=True,
        )
        if "declaration" in job["request"]:
            print_request = job["request"]["declaration"]
        else:
            manifest = json.loads(service.store.read(files["manifest.json"]["hash"]))
            print_request = manifest["declaration"]
        if print_request != declaration(
            payload,
            job["id"],
            print_request["params"].get("job-send-time"),
            job_profile,
        ):
            raise ValueError("Frozen print declaration integrity mismatch")
        response = session.rpc(print_request)
        result = response["result"]
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if (
            isinstance(result, dict)
            and "error-code" in result
            and "job_id" not in result
        ):
            checkpoint(
                service,
                job,
                "failed",
                {
                    "creation_response": response,
                    "reason": "explicit_creation_rejection",
                },
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
        last_heartbeat = last_progress = time.monotonic()
        started = time.monotonic()
        chunk_size = job_profile["chunk_payload"]
        for offset in range(0, len(payload), chunk_size):
            if time.monotonic() - started > 360:
                raise TimeoutError("Bounded upload deadline reached")
            chunk = payload[offset : offset + chunk_size]
            session.send_chunk(chunk, device_id)
            acknowledged_bytes += len(chunk)
            time.sleep(job_profile["chunk_delay_s"])
            if time.monotonic() - last_heartbeat >= 3:
                heartbeat()
                status_heartbeat = session.properties(QUERIES[2][1])
                if (
                    isinstance(status_heartbeat["result"], list)
                    and str(status_heartbeat["result"][0]) == "60"
                ):
                    raise RuntimeError(
                        f"Device error during upload: {status_heartbeat}"
                    )
                last_heartbeat = time.monotonic()
            if time.monotonic() - last_progress >= 10:
                checkpoint(
                    service,
                    job,
                    "transferring",
                    {
                        "acknowledged_image_bytes": acknowledged_bytes,
                        "total_bytes": len(payload),
                    },
                )
                progress(
                    f"{job_profile['image_format'].upper()} transfer "
                    f"{acknowledged_bytes}/{len(payload)} bytes acknowledged",
                    flush=True,
                )
                last_progress = time.monotonic()
        checkpoint(
            service, job, "processing", {"acknowledged_image_bytes": len(payload)}
        )
        progress(
            f"{job_profile['image_format'].upper()} transferred; "
            f"observing device job {device_id}",
            flush=True,
        )
        deadline = time.monotonic() + 300
        previous = None
        while time.monotonic() < deadline:
            heartbeat()
            info = session.rpc(
                {"method": "get-job-info", "params": {"job-id": device_id}}
            )
            value = info["result"]
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            if not isinstance(value, dict) or str(value.get("job-id")) != str(
                device_id
            ):
                raise RuntimeError(f"Unexpected job observation: {info}")
            state = tuple(
                str(value.get(k))
                for k in ("job-state", "job-sub-state", "job-state-reason")
            )
            if state != previous:
                checkpoint(service, job, "processing", {"job_info": info})
                progress(f"Device state {state}", flush=True)
                previous = state
            if state == ("9", "9000", "90001"):
                checkpoint(
                    service,
                    job,
                    "completed",
                    {
                        "job_info": info,
                        "physical_output_verified": False,
                        "profile_still_unvalidated": not job_profile["hardware_validated"],
                    },
                )
                return service.job(job["id"])
            status = session.properties(QUERIES[2][1])
            if isinstance(status["result"], list) and str(status["result"][0]) == "60":
                raise RuntimeError(f"Device error while processing: {status}")
            time.sleep(2)
        raise TimeoutError("Completion not observed within commissioning deadline")
    except BaseException as exc:
        if intent:
            evidence = {
                "error": str(exc),
                "automatic_retry": False,
                "acknowledged_image_bytes": acknowledged_bytes,
                "total_bytes": len(payload),
            }
            evidence.update(transfer_recovery_evidence(exc))
            if device_id is not None:
                evidence["device_job_id"] = device_id
            checkpoint(
                service,
                job,
                "uncertain",
                evidence,
            )
        raise


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="One-sheet JPEG print-only commissioning; dry-run by default"
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--confirm-4x6-photo-paper", action="store_true", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    service = Service(db.data_dir())
    with worker_lock(db.data_dir()):
        job = prepare_photo(service, args.image, args.idempotency_key)
        render = service.get(db.renders, job["render_id"])["data"]
        print(
            json.dumps(
                {
                    "job_id": job["id"],
                    "state": job["state"],
                    "render_id": job["render_id"],
                    "image": render["pages"][0]["artifacts"]["image.jpg"],
                    "profile": PROFILE,
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
            result = execute_photo(service, job, session)
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
