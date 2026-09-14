from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Polygon

Positive = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)]
CutDistance = Annotated[float, Field(ge=0, le=20, allow_inf_nan=False)]
Point = tuple[
    Annotated[float, Field(allow_inf_nan=False)],
    Annotated[float, Field(allow_inf_nan=False)],
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class WorkerStatus(Strict):
    state: Literal["starting", "idle", "working", "blocked", "stopped", "offline"]
    last_seen_at: str | None
    current_job_id: str | None
    message: str | None
    fresh: bool


class PrinterInfo(Strict):
    id: str
    name: str
    found: bool
    ready: bool
    status: Literal["ready", "device_missing", "worker_offline", "attention"]
    worker: WorkerStatus
    attention_job_id: str | None
    execution_mode: Literal["hardware", "simulator"]
    profile: dict[str, Any]
    profiles: list[dict[str, Any]]
    hardware_validated: bool


class StickerInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    asset_id: str
    # Normalized source coordinates. Explicit rectangle is the initial UI preset.
    outline: list[Point] = Field(
        default_factory=lambda: [(0, 0), (1, 0), (1, 1), (0, 1)],
        min_length=3,
        max_length=2000,
    )
    cut_padding_mm: CutDistance = 0
    minimum_cut_width_mm: CutDistance = 0

    @model_validator(mode="after")
    def valid_outline(self):
        if any(x < 0 or x > 1 or y < 0 or y > 1 for x, y in self.outline):
            raise ValueError(
                "Outline coordinates must be in normalized source space [0, 1]"
            )
        if len(set(self.outline)) != len(self.outline):
            raise ValueError("Use distinct vertices and implicit closure")
        poly = Polygon(self.outline)
        if not poly.is_valid or poly.area <= 0:
            raise ValueError("Outline must be a simple nonzero polygon")
        return self


class StickerEdit(StickerInput):
    expected_revision: int = Field(ge=1)


class ExtractionInput(Strict):
    name: str = Field(min_length=1, max_length=120)


class ProjectCutPolicyInput(Strict):
    expected_revision: int = Field(ge=1)
    cut_padding_mm: CutDistance = 0
    minimum_cut_width_mm: CutDistance = 0
    fit_extracted_sheet: bool = True


class Placement(Strict):
    id: str
    sticker_id: str
    x_mm: Nonnegative
    y_mm: Nonnegative
    width_mm: Positive
    height_mm: Positive
    rotation: Literal[0, 90] = 0
    pinned: bool = False


class Page(Strict):
    id: str
    placements: list[Placement] = Field(max_length=1000)
    source_layout: bool = False


class ProjectInput(Strict):
    kind: Literal["sticker"] = "sticker"
    name: str = Field(min_length=1, max_length=120)
    bindings: dict[str, int]
    pages: list[Page] = Field(default_factory=list, max_length=100)


class ProjectEdit(ProjectInput):
    expected_revision: int = Field(ge=1)


class RestoreSourceLayoutInput(Strict):
    expected_revision: int = Field(ge=1)


class LayoutInput(Strict):
    revision: int = Field(ge=1)
    sticker_id: str
    mode: Literal["fill", "grid", "quantity"] = "fill"
    width_mm: Positive = 25
    height_mm: Positive = 25
    gap_mm: Nonnegative = 2
    margin_mm: Annotated[float, Field(ge=2, le=50, allow_inf_nan=False)] = 5
    quantity: int | None = Field(default=None, ge=1, le=1000)
    rows: int | None = Field(default=None, ge=1, le=100)
    columns: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def mode_fields(self):
        if self.mode == "quantity":
            if (
                self.quantity is None
                or self.rows is not None
                or self.columns is not None
            ):
                raise ValueError("Quantity mode requires only quantity")
        elif self.mode == "grid":
            if self.rows is None or self.columns is None or self.quantity is not None:
                raise ValueError("Grid mode requires rows and columns only")
        elif any(v is not None for v in (self.quantity, self.rows, self.columns)):
            raise ValueError("Fill mode does not accept count or grid constraints")
        return self


class RenderInput(Strict):
    revision: int = Field(ge=1)
    profile_id: Literal["simulator-v1"] = "simulator-v1"
    lossless_required: Literal[True] = True
    image_format: Literal["auto_lossless", "png"] = "auto_lossless"


class PhotoCrop(Strict):
    x: Annotated[float, Field(ge=0, le=1)] = 0
    y: Annotated[float, Field(ge=0, le=1)] = 0
    width: Annotated[float, Field(gt=0, le=1)] = 1
    height: Annotated[float, Field(gt=0, le=1)] = 1

    @model_validator(mode="after")
    def inside_source(self):
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("Crop rectangle must stay within normalized source bounds")
        return self


Adjustment = Annotated[float, Field(ge=-100, le=100, allow_inf_nan=False)]


class PhotoAdjustments(Strict):
    exposure: Annotated[float, Field(ge=-2, le=2, allow_inf_nan=False)] = 0
    contrast: Adjustment = 0
    highlights: Adjustment = 0
    shadows: Adjustment = 0
    temperature: Adjustment = 0
    tint: Adjustment = 0
    saturation: Adjustment = 0
    sharpening: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 0
    grain: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 0


class PhotoSettings(Strict):
    crop: PhotoCrop = Field(default_factory=PhotoCrop)
    layout: Literal[
        "single", "yearbook_2x3", "yearbook_1_5x2", "photobooth_2x6"
    ] = "single"
    orientation: Literal["auto", "portrait", "landscape"] = "auto"
    framing: Literal["fill", "contain"] = "fill"
    preset: Literal[
        "original", "auto_balance", "vivid", "warm", "cool", "black_and_white"
    ] = "original"
    film_look: Literal[
        "none", "warm_negative", "faded_print", "vivid_slide", "cool_chrome"
    ] = "none"
    print_quality: int = Field(default=95, ge=60, le=95)
    adjustments: PhotoAdjustments = Field(default_factory=PhotoAdjustments)


class PhotoProjectInput(PhotoSettings):
    name: str = Field(min_length=1, max_length=120)
    asset_id: str


class PhotoProjectEdit(PhotoProjectInput):
    expected_revision: int = Field(ge=1)


class PhotoRenderInput(Strict):
    revision: int = Field(ge=1)


class JobInput(Strict):
    render_id: str
    printer_id: Literal["simulator", "pixcut-s1-usb"] = "simulator"
    mode: Literal["print_cut", "print_only"] = "print_cut"
    pages: list[int] = Field(min_length=1, max_length=100)
    copies: int = Field(default=1, ge=1, le=100)
    accepted_profile_id: Literal[
        "simulator-v1",
        "sticker-4x7-jpeg",
        "photo-4x6-jpeg",
    ]
    confirmed: Literal[True]
    confirmed_media: Literal["4x6 photo paper"] | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)
    name: str = Field(default="Sticker sheet", min_length=1, max_length=120)

    @model_validator(mode="after")
    def unique_pages(self):
        if len(set(self.pages)) != len(self.pages) or any(p < 0 for p in self.pages):
            raise ValueError("Page indices must be distinct and nonnegative")
        if len(self.pages) * self.copies > 1000:
            raise ValueError("A job may contain at most 1000 sheets")
        if self.mode == "print_only" and self.confirmed_media != "4x6 photo paper":
            raise ValueError(
                "Print-only jobs require explicit 4x6 photo-paper confirmation"
            )
        if self.mode == "print_cut" and self.confirmed_media is not None:
            raise ValueError("Sticker jobs do not accept photo-paper confirmation")
        return self


class ReprintInput(Strict):
    sheet_ids: list[str] = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=100)
    confirmed: Literal[True]
    accepted_profile_id: Literal[
        "simulator-v1",
    ]
    idempotency_key: str = Field(min_length=8, max_length=128)


class ResolutionInput(Strict):
    sheet_id: str
    outcome: Literal["completed", "failed", "uncertain"]
    note: str = Field(min_length=1, max_length=2000)
    expected_event_id: int
    printer_restarted: bool = False


class Ring(Strict):
    closed: Literal[True]
    points: list[Point] = Field(min_length=3, max_length=2000)


class CutPolicy(Strict):
    cut_padding_mm: CutDistance = 0
    minimum_cut_width_mm: CutDistance = 0


class GeometryProvenance(Strict):
    kind: Literal["draft", "render"]
    project_id: str
    project_revision: int = Field(ge=1)
    render_id: str | None


class CutShape(Strict):
    id: str
    sticker_id: str
    sticker_revision: int = Field(ge=1)
    placement_id: str
    outer: Ring
    holes: list[Ring]
    cut_policy: CutPolicy = Field(default_factory=CutPolicy)

    @model_validator(mode="after")
    def valid_topology(self):
        for ring in [self.outer, *self.holes]:
            if len(set(ring.points)) != len(ring.points):
                raise ValueError("Rings use distinct vertices and implicit closure")
        polygon = Polygon(self.outer.points, [hole.points for hole in self.holes])
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError("Invalid outer/hole topology")
        return self


class CutGeometry(Strict):
    schema_version: Literal["1.0"]
    geometry_engine: str
    provenance: GeometryProvenance
    page_id: str
    page_mm: tuple[Positive, Positive]
    units: Literal["mm"]
    coordinate_space: Literal["sheet_design"]
    origin: Literal["top_left"]
    x_direction: Literal["right"]
    y_direction: Literal["down"]
    geometry_stage: Literal["finished_cut_pre_device"]
    shapes: list[CutShape]
