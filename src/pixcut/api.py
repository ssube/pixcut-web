import asyncio
import hmac
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, UploadFile, Request, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select, update, func
from . import db, imaging
from .models import (
    StickerInput,
    StickerEdit,
    ExtractionInput,
    ProjectCutPolicyInput,
    ProjectInput,
    ProjectEdit,
    RestoreSourceLayoutInput,
    LayoutInput,
    RenderInput,
    JobInput,
    ReprintInput,
    ResolutionInput,
    CutGeometry,
    PhotoProjectInput,
    PhotoProjectEdit,
    PhotoSettings,
    PhotoRenderInput,
    PrinterInfo,
)
from .service import Service, Problem, uid


def trusted_hosts():
    hosts = ["127.0.0.1", "localhost", "[::1]", "testserver"]
    hosts.extend(
        host.strip()
        for host in os.environ.get("PIXCUT_TRUSTED_HOSTS", "").split(",")
        if host.strip()
    )
    return list(dict.fromkeys(hosts))


def create_app(root=None):
    service = Service(root or db.data_dir())
    execution_mode = os.environ.get("PIXCUT_EXECUTION_MODE", "simulator")
    if execution_mode not in ("simulator", "hardware"):
        raise RuntimeError("PIXCUT_EXECUTION_MODE must be simulator or hardware")
    app = FastAPI(title="PixCut Studio", version="0.1.0")
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts(),
    )
    app.state.service = service
    token = os.environ.get("PIXCUT_API_TOKEN")

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/v1/assets":
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            if length < 0:
                return JSONResponse(
                    {
                        "code": "UPLOAD_LENGTH_REQUIRED",
                        "message": "Send a bounded multipart upload with Content-Length",
                    },
                    status_code=411,
                )
            if length > 26 * 1024 * 1024:
                return JSONResponse(
                    {
                        "code": "UPLOAD_TOO_LARGE",
                        "message": "Multipart request exceeds 26 MiB",
                    },
                    status_code=413,
                )
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            parsed = urlparse(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme not in (
                "http",
                "https",
            ):
                return JSONResponse(
                    {
                        "code": "ORIGIN_REJECTED",
                        "message": "Use the same origin as the application",
                    },
                    status_code=403,
                )
        if token and request.url.path.startswith("/api/"):
            authorization = request.headers.get("authorization", "")
            if not hmac.compare_digest(authorization, f"Bearer {token}"):
                return JSONResponse(
                    {
                        "code": "UNAUTHORIZED",
                        "message": "A valid operator token is required",
                    },
                    status_code=401,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(Problem)
    async def problem_handler(_, error):
        return JSONResponse(
            {
                "code": error.code,
                "message": error.message,
                "affected_objects": [],
                "remediation": "Correct the request or reload the current resource.",
            },
            status_code=error.status,
        )

    @app.exception_handler(ValueError)
    async def value_handler(_, error):
        code, _, message = str(error).partition(":")
        return JSONResponse(
            {
                "code": code if message else "VALIDATION_FAILED",
                "message": message.strip() or str(error),
            },
            status_code=422,
        )

    prefix = "/api/v1"

    @app.get(prefix + "/health")
    def health():
        return {
            "status": "ok",
            "execution_mode": execution_mode,
            "sqlite_version": db.sqlite3.sqlite_version,
        }

    @app.post(prefix + "/assets", status_code=201)
    def upload(file: UploadFile):
        raw = file.file.read(25 * 1024 * 1024 + 1)
        if len(raw) > 25 * 1024 * 1024:
            raise Problem("UPLOAD_TOO_LARGE", "Uploads are limited to 25 MiB", 413)
        try:
            return service.upload(raw)
        except (
            imaging.Image.DecompressionBombError,
            imaging.Image.DecompressionBombWarning,
            OSError,
        ) as exc:
            raise Problem("INVALID_IMAGE", "Image could not be safely decoded") from exc

    @app.get(prefix + "/assets/{id}")
    def asset(id: str):
        return service.get(db.assets, id)["data"]

    @app.get(prefix + "/assets/{id}/{kind}")
    def asset_file(id: str, kind: str):
        if kind not in ("original", "normalized"):
            raise Problem("NOT_FOUND", "Unknown asset representation", 404)
        a = service.get(db.assets, id)["data"][kind]
        return Response(service.store.read(a["hash"]), media_type=a["mime"])

    @app.post(prefix + "/assets/{id}/extractions", status_code=201)
    def extract_asset(id: str, request: ExtractionInput):
        return service.extracted_project(id, request.name)

    @app.post(prefix + "/stickers", status_code=201)
    def sticker(request: StickerInput):
        return service.sticker(request)

    @app.get(prefix + "/stickers/{id}")
    def get_sticker(id: str, revision: int | None = None):
        return service.get(db.stickers, id, revision)["data"]

    @app.patch(prefix + "/stickers/{id}")
    def edit_sticker(id: str, request: StickerEdit):
        return service.sticker(request, id)

    @app.post(prefix + "/projects", status_code=201)
    def project(request: ProjectInput):
        return service.project(request)

    @app.get(prefix + "/projects")
    def projects(kind: Literal["photo", "sticker"] | None = None):
        with service.engine.connect() as c:
            latest = (
                select(
                    db.projects.c.id, func.max(db.projects.c.revision).label("revision")
                )
                .group_by(db.projects.c.id)
                .subquery()
            )
            items = [
                r[0]
                for r in c.execute(
                    select(db.projects.c.data).join(
                        latest,
                        (db.projects.c.id == latest.c.id)
                        & (db.projects.c.revision == latest.c.revision),
                    )
                )
            ]

        def editable_kind(item):
            if item.get("commissioning") or item.get("kind") == "commissioning":
                return None
            return "photo" if item.get("kind") == "photo" else "sticker"

        return [
            item
            for item in items
            if editable_kind(item)
            and (kind is None or editable_kind(item) == kind)
        ]

    @app.get(prefix + "/projects/{id}")
    def get_project(id: str, revision: int | None = None):
        return service.get(db.projects, id, revision)["data"]

    @app.patch(prefix + "/projects/{id}")
    def edit_project(id: str, request: ProjectEdit):
        return service.project(request, id)

    @app.post(prefix + "/projects/{id}/restore-source-layout")
    def restore_source_layout(id: str, request: RestoreSourceLayoutInput):
        return service.restore_source_layout(id, request.expected_revision)

    @app.post(prefix + "/photo-projects", status_code=201)
    def photo_project(request: PhotoProjectInput):
        return service.photo_project(request)

    @app.get(prefix + "/photo-projects/{id}")
    def get_photo_project(id: str, revision: int | None = None):
        project = service.get(db.projects, id, revision)["data"]
        if project.get("kind") != "photo":
            raise Problem("PROJECT_KIND_MISMATCH", "This is not a photo project", 409)
        return project

    @app.patch(prefix + "/photo-projects/{id}")
    def edit_photo_project(id: str, request: PhotoProjectEdit):
        return service.photo_project(request, id)

    @app.post(prefix + "/photo-projects/{id}/draft-preview")
    def photo_draft_preview(id: str, request: PhotoSettings):
        return Response(service.photo_preview(id, request), media_type="image/png")

    @app.post(prefix + "/photo-projects/{id}/preview", include_in_schema=False)
    def photo_preview_alias(id: str, request: PhotoSettings):
        return Response(service.photo_preview(id, request), media_type="image/png")

    @app.post(prefix + "/photo-projects/{id}/renders", status_code=201)
    def photo_render(id: str, request: PhotoRenderInput):
        return service.render_photo(id, request)

    @app.post(prefix + "/projects/{id}/cut-policy")
    def project_cut_policy(id: str, request: ProjectCutPolicyInput):
        return service.apply_cut_policy(id, request)

    @app.delete(prefix + "/projects/{id}", status_code=204)
    def delete_project(id: str):
        service.delete_project(id)
        return Response(status_code=204)

    @app.get(prefix + "/projects/{id}/geometry", response_model=list[CutGeometry])
    def geometry(id: str, revision: int | None = None):
        p = service.get(db.projects, id, revision)["data"]
        return imaging.resolve(p, service.definitions(p))

    @app.post(prefix + "/projects/{id}/layouts")
    def layout(id: str, request: LayoutInput):
        return service.propose_layout(id, request)

    @app.post(prefix + "/projects/{id}/renders", status_code=202)
    def render(id: str, request: RenderInput):
        # Initial bounded implementation completes in the request thread; operation contract is durable.
        result = service.render(id, request)
        op = {"id": uid(), "state": "completed", "render_id": result["id"]}
        with service.engine.begin() as c:
            c.execute(db.operations.insert().values(id=op["id"], data=op))
        return op

    @app.get(prefix + "/operations/{id}")
    def operation(id: str):
        return service.get(db.operations, id)["data"]

    @app.get(prefix + "/renders/{id}")
    def get_render(id: str):
        return service.get(db.renders, id)["data"]

    @app.get(prefix + "/renders/{id}/artifacts/{name:path}")
    def render_artifact(id: str, name: str):
        with service.engine.connect() as c:
            a = (
                c.execute(
                    select(db.artifacts)
                    .join(db.render_files)
                    .where(
                        db.render_files.c.render_id == id,
                        db.render_files.c.name == name,
                    )
                )
                .mappings()
                .first()
            )
        if not a:
            raise Problem("NOT_FOUND", "Artifact not found in this render", 404)
        return Response(
            service.store.read(a["hash"]),
            media_type=a["mime"],
            headers={
                "ETag": f'"{a["hash"]}"',
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )

    def printer_info():
        from .discovery import scan_usb

        found = bool(scan_usb().get("matched_count", 0))
        worker = service.worker_health(execution_mode)
        attention_job_id = (
            service.hardware_attention_job() if execution_mode == "hardware" else None
        )
        if attention_job_id or worker["state"] == "blocked":
            status = "attention"
        elif not worker["fresh"]:
            status = "worker_offline"
        elif execution_mode == "hardware" and not found:
            status = "device_missing"
        else:
            status = "ready"
        ready = status == "ready"
        if execution_mode == "hardware":
            from .printcut import PROFILE
            from .commissioning import PROFILE as PHOTO_PROFILE

            return {
                "id": "pixcut-s1-usb",
                "name": "PixCut S1 USB",
                "found": found,
                "ready": ready,
                "status": status,
                "worker": worker,
                "attention_job_id": attention_job_id,
                "execution_mode": "hardware",
                "profile": PROFILE,
                "profiles": [PHOTO_PROFILE, PROFILE],
                "hardware_validated": PROFILE["hardware_validated"],
            }
        return {
            "id": "simulator",
            "name": "PixCut S1 USB",
            "found": found,
            "ready": ready,
            "status": status,
            "worker": worker,
            "attention_job_id": None,
            "execution_mode": "simulator",
            "profile": imaging.PROFILE,
            "profiles": [imaging.PROFILE],
            "hardware_validated": False,
        }

    @app.get(prefix + "/printers", response_model=list[PrinterInfo])
    def printers():
        return [printer_info()]

    @app.get(prefix + "/printers/{id}", response_model=PrinterInfo)
    def printer(id: str):
        current = printer_info()
        if id != current["id"]:
            raise Problem(
                "PRINTER_MODE_MISMATCH",
                f"This server is running with printer {current['id']}",
                409,
            )
        return current

    @app.post(prefix + "/jobs", status_code=201)
    def submit(request: JobInput):
        if execution_mode == "hardware":
            current_printer = printer_info()
            if not current_printer["ready"]:
                messages = {
                    "attention": "Check History and clear the printer warning before printing",
                    "device_missing": "Turn on or connect the printer before printing",
                    "worker_offline": "The printer service is not running",
                }
                raise Problem(
                    "PRINTER_NOT_READY",
                    messages.get(current_printer["status"], "The printer is not ready"),
                    409,
                )
            from .printcut import PROFILE, prepare
            from .commissioning import PROFILE as PHOTO_PROFILE, prepare_render

            if request.mode == "print_only":
                if (
                    request.printer_id != "pixcut-s1-usb"
                    or request.accepted_profile_id != PHOTO_PROFILE["id"]
                    or request.pages != [0]
                    or request.copies != 1
                    or request.confirmed_media != "4x6 photo paper"
                ):
                    raise Problem(
                        "PRINT_SETTINGS_MISMATCH",
                        "Choose one reviewed page and confirm that 4×6 photo paper is loaded",
                    )
                return prepare_render(
                    service,
                    request.render_id,
                    request.idempotency_key,
                    request.name,
                    dispatch=True,
                )

            if (
                request.printer_id != "pixcut-s1-usb"
                or request.accepted_profile_id != PROFILE["id"]
                or request.pages != [0]
                or request.copies != 1
            ):
                raise Problem(
                    "PRINT_SETTINGS_MISMATCH",
                    "Choose one reviewed page and the 4×7 sticker-paper profile",
                )
            return prepare(
                service, request.render_id, request.idempotency_key, dispatch=True
            )
        if (
            request.mode != "print_cut"
            or request.confirmed_media is not None
            or request.printer_id != "simulator"
            or request.accepted_profile_id != imaging.PROFILE["id"]
        ):
            raise Problem(
                "PRINTER_MODE_MISMATCH",
                "This server is running in simulator mode",
                409,
            )
        return service.submit(request)

    @app.get(prefix + "/jobs")
    def history(
        before: str | None = None,
        limit: int = Query(25, ge=1, le=100),
        state: str | None = None,
    ):
        query = (
            select(db.jobs.c.id)
            .order_by(db.jobs.c.created_at.desc(), db.jobs.c.id.desc())
            .limit(limit + 1)
        )
        if before:
            prior = service.get(db.jobs, before)
            query = query.where(
                (db.jobs.c.created_at < prior["created_at"])
                | (
                    (db.jobs.c.created_at == prior["created_at"])
                    & (db.jobs.c.id < before)
                )
            )
        if state:
            query = query.where(db.jobs.c.state == state)
        with service.engine.connect() as c:
            ids = list(c.execute(query).scalars())
            queue_length = c.execute(
                select(func.count())
                .select_from(db.sheets)
                .where(
                    db.sheets.c.state.in_(
                        ["held", "queued", "creating", "transferring", "processing"]
                    )
                )
            ).scalar_one()
        return {
            "items": [service.job(id) for id in ids[:limit]],
            "next_cursor": ids[limit - 1] if len(ids) > limit else None,
            "queue_length": queue_length,
        }

    @app.get(prefix + "/jobs/{id}")
    def job(id: str):
        return service.job(id)

    @app.get(prefix + "/jobs/{id}/sheets")
    def sheets(id: str):
        return service.job(id)["sheets"]

    @app.get(prefix + "/jobs/{id}/events")
    def job_events(id: str, after: int = 0, limit: int = Query(100, ge=1, le=1000)):
        service.get(db.jobs, id)
        with service.engine.connect() as c:
            return [
                dict(r)
                for r in c.execute(
                    select(db.events)
                    .where(db.events.c.job_id == id, db.events.c.id > after)
                    .order_by(db.events.c.id)
                    .limit(limit)
                ).mappings()
            ]

    @app.post(prefix + "/jobs/{id}/cancel")
    def cancel(id: str):
        return service.cancel(id)

    @app.post(prefix + "/jobs/{id}/resume")
    def resume(id: str):
        if execution_mode != "hardware":
            raise Problem(
                "PRINTER_MODE_MISMATCH",
                "Hardware jobs can only be resumed in hardware mode",
                409,
            )
        return service.resume_hardware_job(id)

    @app.post(prefix + "/jobs/{id}/reprints", status_code=201)
    def reprint(id: str, request: ReprintInput):
        if execution_mode == "hardware":
            raise Problem(
                "HARDWARE_REPRINT_NOT_COMMISSIONED",
                "Prepare and review a fresh one-page render for each real print",
                409,
            )
        old = service.job(id)
        if len(set(request.sheet_ids)) != len(request.sheet_ids):
            raise Problem("INVALID_SHEET_SELECTION", "Select each saved sheet row once")
        selection = [s["page"] for s in old["sheets"] if s["id"] in request.sheet_ids]
        if len(selection) != len(request.sheet_ids):
            raise Problem(
                "INVALID_SHEET_SELECTION", "Sheet rows must belong to this job"
            )
        return service.submit(request, parent=id, selected=selection)

    @app.post(prefix + "/jobs/{id}/resolutions")
    def resolution(id: str, request: ResolutionInput):
        with service.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            last = c.execute(
                select(func.max(db.events.c.id)).where(db.events.c.job_id == id)
            ).scalar()
            if last != request.expected_event_id:
                raise Problem(
                    "STALE_REVISION",
                    "Reload the job before recording a resolution",
                    409,
                )
            s = service.get(db.sheets, request.sheet_id, c=c)
            if s["job_id"] != id or s["state"] != "uncertain":
                raise Problem(
                    "INVALID_RESOLUTION", "Select an uncertain sheet in this job", 409
                )
            recovery = (s.get("evidence") or {}).get("recovery", {})
            if (
                recovery.get("action") == "power_cycle_printer"
                and not request.printer_restarted
            ):
                raise Problem(
                    "RECOVERY_CONFIRMATION_REQUIRED",
                    "Confirm that the printer was restarted before clearing this warning",
                    409,
                )
            evidence = {
                "origin": "operator",
                "note": request.note,
                "resolution": request.outcome,
                "printer_restarted": request.printer_restarted,
            }
            c.execute(
                update(db.sheets)
                .where(db.sheets.c.id == request.sheet_id)
                .values(state=request.outcome, evidence=evidence)
            )
            service.event(
                c, id, request.outcome, request.sheet_id, "operator", evidence
            )
            service.aggregate(c, id)
        return service.job(id)

    @app.get(prefix + "/events")
    async def event_stream(request: Request, after: int = 0):
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise Problem("INVALID_CURSOR", "Event cursor must be an integer")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                with service.engine.connect() as c:
                    rows = [
                        dict(r)
                        for r in c.execute(
                            select(db.events)
                            .where(db.events.c.id > cursor)
                            .order_by(db.events.c.id)
                            .limit(100)
                        ).mappings()
                    ]
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: job\ndata: {json.dumps(row)}\n\n"
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app
