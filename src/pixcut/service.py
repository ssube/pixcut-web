from datetime import datetime, timezone
from copy import deepcopy
from io import BytesIO
from uuid import uuid4
from sqlalchemy import select, func, update
from sqlalchemy.dialects.sqlite import insert
from . import db, imaging, photo
from .artifacts import ArtifactStore, canonical, digest
from .extraction import extract
from .models import ProjectEdit, ProjectInput, StickerInput


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat()


class Problem(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.message, self.status = code, message, status


class Service:
    def __init__(self, root):
        self.engine = db.engine_for(root)
        self.store = ArtifactStore(root)
        with self.engine.connect() as c:
            from sqlalchemy import text

            if (
                c.execute(text("SELECT version_num FROM alembic_version")).scalar()
                != "0002"
            ):
                raise RuntimeError("Run pixcut migrate before starting the service")

    def worker_heartbeat(
        self, execution_mode, state, current_job_id=None, message=None
    ):
        timestamp = now()
        with self.engine.begin() as c:
            c.execute(
                insert(db.worker_status)
                .values(
                    id="printer",
                    execution_mode=execution_mode,
                    state=state,
                    current_job_id=current_job_id,
                    last_seen_at=timestamp,
                    message=message,
                )
                .on_conflict_do_update(
                    index_elements=[db.worker_status.c.id],
                    set_={
                        "execution_mode": execution_mode,
                        "state": state,
                        "current_job_id": current_job_id,
                        "last_seen_at": timestamp,
                        "message": message,
                    },
                )
            )

    def worker_health(self, execution_mode, stale_seconds=10):
        with self.engine.connect() as c:
            row = c.execute(
                select(db.worker_status).where(db.worker_status.c.id == "printer")
            ).mappings().first()
        if row is None or row["execution_mode"] != execution_mode:
            return {
                "state": "offline",
                "last_seen_at": None,
                "current_job_id": None,
                "message": None,
                "fresh": False,
            }
        age = (
            datetime.now(timezone.utc) - datetime.fromisoformat(row["last_seen_at"])
        ).total_seconds()
        fresh = age <= stale_seconds and row["state"] != "stopped"
        return {
            "state": row["state"] if fresh else "offline",
            "last_seen_at": row["last_seen_at"],
            "current_job_id": row["current_job_id"],
            "message": row["message"],
            "fresh": fresh,
        }

    def hardware_attention_job(self):
        with self.engine.connect() as c:
            rows = c.execute(
                select(db.jobs.c.id, db.sheets.c.evidence)
                .join(db.sheets)
                .where(
                    db.jobs.c.execution_mode == "hardware",
                    db.sheets.c.state == "uncertain",
                )
                .order_by(db.jobs.c.created_at, db.jobs.c.id)
            ).mappings()
            for row in rows:
                recovery = (row["evidence"] or {}).get("recovery", {})
                if recovery.get("action") == "power_cycle_printer":
                    return row["id"]
        return None

    def register(self, c, artifact):
        c.execute(insert(db.artifacts).values(**artifact).on_conflict_do_nothing())

    def get(self, table, id, revision=None, c=None):
        query = select(table).where(table.c.id == id)
        if "revision" in table.c:
            if revision is not None:
                query = query.where(table.c.revision == revision)
            query = query.order_by(table.c.revision.desc())
        if c is None:
            with self.engine.connect() as connection:
                row = connection.execute(query).mappings().first()
        else:
            row = c.execute(query).mappings().first()
        if row is None:
            raise Problem("NOT_FOUND", f"{table.name} {id} was not found", 404)
        return dict(row)

    def upload(self, raw):
        image, normalized, mime, policy = imaging.normalize(raw)
        original = self.store.put(raw, mime)
        normalized = self.store.put(normalized, "image/png")
        asset = {
            "id": uid(),
            "original": original,
            "normalized": normalized,
            "width_px": image.width,
            "height_px": image.height,
            "pixel_hash": digest(image.tobytes()),
            "normalization": {
                "orientation": "exif-transposed",
                "color": policy,
                "mode": "RGBA",
            },
        }
        with self.engine.begin() as c:
            self.register(c, original)
            self.register(c, normalized)
            c.execute(db.assets.insert().values(id=asset["id"], data=asset))
        return asset

    def photo_project(self, request, id=None):
        self.get(db.assets, request.asset_id)
        id = id or uid()
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            revision = 1
            if hasattr(request, "expected_revision"):
                old = self.get(db.projects, id, c=c)
                if old["data"].get("kind") != "photo":
                    raise Problem(
                        "PROJECT_KIND_MISMATCH", "This is not a photo project", 409
                    )
                if old["revision"] != request.expected_revision:
                    raise Problem(
                        "STALE_REVISION", "Reload the photo before saving", 409
                    )
                if old["data"]["asset_id"] != request.asset_id:
                    raise Problem(
                        "PHOTO_SOURCE_IMMUTABLE",
                        "Create a new photo project to use a different source asset",
                        409,
                    )
                revision = old["revision"] + 1
            data = request.model_dump(exclude={"expected_revision"}) | {
                "id": id,
                "revision": revision,
                "kind": "photo",
                "adjustment_pipeline_version": photo.PIPELINE_VERSION,
            }
            c.execute(db.projects.insert().values(id=id, revision=revision, data=data))
        return data

    def _photo_source(self, project):
        asset = self.get(db.assets, project["asset_id"])["data"]
        with imaging.Image.open(
            BytesIO(self.store.read(asset["normalized"]["hash"]))
        ) as decoded:
            decoded.load()
            if decoded.mode == "RGBA":
                background = imaging.Image.new("RGBA", decoded.size, "white")
                source = imaging.Image.alpha_composite(background, decoded).convert(
                    "RGB"
                )
            else:
                source = decoded.convert("RGB")
            return source, asset

    def photo_preview(self, id, settings):
        project = self.get(db.projects, id)["data"]
        if project.get("kind") != "photo":
            raise Problem("PROJECT_KIND_MISMATCH", "This is not a photo project", 409)
        draft = project | settings.model_dump()
        source, _ = self._photo_source(project)
        return photo.preview_png(source, draft)

    def render_photo(self, id, request):
        project = self.get(db.projects, id, request.revision)["data"]
        if project.get("kind") != "photo":
            raise Problem("PROJECT_KIND_MISMATCH", "This is not a photo project", 409)
        source, asset = self._photo_source(project)
        try:
            payload = photo.exact_jpeg(source, project)
        except ValueError as exc:
            code, _, message = str(exc).partition(":")
            raise Problem(code, message.strip() or str(exc), 422) from exc
        render_id = uid()
        orientation = photo.resolved_orientation(project)
        width, height = photo.output_size(project)
        page_mm = [101.6, 152.4]
        geometry = {
            "schema_version": "1.0",
            "geometry_engine": "photo-no-cuts-v1",
            "provenance": {
                "kind": "render",
                "project_id": id,
                "project_revision": request.revision,
                "render_id": render_id,
            },
            "page_id": "photo",
            "page_mm": page_mm,
            "units": "mm",
            "coordinate_space": "sheet_design",
            "origin": "top_left",
            "x_direction": "right",
            "y_direction": "down",
            "geometry_stage": "finished_cut_pre_device",
            "shapes": [],
        }
        with imaging.Image.open(BytesIO(payload)) as decoded:
            decoded.load()
            exact = decoded.copy()
        thumb = exact.copy()
        thumb.thumbnail((240, 360), imaging.Image.Resampling.LANCZOS)
        outputs = {
            "image.jpg": (payload, "image/jpeg"),
            "thumbnail.png": (imaging.png(thumb), "image/png"),
            "cut-geometry.json": (canonical(geometry), "application/json"),
            "overlay.svg": (
                (
                    f'<svg xmlns="http://www.w3.org/2000/svg" '
                    f'viewBox="0 0 {page_mm[0]} {page_mm[1]}"/>'
                ).encode(),
                "image/svg+xml",
            ),
        }
        files = {
            name: self.store.put(data, mime) for name, (data, mime) in outputs.items()
        }
        manifest = {
            "schema_version": "1.0",
            "profile": photo.profile(),
            "render_id": render_id,
            "project_id": id,
            "project_revision": request.revision,
            "source_asset_id": asset["id"],
            "source_asset": asset,
            "source_project": project,
            "crop": project["crop"],
            "layout": project.get("layout", "single"),
            "sheet_layout": photo.layout_spec(project),
            "orientation": orientation,
            "framing": project["framing"],
            "preset": project["preset"],
            "film_look": project["film_look"],
            "print_quality": project.get("print_quality", 95),
            "encoder": {
                "format": "JPEG",
                "quality": project.get("print_quality", 95),
                "subsampling": 0,
                "optimize": True,
                "dpi": [300, 300],
            },
            "adjustments": project["adjustments"],
            "adjustment_pipeline_version": photo.PIPELINE_VERSION,
            "artifacts": dict(files),
            "raster_hash": digest(exact.tobytes()),
            "raster_mode": "RGB",
            "raster_size": [width, height],
            "renderer": "pixcut-photo-v1",
            "pillow_version": imaging.Image.__version__,
            "validation": {"hardware_eligible": True, "print_only": True},
        }
        files["manifest.json"] = self.store.put(
            canonical(manifest), "application/json"
        )
        page = {
            "index": 0,
            "sticker_count": 0,
            "raster_hash": manifest["raster_hash"],
            "artifacts": files,
        }
        result = {
            "id": render_id,
            "project_id": id,
            "revision": request.revision,
            "created_at": now(),
            "kind": "photo",
            "profile": photo.profile(),
            "pages": [page],
            "hardware_eligible": True,
            "warnings": ["Print only. Confirm 4x6 photo paper before physical output."],
        }
        with self.engine.begin() as c:
            c.execute(
                db.renders.insert().values(
                    id=render_id,
                    project_id=id,
                    revision=request.revision,
                    data=result,
                )
            )
            for name, artifact in files.items():
                self.register(c, artifact)
                c.execute(
                    db.render_files.insert().values(
                        render_id=render_id,
                        name=f"0/{name}",
                        hash=artifact["hash"],
                    )
                )
        return result

    def extracted_project(self, asset_id, name):
        source = self.get(db.assets, asset_id)["data"]
        with imaging.Image.open(
            BytesIO(self.store.read(source["normalized"]["hash"]))
        ) as decoded:
            decoded.load()
            regions, method = extract(decoded)

        stickers = []
        for index, region in enumerate(regions, 1):
            encoded = imaging.png(region.image)
            artifact = self.store.put(encoded, "image/png")
            crop_asset = {
                "id": uid(),
                "original": artifact,
                "normalized": artifact,
                "width_px": region.image.width,
                "height_px": region.image.height,
                "pixel_hash": digest(region.image.tobytes()),
                "normalization": {
                    "orientation": "inherited",
                    "color": "inherited-sRGB",
                    "mode": "RGBA",
                    "extracted_from": asset_id,
                    "method": method,
                    "bbox_px": list(region.bbox),
                },
            }
            with self.engine.begin() as c:
                self.register(c, artifact)
                c.execute(
                    db.assets.insert().values(id=crop_asset["id"], data=crop_asset)
                )
            stickers.append(
                self.sticker(
                    StickerInput(
                        name=f"{name} · region {index}",
                        asset_id=crop_asset["id"],
                        outline=region.outline,
                    )
                )
            )

        scale = min(
            (imaging.PAGE_MM[0] - 4) / source["width_px"],
            (imaging.PAGE_MM[1] - 4) / source["height_px"],
        )
        used_width = source["width_px"] * scale
        used_height = source["height_px"] * scale
        offset_x = (imaging.PAGE_MM[0] - used_width) / 2
        offset_y = (imaging.PAGE_MM[1] - used_height) / 2
        # Extracted transparent crops may have overlapping rectangular bounds even
        # when their visible artwork and cut contours do not overlap. Preserve the
        # source scale here and let canonical cut geometry enforce real collisions.
        shrink = 1.0
        placements = []
        for index, (sticker, region) in enumerate(zip(stickers, regions), 1):
            x0, y0, x1, y1 = region.bbox
            width_px = (x1 - x0) * shrink
            height_px = (y1 - y0) * shrink
            center_x = (x0 + x1) / 2
            center_y = (y0 + y1) / 2
            placements.append(
                {
                    "id": f"extracted-{index}",
                    "sticker_id": sticker["id"],
                    "x_mm": offset_x + (center_x - width_px / 2) * scale,
                    "y_mm": offset_y + (center_y - height_px / 2) * scale,
                    "width_mm": width_px * scale,
                    "height_mm": height_px * scale,
                    "rotation": 0,
                    "pinned": len(stickers) > 1,
                }
            )
        project = self.project(
            ProjectInput(
                name=name,
                bindings={item["id"]: item["revision"] for item in stickers},
                pages=[
                    {
                        "id": "extracted-sheet",
                        "placements": placements,
                        "source_layout": True,
                    }
                ],
            )
        )
        return {
            "source_asset_id": asset_id,
            "method": method,
            "component_count": len(stickers),
            "stickers": stickers,
            "project": project,
        }

    def delete_project(self, id):
        self.get(db.projects, id)
        with self.engine.begin() as c:
            c.execute(db.projects.delete().where(db.projects.c.id == id))

    def restore_source_layout(self, id, expected_revision):
        current = self.get(db.projects, id)["data"]
        if current["revision"] != expected_revision:
            raise Problem("STALE_REVISION", "Reload the project before restoring", 409)

        definitions = self.definitions(current)
        pages = deepcopy(current["pages"])
        restored = False
        for page in pages:
            placement_sources = []
            for placement in page["placements"]:
                definition = definitions[placement["sticker_id"]]
                crop = self.get(db.assets, definition["asset_id"])["data"]
                normalization = crop.get("normalization", {})
                source_id = normalization.get("extracted_from")
                bbox = normalization.get("bbox_px")
                if not source_id or not bbox or len(bbox) != 4:
                    raise Problem(
                        "SOURCE_LAYOUT_UNAVAILABLE",
                        "The original imported positions are not available for this project.",
                        409,
                    )
                placement_sources.append((placement, source_id, bbox))

            if not placement_sources:
                continue
            source_ids = {source_id for _, source_id, _ in placement_sources}
            if len(source_ids) != 1:
                raise Problem(
                    "SOURCE_LAYOUT_UNAVAILABLE",
                    "This sheet combines artwork from different source images.",
                    409,
                )
            source = self.get(db.assets, source_ids.pop())["data"]
            scale = min(
                (imaging.PAGE_MM[0] - 4) / source["width_px"],
                (imaging.PAGE_MM[1] - 4) / source["height_px"],
            )
            offset_x = (imaging.PAGE_MM[0] - source["width_px"] * scale) / 2
            offset_y = (imaging.PAGE_MM[1] - source["height_px"] * scale) / 2
            for placement, _, bbox in placement_sources:
                x0, y0, x1, y1 = bbox
                placement.update(
                    {
                        "x_mm": offset_x + x0 * scale,
                        "y_mm": offset_y + y0 * scale,
                        "width_mm": (x1 - x0) * scale,
                        "height_mm": (y1 - y0) * scale,
                        "rotation": 0,
                    }
                )
            page["source_layout"] = True
            restored = True

        if not restored:
            raise Problem(
                "SOURCE_LAYOUT_UNAVAILABLE",
                "This project does not have an imported layout to restore.",
                409,
            )
        return self.project(
            ProjectEdit(
                name=current["name"],
                bindings=current["bindings"],
                pages=pages,
                expected_revision=current["revision"],
            ),
            id,
        )

    def apply_cut_policy(self, id, request):
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            current = self.get(db.projects, id, c=c)
            if current["revision"] != request.expected_revision:
                raise Problem("STALE_REVISION", "Reload the project before saving", 409)
            project = dict(current["data"])
            bindings = dict(project["bindings"])
            definitions = {}
            for sticker_id, bound_revision in bindings.items():
                bound = self.get(db.stickers, sticker_id, bound_revision, c=c)
                latest = self.get(db.stickers, sticker_id, c=c)
                definition = dict(bound["data"])
                definition.update(
                    {
                        "revision": latest["revision"] + 1,
                        "cut_padding_mm": request.cut_padding_mm,
                        "minimum_cut_width_mm": request.minimum_cut_width_mm,
                    }
                )
                c.execute(
                    db.stickers.insert().values(
                        id=sticker_id,
                        revision=definition["revision"],
                        data=definition,
                    )
                )
                bindings[sticker_id] = definition["revision"]
                definitions[sticker_id] = definition
            project.update(
                {
                    "revision": current["revision"] + 1,
                    "bindings": bindings,
                }
            )
            layout_scale = 1.0
            try:
                imaging.resolve(project, definitions)
            except ValueError as initial_error:
                adjustable = (
                    request.fit_extracted_sheet
                    and len(project["pages"]) == 1
                    and project["pages"][0]["placements"]
                    and all(
                        placement["id"].startswith("extracted-")
                        for placement in project["pages"][0]["placements"]
                    )
                )
                if not adjustable or not str(initial_error).startswith(
                    ("CUT_COLLISION:", "SHEET_GUARD:")
                ):
                    raise
                original_pages = deepcopy(project["pages"])
                for percent in range(99, 49, -1):
                    layout_scale = percent / 100
                    pages = deepcopy(original_pages)
                    for page in pages:
                        for placement in page["placements"]:
                            center_x = placement["x_mm"] + placement["width_mm"] / 2
                            center_y = placement["y_mm"] + placement["height_mm"] / 2
                            placement["width_mm"] *= layout_scale
                            placement["height_mm"] *= layout_scale
                            placement["x_mm"] = (
                                center_x - placement["width_mm"] / 2
                            )
                            placement["y_mm"] = (
                                center_y - placement["height_mm"] / 2
                            )
                    project["pages"] = pages
                    try:
                        imaging.resolve(project, definitions)
                        break
                    except ValueError as fit_error:
                        if not str(fit_error).startswith(
                            ("CUT_COLLISION:", "SHEET_GUARD:")
                        ):
                            raise
                else:
                    raise initial_error
            c.execute(
                db.projects.insert().values(
                    id=id, revision=project["revision"], data=project
                )
            )
        return {"project": project, "layout_scale": layout_scale}

    def sticker(self, request, id=None):
        self.get(db.assets, request.asset_id)
        id = id or uid()
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            revision = 1
            if hasattr(request, "expected_revision"):
                old = self.get(db.stickers, id, c=c)
                if old["revision"] != request.expected_revision:
                    raise Problem(
                        "STALE_REVISION", "Reload the sticker before saving", 409
                    )
                revision = old["revision"] + 1
            data = request.model_dump(exclude={"expected_revision"}) | {
                "id": id,
                "revision": revision,
            }
            c.execute(db.stickers.insert().values(id=id, revision=revision, data=data))
        # Projects bind explicitly to revisions; updating that binding updates all copies.
        return data

    def definitions(self, project, c=None):
        return {
            id: self.get(db.stickers, id, revision, c=c)["data"]
            for id, revision in project["bindings"].items()
        }

    def project(self, request, id=None):
        id = id or uid()
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            revision = 1
            if hasattr(request, "expected_revision"):
                old = self.get(db.projects, id, c=c)
                if old["revision"] != request.expected_revision:
                    raise Problem(
                        "STALE_REVISION", "Reload the project before saving", 409
                    )
                revision = old["revision"] + 1
            data = request.model_dump(exclude={"expected_revision"}) | {
                "id": id,
                "revision": revision,
            }
            if len(data["bindings"]) > 1000:
                raise Problem(
                    "PROJECT_LIMIT", "At most 1000 sticker definitions per project"
                )
            definitions = self.definitions(data, c)
            ids = [p["id"] for page in data["pages"] for p in page["placements"]]
            if len(ids) != len(set(ids)) or len(
                {p["id"] for p in data["pages"]}
            ) != len(data["pages"]):
                raise Problem("DUPLICATE_ID", "Page and placement IDs must be unique")
            if any(
                p["sticker_id"] not in definitions
                for page in data["pages"]
                for p in page["placements"]
            ):
                raise Problem(
                    "UNBOUND_STICKER",
                    "Every placement must use a project sticker binding",
                )
            imaging.resolve(data, definitions)
            c.execute(db.projects.insert().values(id=id, revision=revision, data=data))
        return data

    def propose_layout(self, id, request):
        project = self.get(db.projects, id)["data"]
        if request.revision != project["revision"]:
            raise Problem("STALE_REVISION", "Reload the project before arranging", 409)
        if request.sticker_id not in project["bindings"]:
            raise Problem(
                "UNBOUND_STICKER", "Add the sticker to the shared project bindings"
            )
        if any(p["pinned"] for page in project["pages"] for p in page["placements"]):
            raise Problem(
                "PINNED_LAYOUT",
                "Some stickers are locked in place. Choose Unlock layout before rearranging the sheet.",
            )
        return imaging.layout(request)

    def render(self, id, request):
        project = self.get(db.projects, id, request.revision)["data"]
        if not project["pages"] or any(
            not page["placements"] for page in project["pages"]
        ):
            raise Problem("EMPTY_SHEET", "Add at least one placement to every page")
        definitions = self.definitions(project)
        assets = {
            d["asset_id"]: self.get(db.assets, d["asset_id"])["data"]
            for d in definitions.values()
        }
        render_id = uid()
        geometry = imaging.resolve(project, definitions, render_id)
        files, page_records = {}, []
        for index, page in enumerate(project["pages"]):
            outputs, pixel_hash = imaging.render_page(
                page, geometry[index], definitions, assets, self.store
            )
            local = {
                name: self.store.put(data, mime)
                for name, (data, mime) in outputs.items()
            }
            manifest = {
                "schema_version": "1.0",
                "render_id": render_id,
                "project": project,
                "sticker_definitions": definitions,
                "source_assets": assets,
                "page_index": index,
                "profile": imaging.PROFILE,
                "artifacts": local,
                "raster_hash": pixel_hash,
                "raster_mode": "RGB",
                "renderer": "pixcut-pillow-v1",
                "pillow_version": imaging.Image.__version__,
                "validation": {"simulator_eligible": True, "hardware_eligible": False},
                "intended_sticker_count": len(page["placements"]),
            }
            local["manifest.json"] = self.store.put(
                canonical(manifest), "application/json"
            )
            files.update(
                {f"{index}/{name}": artifact for name, artifact in local.items()}
            )
            page_records.append(
                {
                    "index": index,
                    "sticker_count": len(page["placements"]),
                    "raster_hash": pixel_hash,
                    "artifacts": local,
                }
            )
        result = {
            "id": render_id,
            "project_id": id,
            "revision": request.revision,
            "created_at": now(),
            "profile": imaging.PROFILE,
            "pages": page_records,
            "hardware_eligible": False,
            "warnings": [],
        }
        with self.engine.begin() as c:
            c.execute(
                db.renders.insert().values(
                    id=render_id, project_id=id, revision=request.revision, data=result
                )
            )
            for name, artifact in files.items():
                self.register(c, artifact)
                c.execute(
                    db.render_files.insert().values(
                        render_id=render_id, name=name, hash=artifact["hash"]
                    )
                )
        return result

    def verify_render(self, render_id):
        render = self.get(db.renders, render_id)["data"]
        if render["profile"] not in (imaging.PROFILE, photo.profile()):
            raise Problem(
                "REPRINT_PROFILE_MISMATCH",
                "Render requires a new profile approval",
                409,
            )
        with self.engine.connect() as c:
            files = (
                c.execute(
                    select(db.render_files).where(
                        db.render_files.c.render_id == render_id
                    )
                )
                .mappings()
                .all()
            )
        expected = {
            f"{p['index']}/{name}": a["hash"]
            for p in render["pages"]
            for name, a in p["artifacts"].items()
        }
        if {f["name"]: f["hash"] for f in files} != expected:
            raise Problem(
                "ARTIFACT_INTEGRITY", "Render artifact references do not match", 409
            )
        for f in files:
            self.store.read(f["hash"])
        return render

    def event(self, c, job_id, state, sheet_id=None, origin="simulator", data=None):
        c.execute(
            db.events.insert().values(
                job_id=job_id,
                sheet_id=sheet_id,
                state=state,
                origin=origin,
                data=data or {},
                created_at=now(),
            )
        )

    def submit(self, request, parent=None, selected=None):
        payload = request.model_dump() | {"reprint_of_job_id": parent}
        request_hash = digest(canonical(payload))
        self.store.space_check()
        # Resolve lost HTTP responses before checking current artifact eligibility.
        with self.engine.connect() as c:
            existing = (
                c.execute(
                    select(db.jobs).where(
                        db.jobs.c.idempotency_key == request.idempotency_key
                    )
                )
                .mappings()
                .first()
            )
        if existing:
            if existing["request_hash"] != request_hash:
                raise Problem(
                    "IDEMPOTENCY_CONFLICT",
                    "Use a new key for a different submission",
                    409,
                )
            return self.job(existing["id"])
        render_id = (
            self.get(db.jobs, parent)["render_id"] if parent else request.render_id
        )
        render = self.verify_render(render_id)
        page_indices = selected if selected is not None else sorted(request.pages)
        if (
            not page_indices
            or any(p >= len(render["pages"]) or p < 0 for p in page_indices)
            or len(page_indices) * request.copies > 1000
        ):
            raise Problem(
                "INVALID_SHEET_SELECTION",
                "Choose existing pages and at most 1000 sheet copies",
            )
        id = uid()
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            existing = (
                c.execute(
                    select(db.jobs).where(
                        db.jobs.c.idempotency_key == request.idempotency_key
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Problem(
                        "IDEMPOTENCY_CONFLICT",
                        "Use a new key for a different submission",
                        409,
                    )
                id = existing["id"]
            else:
                c.execute(
                    db.jobs.insert().values(
                        id=id,
                        created_at=now(),
                        name=(request.name if not parent else "Reprint"),
                        render_id=render_id,
                        reprint_of_job_id=parent,
                        idempotency_key=request.idempotency_key,
                        request_hash=request_hash,
                        execution_mode="simulator",
                        state="queued",
                        request=payload,
                    )
                )
                ordinal = 0
                for copy in range(request.copies):
                    for page in page_indices:
                        c.execute(
                            db.sheets.insert().values(
                                id=uid(),
                                job_id=id,
                                ordinal=ordinal,
                                page=page,
                                copy=copy,
                                state="queued",
                            )
                        )
                        ordinal += 1
                self.event(
                    c,
                    id,
                    "queued",
                    data={"sheet_count": ordinal, "profile": imaging.PROFILE},
                )
        return self.job(id)

    def job(self, id):
        with self.engine.connect() as c:
            job = self.get(db.jobs, id, c=c)
            job.pop("request_hash")
            job["sheets"] = [
                dict(r)
                for r in c.execute(
                    select(db.sheets)
                    .where(db.sheets.c.job_id == id)
                    .order_by(db.sheets.c.ordinal)
                ).mappings()
            ]
            job["counts"] = {
                s: sum(row["state"] == s for row in job["sheets"])
                for s in (
                    "queued",
                    "creating",
                    "transferring",
                    "processing",
                    "completed",
                    "failed",
                    "cancelled",
                    "uncertain",
                )
            }
            job["last_event_id"] = (
                c.execute(
                    select(func.max(db.events.c.id)).where(db.events.c.job_id == id)
                ).scalar()
                or 0
            )
        return job

    def aggregate(self, c, id):
        states = list(
            c.execute(
                select(db.sheets.c.state).where(db.sheets.c.job_id == id)
            ).scalars()
        )
        if "uncertain" in states:
            state = "uncertain"
        elif any(s in ("creating", "transferring", "processing") for s in states):
            state = "processing"
        elif "queued" in states:
            state = "queued"
        elif len(set(states)) == 1:
            state = states[0]
        else:
            state = "partial"
        c.execute(update(db.jobs).where(db.jobs.c.id == id).values(state=state))

    def hold_hardware_job(self, id, reason):
        """Hold a queued hardware job only while it still has no device intent."""
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            job = self.get(db.jobs, id, c=c)
            sheets = list(
                c.execute(
                    select(db.sheets).where(db.sheets.c.job_id == id)
                ).mappings()
            )
            attempted = c.execute(
                select(db.attempts.c.id)
                .join(db.sheets)
                .where(db.sheets.c.job_id == id)
                .limit(1)
            ).first()
            if (
                job["execution_mode"] != "hardware"
                or job["state"] != "queued"
                or len(sheets) != 1
                or sheets[0]["state"] != "queued"
                or attempted
            ):
                return False
            evidence = {
                "origin": "worker",
                "reason": "printer_unavailable_before_device_intent",
                "error": reason,
                "resume_required": True,
            }
            c.execute(
                update(db.sheets)
                .where(db.sheets.c.id == sheets[0]["id"])
                .values(state="held", evidence=evidence)
            )
            c.execute(update(db.jobs).where(db.jobs.c.id == id).values(state="held"))
            self.event(c, id, "held", sheets[0]["id"], "worker", evidence)
        return True

    def resume_hardware_job(self, id):
        """Requeue one held hardware sheet that has never reached device intent."""
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            job = self.get(db.jobs, id, c=c)
            sheets = list(
                c.execute(
                    select(db.sheets).where(db.sheets.c.job_id == id)
                ).mappings()
            )
            if job["execution_mode"] != "hardware" or job["state"] != "held":
                raise Problem(
                    "JOB_NOT_RESUMABLE",
                    "Only a held hardware job can be resumed",
                    409,
                )
            if len(sheets) != 1 or sheets[0]["state"] != "held":
                raise Problem(
                    "JOB_NOT_RESUMABLE",
                    "The held job does not contain exactly one held sheet",
                    409,
                )
            if c.execute(
                select(db.attempts.c.id)
                .where(db.attempts.c.sheet_id == sheets[0]["id"])
                .limit(1)
            ).first():
                raise Problem(
                    "JOB_NOT_RESUMABLE",
                    "This job already reached device intent and cannot be replayed",
                    409,
                )
            if c.execute(
                select(db.jobs.c.id)
                .where(
                    db.jobs.c.execution_mode == "hardware",
                    db.jobs.c.state.in_(
                        ["creating", "transferring", "processing", "uncertain"]
                    ),
                )
                .limit(1)
            ).first():
                raise Problem(
                    "HARDWARE_DISPATCH_BLOCKED",
                    "Resolve the active or uncertain hardware job before resuming",
                    409,
                )
            evidence = {
                "origin": "operator",
                "resumed_from": "held",
                "device_intent_previously_created": False,
            }
            c.execute(
                update(db.sheets)
                .where(db.sheets.c.id == sheets[0]["id"])
                .values(state="queued", evidence=evidence)
            )
            c.execute(
                update(db.jobs).where(db.jobs.c.id == id).values(state="queued")
            )
            self.event(c, id, "queued", sheets[0]["id"], "operator", evidence)
        return self.job(id)

    def cancel(self, id):
        with self.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            self.get(db.jobs, id, c=c)
            queued = list(
                c.execute(
                    select(db.sheets.c.id).where(
                        db.sheets.c.job_id == id, db.sheets.c.state == "queued"
                    )
                ).scalars()
            )
            if not queued:
                raise Problem(
                    "CANCELLATION_UNAVAILABLE",
                    "Only sheets that have not reached the device intent can be cancelled",
                    409,
                )
            for sheet in queued:
                c.execute(
                    update(db.sheets)
                    .where(db.sheets.c.id == sheet)
                    .values(
                        state="cancelled",
                        evidence={
                            "origin": "operator",
                            "reason": "cancelled_before_device_intent",
                        },
                    )
                )
                self.event(c, id, "cancelled", sheet, "operator")
            self.aggregate(c, id)
        return self.job(id)
