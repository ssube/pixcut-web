export type Point = [number, number];
export interface Ring {
  closed: true;
  points: Point[];
}
export type CutGeometry =
  import("./api.generated").components["schemas"]["CutGeometry"];
export interface Placement {
  id: string;
  sticker_id: string;
  x_mm: number;
  y_mm: number;
  width_mm: number;
  height_mm: number;
  rotation: 0 | 90;
  pinned: boolean;
}
export interface Page {
  id: string;
  placements: Placement[];
  source_layout?: boolean;
}
export interface Project {
  id: string;
  kind?: "sticker";
  name: string;
  revision: number;
  bindings: Record<string, number>;
  pages: Page[];
}
export interface PhotoCrop {
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface PhotoAdjustments {
  exposure: number;
  contrast: number;
  highlights: number;
  shadows: number;
  temperature: number;
  tint: number;
  saturation: number;
  sharpening: number;
  grain: number;
}
export interface PhotoProject {
  id: string;
  kind: "photo";
  name: string;
  revision: number;
  asset_id: string;
  crop: PhotoCrop;
  layout: "single" | "yearbook_2x3" | "yearbook_1_5x2" | "photobooth_2x6";
  orientation: "auto" | "portrait" | "landscape";
  framing: "fill" | "contain";
  preset:
    | "original"
    | "auto_balance"
    | "vivid"
    | "warm"
    | "cool"
    | "black_and_white";
  film_look:
    | "none"
    | "warm_negative"
    | "faded_print"
    | "vivid_slide"
    | "cool_chrome";
  print_quality: number;
  adjustments: PhotoAdjustments;
  adjustment_pipeline_version: string;
}
export interface Sticker {
  id: string;
  name: string;
  asset_id: string;
  revision: number;
  outline: Point[];
  cut_padding_mm?: number;
  minimum_cut_width_mm?: number;
}
export interface ExtractionResult {
  source_asset_id: string;
  method: "alpha" | "edge-connected-white" | "opaque-image";
  component_count: number;
  stickers: Sticker[];
  project: Project;
}
export interface Artifact {
  hash: string;
  mime: string;
  size: number;
}
export interface Render {
  id: string;
  project_id: string;
  revision: number;
  hardware_eligible: boolean;
  source_render_id?: string;
  warnings: string[];
  kind?: "photo";
  profile?: { id: string; mode?: string; [key: string]: unknown };
  pages: {
    index: number;
    sticker_count: number;
    raster_hash: string;
    artifacts: Record<string, Artifact>;
  }[];
}
export interface Sheet {
  id: string;
  page: number;
  copy: number;
  ordinal: number;
  state: string;
  evidence: {
    recovery?: {
      action?: "power_cycle_printer";
      automatic_retry?: boolean;
    };
    [key: string]: unknown;
  };
}
export interface Job {
  id: string;
  name: string;
  render_id: string;
  state: string;
  created_at: string;
  reprint_of_job_id: string | null;
  execution_mode: "simulator" | "hardware";
  sheets: Sheet[];
  counts: Record<string, number>;
  last_event_id: number;
  request: { mode?: "print_cut" | "print_only"; [key: string]: unknown };
}
export interface Printer {
  id: "simulator" | "pixcut-s1-usb";
  name: string;
  found: boolean;
  ready: boolean;
  status: "ready" | "device_missing" | "worker_offline" | "attention";
  attention_job_id: string | null;
  worker: {
    state: "starting" | "idle" | "working" | "blocked" | "stopped" | "offline";
    last_seen_at: string | null;
    current_job_id: string | null;
    message: string | null;
    fresh: boolean;
  };
  execution_mode: "simulator" | "hardware";
  hardware_validated: boolean;
  profile: {
    id: "simulator-v1" | "sticker-4x7-jpeg" | "photo-4x6-jpeg";
    [key: string]: unknown;
  };
  profiles: {
    id: string;
    mode?: "print_cut" | "print_only";
    hardware_validated?: boolean;
    [key: string]: unknown;
  }[];
}
