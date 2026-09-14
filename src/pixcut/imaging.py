from io import BytesIO
import math
import warnings
from PIL import Image, ImageOps, ImageCms
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from .artifacts import canonical, digest
from .models import LayoutInput, Page, Placement, CutGeometry

PAGE_MM = (101.6, 177.8)
RASTER = (1200, 2100)
PROFILE = {
    "id": "simulator-v1",
    "execution_mode": "simulator",
    "hardware_validated": False,
    "image_format": "png",
    "document_format": 10,
    "raster": list(RASTER),
    "page_mm": list(PAGE_MM),
    "guard_mm": 2,
    "max_bytes": 32 * 1024 * 1024,
    "serializer": "simulator-plt-v1",
    "units_per_mm": 40,
}
Image.MAX_IMAGE_PIXELS = 20_000_000


def png(image):
    out = BytesIO()
    image.save(out, format="PNG")
    encoded = out.getvalue()
    with Image.open(BytesIO(encoded)) as decoded:
        if decoded.mode != image.mode or decoded.tobytes() != image.tobytes():
            raise ValueError("LOSSLESS_VERIFICATION_FAILED")
    return encoded


def normalize(data):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as source:
            if (
                source.format not in ("PNG", "JPEG", "WEBP")
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ValueError("Use a single-frame PNG, JPEG or WebP image")
            if source.width * source.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("Decoded image exceeds 20 million pixels")
            mime = Image.MIME[source.format]
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            policy = "untagged-assumed-sRGB"
            if source.info.get("icc_profile"):
                alpha = image.getchannel("A")
                image = ImageCms.profileToProfile(
                    image.convert("RGB"),
                    ImageCms.ImageCmsProfile(BytesIO(source.info["icc_profile"])),
                    ImageCms.createProfile("sRGB"),
                    outputMode="RGB",
                ).convert("RGBA")
                image.putalpha(alpha)
                policy = "embedded-ICC-to-sRGB"
            image.info.clear()
            return image, png(image), mime, policy


def layout(request: LayoutInput):
    w, h = PAGE_MM
    cols = math.floor(
        (w - 2 * request.margin_mm + request.gap_mm)
        / (request.width_mm + request.gap_mm)
    )
    rows = math.floor(
        (h - 2 * request.margin_mm + request.gap_mm)
        / (request.height_mm + request.gap_mm)
    )
    if cols < 1 or rows < 1:
        raise ValueError(
            "LAYOUT_OVERFLOW: sticker does not fit at the requested physical size"
        )
    if request.mode == "grid":
        if request.columns > cols or request.rows > rows:
            raise ValueError(
                "LAYOUT_OVERFLOW: requested grid does not fit; size was not changed"
            )
        cols, rows = request.columns, request.rows
    capacity = cols * rows
    count = request.quantity if request.mode == "quantity" else capacity
    if count > 1000:
        raise ValueError("LAYOUT_LIMIT: at most 1000 placements per proposal")
    pages = []
    for first in range(0, count, capacity):
        page_index = len(pages)
        placed = []
        for index in range(min(capacity, count - first)):
            placed.append(
                Placement(
                    id=f"p{page_index}-{index}",
                    sticker_id=request.sticker_id,
                    x_mm=request.margin_mm
                    + (index % cols) * (request.width_mm + request.gap_mm),
                    y_mm=request.margin_mm
                    + (index // cols) * (request.height_mm + request.gap_mm),
                    width_mm=request.width_mm,
                    height_mm=request.height_mm,
                )
            )
        pages.append(Page(id=f"page-{page_index}", placements=placed).model_dump())
    if len(pages) > 100:
        raise ValueError("LAYOUT_LIMIT: at most 100 pages per project")
    return {"pages": pages, "achieved_quantity": count, "unplaced": [], "warnings": []}


def resolve(project, definitions, render_id=None):
    result = []
    for page in project["pages"]:
        shapes, art_envelopes, cut_polygons = [], [], []
        source_layout = page.get("source_layout", False) or (
            bool(page["placements"])
            and all(
                placement["id"].startswith("extracted-")
                for placement in page["placements"]
            )
        )
        for p in page["placements"]:
            definition = definitions[p["sticker_id"]]
            w, h = p["width_mm"], p["height_mm"]
            x, y = p["x_mm"], p["y_mm"]
            rotated = p["rotation"] == 90
            # Positive rotation is clockwise in the Y-down sheet frame.
            points = [
                (
                    x + (h - v * h if rotated else u * w),
                    y + (u * w if rotated else v * h),
                )
                for u, v in definition["outline"]
            ]
            art_envelope = box(
                x, y, x + (h if rotated else w), y + (w if rotated else h)
            )
            guard = box(2, 2, PAGE_MM[0] - 2, PAGE_MM[1] - 2)
            if not guard.covers(art_envelope):
                raise ValueError(
                    f"SHEET_GUARD: placement {p['id']} crosses the simulator guard"
                )
            if not source_layout and any(
                art_envelope.intersection(other).area > 1e-9
                for other in art_envelopes
            ):
                raise ValueError(
                    f"PLACEMENT_COLLISION: placement {p['id']} overlaps another artwork envelope"
                )
            art_envelopes.append(art_envelope)

            polygon = Polygon(points)
            padding = definition.get("cut_padding_mm", 0)
            if padding:
                polygon = polygon.buffer(padding, quad_segs=8)
            minimum_width = definition.get("minimum_cut_width_mm", 0)
            if minimum_width:
                # Keep the complete, evenly padded contour and only bridge details
                # whose depth is within the requested feature radius. A morphological
                # close (buffer out, then in) seals narrow but deep openings such as
                # the gap beside a chimney; its exterior then shortcuts the whole
                # opening. Unioning a simplified contour keeps those deep boundaries
                # while removing shallow cut details, and can never pull the cut
                # inside the uniform padding envelope.
                detail_radius = minimum_width / 2
                simplified = polygon.simplify(
                    detail_radius, preserve_topology=True
                )
                polygon = polygon.union(simplified)
                # A retained deep opening can taper to a near-zero-width return,
                # making the blade enter and leave along almost the same line.
                # Close only that narrow tip with half the feature radius. Wider
                # portions of the opening remain, and union preserves the complete
                # padding envelope if buffering introduces any numeric contraction.
                hairpin_radius = minimum_width / 4
                cleaned = polygon.buffer(
                    hairpin_radius, quad_segs=8
                ).buffer(-hairpin_radius, quad_segs=8)
                device_step_mm = 1 / 40
                cleaned = cleaned.simplify(
                    min(device_step_mm, hairpin_radius / 4),
                    preserve_topology=True,
                )
                polygon = polygon.union(cleaned)
            if polygon.is_empty or polygon.geom_type != "Polygon" or not polygon.is_valid:
                raise ValueError(
                    f"CUT_POLICY_TOPOLOGY: cut settings make placement {p['id']} invalid"
                )
            polygon = orient(polygon, sign=1)
            points = list(polygon.exterior.coords)[:-1]
            if len(points) > 2000:
                raise ValueError(
                    f"CUT_COMPLEXITY: placement {p['id']} exceeds 2000 cut vertices"
                )
            if not guard.covers(polygon):
                raise ValueError(
                    f"SHEET_GUARD: cut padding for placement {p['id']} crosses the simulator guard"
                )
            if any(polygon.intersects(other) for other in cut_polygons):
                raise ValueError(
                    f"CUT_COLLISION: adjusted cut for placement {p['id']} intersects another cut"
                )
            cut_polygons.append(polygon)
            shapes.append(
                {
                    "id": p["id"],
                    "sticker_id": p["sticker_id"],
                    "sticker_revision": definition["revision"],
                    "placement_id": p["id"],
                    "outer": {"closed": True, "points": points},
                    "holes": [],
                    "cut_policy": {
                        "cut_padding_mm": padding,
                        "minimum_cut_width_mm": minimum_width,
                    },
                }
            )
        result.append(
            {
                "schema_version": "1.0",
                "geometry_engine": "pixcut-polygon-v2",
                "provenance": {
                    "kind": "render" if render_id else "draft",
                    "project_id": project["id"],
                    "project_revision": project["revision"],
                    "render_id": render_id,
                },
                "page_id": page["id"],
                "page_mm": list(PAGE_MM),
                "units": "mm",
                "coordinate_space": "sheet_design",
                "origin": "top_left",
                "x_direction": "right",
                "y_direction": "down",
                "geometry_stage": "finished_cut_pre_device",
                "shapes": shapes,
            }
        )
    return [
        CutGeometry.model_validate(snapshot).model_dump(mode="json")
        for snapshot in result
    ]


def serialize_plt(snapshot):
    # Simulator convention only. Never a commissioned device calibration.
    lines = ["IN;", "SP1;"]
    recovered = []
    for shape in snapshot["shapes"]:
        points = [(round(x * 40), round(y * 40)) for x, y in shape["outer"]["points"]]
        start, *rest = points
        lines.append(f"PU{start[0]},{start[1]};")
        lines.append("PD" + ",".join(str(v) for pt in [*rest, start] for v in pt) + ";")
        lines.append("PU;")
    lines.append("SP0;")
    data = "\n".join(lines).encode()
    # Independently parse the serialized commands for the approval overlay.
    for command in data.decode().replace("\n", "").split(";"):
        if command.startswith("PU") and len(command) > 2:
            nums = list(map(int, command[2:].split(",")))
            recovered.append([(nums[0] / 40, nums[1] / 40)])
        elif command.startswith("PD"):
            nums = list(map(int, command[2:].split(",")))
            recovered[-1].extend(
                (nums[i] / 40, nums[i + 1] / 40) for i in range(0, len(nums), 2)
            )
    paths = "".join(
        '<polyline points="' + " ".join(f"{x},{y}" for x, y in ring) + '"/>'
        for ring in recovered
    )
    overlay = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 101.6 177.8" width="101.6mm" height="177.8mm"><g fill="none" stroke="#d61f75" stroke-width="0.2">{paths}</g></svg>'
    return data, overlay.encode()


def render_page(page, geometry, definitions, assets, store):
    image = Image.new("RGB", RASTER, "white")
    sx, sy = RASTER[0] / PAGE_MM[0], RASTER[1] / PAGE_MM[1]
    for p in page["placements"]:
        asset = assets[definitions[p["sticker_id"]]["asset_id"]]
        with Image.open(BytesIO(store.read(asset["normalized"]["hash"]))) as source:
            x, y = round(p["x_mm"] * sx), round(p["y_mm"] * sy)
            # Quantize shared physical endpoints rather than accumulating pixel widths.
            w = (
                round(
                    (p["x_mm"] + (p["height_mm"] if p["rotation"] else p["width_mm"]))
                    * sx
                )
                - x
            )
            h = (
                round(
                    (p["y_mm"] + (p["width_mm"] if p["rotation"] else p["height_mm"]))
                    * sy
                )
                - y
            )
            art = (
                source.transpose(Image.Transpose.ROTATE_270)
                if p["rotation"]
                else source.copy()
            )
            art = art.resize((w, h), Image.Resampling.LANCZOS)
            image.paste(art, (x, y), art.getchannel("A"))
    encoded = png(image)
    if len(encoded) > PROFILE["max_bytes"]:
        raise ValueError("LOSSLESS_PAYLOAD_TOO_LARGE")
    plt, overlay = serialize_plt(geometry)
    thumb = image.copy()
    thumb.thumbnail((240, 420))
    return {
        "image.png": (encoded, "image/png"),
        "cut-geometry.json": (canonical(geometry), "application/json"),
        "cut.plt": (plt, "application/vnd.hp-hpgl"),
        "overlay.svg": (overlay, "image/svg+xml"),
        "thumbnail.png": (png(thumb), "image/png"),
    }, digest(image.tobytes())
