import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type {
  Project,
  Sticker,
  Render,
  Job,
  Printer,
  CutGeometry,
  Point,
  ExtractionResult,
  PhotoProject,
  PhotoCrop,
  PhotoAdjustments,
} from "./types";
import { download, exportSVG, exportDXF, exportSTL } from "./exports";
import { calculateSheetUsage } from "./usage";
import "./style.css";

const base = "/api/v1";
const themeKey = "pixcut-theme";

function savedPumpkinSpice() {
  try {
    return localStorage.getItem(themeKey) === "pumpkin-spice";
  } catch {
    return false;
  }
}

function applyPumpkinSpice(enabled: boolean) {
  if (enabled) document.documentElement.dataset.theme = "pumpkin-spice";
  else delete document.documentElement.dataset.theme;
  document
    .querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    ?.setAttribute("content", enabled ? "#a94620" : "#174c42");
  try {
    if (enabled) localStorage.setItem(themeKey, "pumpkin-spice");
    else localStorage.removeItem(themeKey);
  } catch {
    // The theme still works for this visit when storage is unavailable.
  }
}

applyPumpkinSpice(savedPumpkinSpice());

async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = sessionStorage.getItem("pixcut-token");
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const response = await fetch(base + path, {
    method,
    headers,
    signal,
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    let error: { message?: string; detail?: unknown } = {};
    try {
      error = JSON.parse(text);
    } catch {
      error = { message: text };
    }
    throw new Error(
      error.message || JSON.stringify(error.detail) || response.statusText,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
async function apiBlob(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
) {
  const headers: Record<string, string> = {};
  const token = sessionStorage.getItem("pixcut-token");
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(base + path, {
    method,
    headers,
    signal,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    try {
      const error = JSON.parse(text);
      throw new Error(error.message || response.statusText);
    } catch (cause) {
      if (cause instanceof SyntaxError)
        throw new Error(text || response.statusText);
      throw cause;
    }
  }
  return response.blob();
}
function AssetImage({
  path,
  alt = "",
  ...props
}: { path: string } & React.ImgHTMLAttributes<HTMLImageElement>) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
    setUrl("");
    let live = true,
      objectUrl = "";
    const token = sessionStorage.getItem("pixcut-token");
    fetch(base + path, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error("Preview unavailable");
        return r.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (live) setUrl(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => {
        if (live) {
          setUrl("");
          setFailed(true);
        }
      });
    return () => {
      live = false;
      URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return url ? (
    <img src={url} alt={alt} {...props} />
  ) : failed ? (
    <span
      className="asset-placeholder"
      role="img"
      aria-label={`${alt || "Preview"} unavailable`}
    >
      Preview unavailable
    </span>
  ) : (
    <span className="muted" role="status">
      Loading preview…
    </span>
  );
}
function TrashIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17">
      <path
        d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const zeroAdjustments: PhotoAdjustments = {
  exposure: 0,
  contrast: 0,
  highlights: 0,
  shadows: 0,
  temperature: 0,
  tint: 0,
  saturation: 0,
  sharpening: 0,
  grain: 0,
};
type PhotoDraft = Pick<
  PhotoProject,
  | "crop"
  | "layout"
  | "orientation"
  | "framing"
  | "preset"
  | "film_look"
  | "print_quality"
  | "adjustments"
>;
const photoSettings = (project: PhotoProject): PhotoDraft => ({
  crop: project.crop,
  layout: project.layout || "single",
  orientation: project.orientation,
  framing: project.framing,
  preset: project.preset,
  film_look: project.film_look,
  print_quality: project.print_quality ?? 95,
  adjustments: project.adjustments,
});
const presetLabels: [PhotoProject["preset"], string][] = [
  ["original", "Original"],
  ["auto_balance", "Auto Balance"],
  ["vivid", "Vivid"],
  ["warm", "Warm"],
  ["cool", "Cool"],
  ["black_and_white", "Black & White"],
];
const layoutLabels: [PhotoProject["layout"], string, string][] = [
  ["single", "Single", "one 4×6"],
  ["yearbook_2x3", "Yearbook", "four 2×3"],
  ["yearbook_1_5x2", "Yearbook mini", "six 1½×2"],
  ["photobooth_2x6", "Photo booth", "two 2×6 strips"],
];
const jobStateLabels: Record<string, string> = {
  held: "Needs attention",
  queued: "Waiting",
  creating: "Starting",
  transferring: "Sending",
  processing: "Printing",
  completed: "Finished",
  failed: "Problem",
  cancelled: "Cancelled",
  uncertain: "Check printer",
};
const jobStateLabel = (state: string) => jobStateLabels[state] || state;
const needsPrinterRestart = (job: Job) =>
  job.sheets.some(
    (sheet) => sheet.evidence?.recovery?.action === "power_cycle_printer",
  );
const formatOutline = (points: Point[]) =>
  points.map(([x, y]) => `${x.toFixed(3)}, ${y.toFixed(3)}`).join("\n");

type JobsResponse = {
  items: Job[];
  next_cursor: string | null;
  queue_length: number;
};
const mergeJobs = (recent: Job[], existing: Job[]) => {
  const recentIds = new Set(recent.map((job) => job.id));
  return [...recent, ...existing.filter((job) => !recentIds.has(job.id))];
};
async function currentPrinter(expected: Printer) {
  const [current] = await api<Printer[]>("/printers");
  if (
    !current ||
    current.id !== expected.id ||
    current.execution_mode !== expected.execution_mode
  )
    throw new Error(
      "Printer mode changed. Refresh the page, review the sheet, and try again.",
    );
  return current;
}

function PhotoWorkspace({
  printer,
  onQueued,
  onDirtyChange,
}: {
  printer: Printer | null;
  onQueued: () => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [projects, setProjects] = useState<PhotoProject[]>([]);
  const [project, setProject] = useState<PhotoProject | null>(null);
  const [draft, setDraft] = useState<PhotoDraft | null>(null);
  const [assetSize, setAssetSize] = useState<[number, number]>([2, 3]);
  const [preview, setPreview] = useState("");
  const [before, setBefore] = useState("");
  const [compare, setCompare] = useState(false);
  const [exact, setExact] = useState<Render | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<{
    key: string;
    signature: string;
  } | null>(null);
  const [drag, setDrag] = useState<{
    handle: string;
    x: number;
    y: number;
    crop: PhotoCrop;
  } | null>(null);

  const dirty =
    !!project &&
    !!draft &&
    JSON.stringify(draft) !== JSON.stringify(photoSettings(project));
  const realMode = printer?.execution_mode === "hardware";
  const photoProfile = printer?.profiles?.find(
    (profile) => profile.mode === "print_only",
  );

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(
    () => () => {
      onDirtyChange(false);
    },
    [onDirtyChange],
  );

  function confirmDiscard() {
    return (
      !dirty ||
      window.confirm("Discard your unsaved photo changes and continue?")
    );
  }

  async function run(action: () => Promise<void>, label = "Working…") {
    setBusy(true);
    setActivity(label);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
      setActivity("");
    }
  }
  async function refreshProjects() {
    setProjects(await api<PhotoProject[]>("/projects?kind=photo"));
  }
  async function load(next: PhotoProject) {
    const asset = await api<{ width_px: number; height_px: number }>(
      `/assets/${next.asset_id}`,
    );
    setProject(next);
    setDraft(photoSettings(next));
    setAssetSize([asset.width_px, asset.height_px]);
    setExact(null);
    setAccepted(false);
  }
  useEffect(() => {
    void run(refreshProjects, "Loading photos…");
  }, []);

  useEffect(() => {
    if (!project || !draft) return;
    let live = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      let nextPreview = "",
        nextBefore = "";
      Promise.all([
        apiBlob(
          `/photo-projects/${project.id}/draft-preview`,
          "POST",
          draft,
          controller.signal,
        ),
        apiBlob(
          `/photo-projects/${project.id}/draft-preview`,
          "POST",
          {
            ...draft,
            preset: "original",
            film_look: "none",
            adjustments: zeroAdjustments,
          },
          controller.signal,
        ),
      ])
        .then(([edited, original]) => {
          nextPreview = URL.createObjectURL(edited);
          nextBefore = URL.createObjectURL(original);
          if (!live) {
            URL.revokeObjectURL(nextPreview);
            URL.revokeObjectURL(nextBefore);
            return;
          }
          setPreview((old) => {
            URL.revokeObjectURL(old);
            return nextPreview;
          });
          setBefore((old) => {
            URL.revokeObjectURL(old);
            return nextBefore;
          });
        })
        .catch((cause) => {
          if (live && (cause as Error).name !== "AbortError")
            setError((cause as Error).message);
        });
    }, 300);
    return () => {
      live = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [project?.id, JSON.stringify(draft)]);

  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    const asset = await api<{ id: string }>("/assets", "POST", form);
    const next = await api<PhotoProject>("/photo-projects", "POST", {
      name: file.name,
      asset_id: asset.id,
      crop: { x: 0, y: 0, width: 1, height: 1 },
      layout: "single",
      orientation: "auto",
      framing: "fill",
      preset: "original",
      film_look: "none",
      print_quality: 95,
      adjustments: zeroAdjustments,
    });
    await load(next);
    await refreshProjects();
  }
  async function save() {
    if (!project || !draft) return project;
    if (!dirty) return project;
    const next = await api<PhotoProject>(
      `/photo-projects/${project.id}`,
      "PATCH",
      {
        name: project.name,
        asset_id: project.asset_id,
        ...draft,
        expected_revision: project.revision,
      },
    );
    await load(next);
    await refreshProjects();
    return next;
  }
  async function prepareExact() {
    const saved = await save();
    if (!saved) return;
    const result = await api<Render>(
      `/photo-projects/${saved.id}/renders`,
      "POST",
      {
        revision: saved.revision,
      },
    );
    setExact(result);
    setAccepted(false);
  }
  async function submitPhoto() {
    if (!exact || !project || !photoProfile || !accepted) return;
    const livePrinter = await currentPrinter(printer!);
    const livePhotoProfile = livePrinter.profiles?.find(
      (profile) => profile.mode === "print_only",
    );
    if (!livePhotoProfile || livePrinter.execution_mode !== "hardware")
      throw new Error(
        "The printer connection changed. Refresh the page and try again.",
      );
    if (
      !window.confirm(
        "Before printing, make sure 4×6 photo paper is loaded. Is the correct paper in the printer?",
      )
    )
      return;
    const payload = {
      render_id: exact.id,
      printer_id: livePrinter.id,
      mode: "print_only",
      pages: [0],
      copies: 1,
      accepted_profile_id: livePhotoProfile.id,
      confirmed: true,
      confirmed_media: "4x6 photo paper",
      name: project.name,
    };
    const signature = JSON.stringify(payload);
    const key =
      pending?.signature === signature ? pending.key : crypto.randomUUID();
    setPending({ key, signature });
    await api("/jobs", "POST", { ...payload, idempotency_key: key });
    setPending(null);
    setAccepted(false);
    await onQueued();
  }
  function changeCrop(next: PhotoCrop) {
    setDraft((current) => current && { ...current, crop: next });
    setExact(null);
  }
  function adjustCrop(action: string) {
    if (!draft) return;
    const step = 0.01;
    const minimum = 0.03;
    let { x, y, width, height } = draft.crop;
    if (action === "left") x = Math.max(0, x - step);
    if (action === "right") x = Math.min(1 - width, x + step);
    if (action === "up") y = Math.max(0, y - step);
    if (action === "down") y = Math.min(1 - height, y + step);
    if (action === "wider") {
      const amount = Math.min(step, x, 1 - x - width);
      x -= amount;
      width += amount * 2;
    }
    if (action === "narrower" && width > minimum) {
      const amount = Math.min(step, (width - minimum) / 2);
      x += amount;
      width -= amount * 2;
    }
    if (action === "taller") {
      const amount = Math.min(step, y, 1 - y - height);
      y -= amount;
      height += amount * 2;
    }
    if (action === "shorter" && height > minimum) {
      const amount = Math.min(step, (height - minimum) / 2);
      y += amount;
      height -= amount * 2;
    }
    changeCrop({ x, y, width, height });
  }
  function pointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag || !draft) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const dx = (event.clientX - drag.x) / rect.width;
    const dy = (event.clientY - drag.y) / rect.height;
    let { x, y, width, height } = drag.crop;
    const minimum = 0.03;
    if (drag.handle === "move") {
      x = Math.max(0, Math.min(1 - width, x + dx));
      y = Math.max(0, Math.min(1 - height, y + dy));
    } else {
      if (drag.handle.includes("w")) {
        const right = x + width;
        x = Math.max(0, Math.min(right - minimum, x + dx));
        width = right - x;
      }
      if (drag.handle.includes("e"))
        width = Math.max(minimum, Math.min(1 - x, width + dx));
      if (drag.handle.includes("n")) {
        const bottom = y + height;
        y = Math.max(0, Math.min(bottom - minimum, y + dy));
        height = bottom - y;
      }
      if (drag.handle.includes("s"))
        height = Math.max(minimum, Math.min(1 - y, height + dy));
    }
    changeCrop({ x, y, width, height });
  }
  const adjustmentLabels: [keyof PhotoAdjustments, string, number, number][] = [
    ["exposure", "Exposure", -2, 2],
    ["contrast", "Contrast", -100, 100],
    ["highlights", "Highlights", -100, 100],
    ["shadows", "Shadows", -100, 100],
    ["temperature", "Temperature", -100, 100],
    ["tint", "Tint", -100, 100],
    ["saturation", "Saturation", -100, 100],
    ["sharpening", "Sharpening", 0, 100],
    ["grain", "Film grain", 0, 100],
  ];

  return (
    <main className="workspace photo-workspace" aria-busy={busy}>
      <span className="sr-only" role="status" aria-live="polite">
        {activity}
      </span>
      <aside className="library">
        <small>01 / PHOTO</small>
        <h2>Your photos</h2>
        <label className="upload">
          <span>＋</span>
          <strong>Import a photo</strong>
          <small>PNG, JPEG or WebP · up to 25 MiB</small>
          <input
            aria-label="Import a photo"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file && confirmDiscard())
                void run(() => upload(file), "Uploading photo…");
              event.target.value = "";
            }}
          />
        </label>
        <h3>Saved photos</h3>
        {projects.map((item) => (
          <div
            className={`project-row ${item.id === project?.id ? "selected" : ""}`}
            key={item.id}
          >
            <button
              className="project"
              aria-current={item.id === project?.id ? "true" : undefined}
              onClick={() => {
                if (confirmDiscard())
                  void run(() => load(item), "Opening photo…");
              }}
            >
              <span>▧</span>
              <div>
                {item.name}
                <small>Saved · edit anytime</small>
              </div>
            </button>
            <button
              className="delete-project"
              aria-label={`Delete ${item.name}`}
              onClick={() =>
                void run(async () => {
                  if (
                    !window.confirm(
                      `Delete project “${item.name}”? This cannot be undone.`,
                    )
                  )
                    return;
                  await api(`/projects/${item.id}`, "DELETE");
                  if (project?.id === item.id) {
                    setProject(null);
                    setDraft(null);
                    setExact(null);
                  }
                  await refreshProjects();
                }, "Deleting photo…")
              }
            >
              <TrashIcon />
            </button>
          </div>
        ))}
        <div className="library-note">
          Your original photo stays untouched, so you can change the crop or
          color anytime.
        </div>
      </aside>
      <section className="canvas-area">
        {error && (
          <div role="alert" className="photo-error">
            {error}
            <button aria-label="Dismiss error" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        <div className="canvas-title">
          <div>
            <small>{exact ? "READY TO PRINT" : "PHOTO PREVIEW"}</small>
            <h1>{project?.name || "A fresh photo"}</h1>
          </div>
          <span className={`pill ${dirty ? "dirty" : ""}`}>
            {dirty
              ? "Unsaved changes"
              : project
                ? "Saved"
                : "Ready when you are"}
          </span>
        </div>
        {!project || !draft ? (
          <div className="photo-empty">
            <span>◫</span>
            <h2>Bring a favorite moment to print.</h2>
            <p>
              Import a photo to crop, brighten, and see how it will look as a
              4×6 print.
            </p>
          </div>
        ) : (
          <>
            <div className="photo-edit-grid">
              <div>
                <h3>Crop</h3>
                <div
                  className="crop-stage"
                  style={{ aspectRatio: `${assetSize[0]} / ${assetSize[1]}` }}
                  onPointerMove={pointerMove}
                  onPointerUp={() => setDrag(null)}
                  onPointerCancel={() => setDrag(null)}
                >
                  <AssetImage
                    path={`/assets/${project.asset_id}/normalized`}
                    alt="Unedited source"
                    draggable={false}
                  />
                  <div className="crop-shade" />
                  <div
                    className="crop-box"
                    style={{
                      left: `${draft.crop.x * 100}%`,
                      top: `${draft.crop.y * 100}%`,
                      width: `${draft.crop.width * 100}%`,
                      height: `${draft.crop.height * 100}%`,
                    }}
                    onPointerDown={(event) => {
                      event.currentTarget.parentElement?.setPointerCapture(
                        event.pointerId,
                      );
                      setDrag({
                        handle: "move",
                        x: event.clientX,
                        y: event.clientY,
                        crop: draft.crop,
                      });
                    }}
                  >
                    {(["nw", "ne", "sw", "se"] as const).map((handle) => (
                      <span
                        aria-hidden="true"
                        key={handle}
                        className={`crop-handle ${handle}`}
                        onPointerDown={(event) => {
                          event.stopPropagation();
                          event.currentTarget.parentElement?.parentElement?.setPointerCapture(
                            event.pointerId,
                          );
                          setDrag({
                            handle,
                            x: event.clientX,
                            y: event.clientY,
                            crop: draft.crop,
                          });
                        }}
                      />
                    ))}
                  </div>
                </div>
                <button
                  className="subtle wide"
                  onClick={() =>
                    changeCrop({ x: 0, y: 0, width: 1, height: 1 })
                  }
                >
                  Reset crop
                </button>
                <details className="crop-fine-tune">
                  <summary>Fine-tune crop</summary>
                  <p className="muted">Move or resize by 1%.</p>
                  <div>
                    {[
                      ["up", "Move up"],
                      ["down", "Move down"],
                      ["left", "Move left"],
                      ["right", "Move right"],
                      ["wider", "Make wider"],
                      ["narrower", "Make narrower"],
                      ["taller", "Make taller"],
                      ["shorter", "Make shorter"],
                    ].map(([action, label]) => (
                      <button
                        type="button"
                        key={action}
                        onClick={() => adjustCrop(action)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </details>
              </div>
              <div>
                <h3>Print preview</h3>
                <div
                  className={`photo-preview ${draft.layout === "single" && (draft.orientation === "landscape" || (draft.orientation === "auto" && draft.crop.width >= draft.crop.height)) ? "landscape" : "portrait"}`}
                >
                  {(compare ? before : preview) ? (
                    <img
                      src={compare ? before : preview}
                      alt={
                        compare ? "Before adjustments" : "Edited photo preview"
                      }
                    />
                  ) : (
                    <span>Balancing preview…</span>
                  )}
                </div>
                <button
                  className={compare ? "active wide" : "wide"}
                  aria-pressed={compare}
                  onClick={() => setCompare((current) => !current)}
                >
                  {compare ? "Show edits" : "Show original"}
                </button>
              </div>
            </div>
            {exact && (
              <div className="exact-proof">
                <AssetImage
                  path={`/renders/${exact.id}/artifacts/0/image.jpg`}
                  alt="Print-ready 4 by 6 photo"
                />
                <div>
                  <strong>Print-ready 4×6 photo</strong>
                  <small>
                    {Math.ceil(
                      exact.pages[0].artifacts["image.jpg"].size / 1024,
                    ).toLocaleString()}{" "}
                    KB · Quality {draft.print_quality}
                  </small>
                </div>
              </div>
            )}
          </>
        )}
      </section>
      <aside className="inspector photo-inspector">
        <small>02 / BALANCE & FRAME</small>
        <h2>Finish your photo</h2>
        <fieldset disabled={!draft || busy}>
          <h3>Presets</h3>
          <div className="preset-grid">
            {presetLabels.map(([value, label]) => (
              <button
                key={value}
                className={draft?.preset === value ? "active" : ""}
                aria-pressed={draft?.preset === value}
                onClick={() => {
                  setDraft(
                    (current) =>
                      current && {
                        ...current,
                        preset: value,
                        adjustments: { ...zeroAdjustments },
                      },
                  );
                  setExact(null);
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <h3>Adjustments</h3>
          {adjustmentLabels.map(([key, label, min, max]) => (
            <label className="slider" key={key}>
              <span>
                {label}
                <output>{draft?.adjustments[key] ?? 0}</output>
              </span>
              <input
                aria-label={label}
                type="range"
                min={min}
                max={max}
                step={key === "exposure" ? 0.1 : 1}
                value={draft?.adjustments[key] ?? 0}
                onChange={(event) => {
                  const value = +event.target.value;
                  setDraft(
                    (current) =>
                      current && {
                        ...current,
                        adjustments: { ...current.adjustments, [key]: value },
                      },
                  );
                  setExact(null);
                }}
              />
            </label>
          ))}
          <label>
            Film look
            <select
              value={draft?.film_look}
              onChange={(event) => {
                setDraft(
                  (current) =>
                    current && {
                      ...current,
                      film_look: event.target.value as PhotoDraft["film_look"],
                    },
                );
                setExact(null);
              }}
            >
              <option value="none">None</option>
              <option value="warm_negative">Warm negative</option>
              <option value="faded_print">Faded print</option>
              <option value="vivid_slide">Vivid slide</option>
              <option value="cool_chrome">Cool chrome</option>
            </select>
          </label>
          <h3>Print layout</h3>
          <div className="layout-grid">
            {layoutLabels.map(([value, label, detail]) => (
              <button
                key={value}
                className={draft?.layout === value ? "active" : ""}
                aria-pressed={draft?.layout === value}
                onClick={() => {
                  setDraft(
                    (current) => current && { ...current, layout: value },
                  );
                  setExact(null);
                }}
              >
                <strong>{label}</strong>
                <small>{detail}</small>
              </button>
            ))}
          </div>
          <h3>Fit to paper</h3>
          <div className="segmented">
            {(["fill", "contain"] as const).map((value) => (
              <button
                key={value}
                className={draft?.framing === value ? "active" : ""}
                aria-pressed={draft?.framing === value}
                onClick={() => {
                  setDraft(
                    (current) => current && { ...current, framing: value },
                  );
                  setExact(null);
                }}
              >
                {value === "fill" ? "Fill" : "Contain · white"}
              </button>
            ))}
          </div>
          <label>
            Orientation
            <select
              value={draft?.orientation}
              disabled={draft?.layout !== "single"}
              onChange={(event) => {
                setDraft(
                  (current) =>
                    current && {
                      ...current,
                      orientation: event.target
                        .value as PhotoDraft["orientation"],
                    },
                );
                setExact(null);
              }}
            >
              <option value="auto">Automatic from crop</option>
              <option value="portrait">Portrait</option>
              <option value="landscape">Landscape</option>
            </select>
            {draft?.layout !== "single" && (
              <small>Repeat layouts use portrait 4×6 paper.</small>
            )}
          </label>
          <h3>Print quality</h3>
          <label className="slider">
            <span>
              Print quality
              <output>{draft?.print_quality ?? 95}</output>
            </span>
            <input
              aria-label="Print quality"
              type="range"
              min="60"
              max="95"
              step="1"
              value={draft?.print_quality ?? 95}
              onChange={(event) => {
                setDraft(
                  (current) =>
                    current && {
                      ...current,
                      print_quality: +event.target.value,
                    },
                );
                setExact(null);
              }}
            />
            <small>
              Lower this if the printer says the photo is too large.
            </small>
          </label>
          <button
            className="wide"
            disabled={!dirty}
            onClick={() =>
              void run(async () => {
                await save();
              }, "Saving changes…")
            }
          >
            Save changes
          </button>
          <button
            className="primary wide"
            onClick={() => void run(prepareExact, "Preparing print…")}
          >
            Review print →
          </button>
        </fieldset>
        {exact && (
          <div className="approval">
            <small>03 / REVIEW & PRINT</small>
            <h3>Ready to print</h3>
            {realMode && photoProfile ? (
              <>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={accepted}
                    onChange={(event) => setAccepted(event.target.checked)}
                  />
                  I checked the preview and loaded 4×6 photo paper.
                </label>
                <button
                  className="primary wide"
                  disabled={!accepted || busy || !printer?.ready}
                  onClick={() => void run(submitPhoto, "Sending to printer…")}
                >
                  Print this sheet
                </button>
                {!printer?.ready && (
                  <p className="muted">
                    {printer?.status === "attention"
                      ? "Resolve the printer message in History first."
                      : "The printer service is not ready yet."}
                  </p>
                )}
              </>
            ) : (
              <>
                <p>Connect the printer when you are ready to print.</p>
                <button className="primary wide" disabled>
                  Printer not connected
                </button>
              </>
            )}
          </div>
        )}
      </aside>
    </main>
  );
}
function App() {
  const [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState<Project | null>(null);
  const [stickers, setStickers] = useState<Record<string, Sticker>>({}),
    [jobs, setJobs] = useState<Job[]>([]);
  const [printer, setPrinter] = useState<Printer | null>(null);
  const [geometry, setGeometry] = useState<CutGeometry[]>([]),
    [render, setRender] = useState<Render | null>(null);
  const [page, setPage] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [width, setWidth] = useState(25),
    [height, setHeight] = useState(25),
    [gap, setGap] = useState(2);
  const [mode, setMode] = useState("fill"),
    [quantity, setQuantity] = useState(12),
    [rows, setRows] = useState(3),
    [columns, setColumns] = useState(3);
  const [copies, setCopies] = useState(1),
    [thickness, setThickness] = useState(2),
    [accepted, setAccepted] = useState(false);
  const [addMagnetPocket, setAddMagnetPocket] = useState(false),
    [magnetDiameter, setMagnetDiameter] = useState(6),
    [magnetDepth, setMagnetDepth] = useState(1.5);
  const [tab, setTab] = useState<"photo" | "sticker" | "history">("photo"),
    [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [queueLength, setQueueLength] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [connectionLost, setConnectionLost] = useState(false);
  const [photoDirty, setPhotoDirty] = useState(false);
  const [printerRestartConfirmed, setPrinterRestartConfirmed] = useState(false);
  const [outline, setOutline] = useState(""),
    [token, setToken] = useState(sessionStorage.getItem("pixcut-token") || "");
  const [pending, setPending] = useState<{
    key: string;
    signature: string;
  } | null>(null);
  const [showCuts, setShowCuts] = useState(true);
  const [importSummary, setImportSummary] = useState("");
  const [cutPadding, setCutPadding] = useState(0);
  const [minimumCutWidth, setMinimumCutWidth] = useState(0);
  const [fitExtractedSheet, setFitExtractedSheet] = useState(true);
  const [previewZoom, setPreviewZoom] = useState<"fit" | number>("fit");
  const [pumpkinSpice, setPumpkinSpice] = useState(savedPumpkinSpice);
  const currentSticker = project
    ? stickers[Object.keys(project.bindings)[0]]
    : undefined;
  const hasPinnedPlacements = !!project?.pages.some((sheet) =>
    sheet.placements.some((placement) => placement.pinned),
  );
  const hasSourceLayout = !!project?.pages.some(
    (sheet) =>
      sheet.source_layout ||
      (sheet.placements.length > 0 &&
        sheet.placements.every((placement) =>
          placement.id.startsWith("extracted-"),
        )),
  );
  const printerRestartJob =
    jobs.find((job) => job.id === printer?.attention_job_id) ||
    jobs.find(needsPrinterRestart);
  const printerRestartNeeded = !!printerRestartJob;
  const outlineDirty =
    !!currentSticker && outline !== formatOutline(currentSticker.outline);
  const anyUnsavedChanges = photoDirty || outlineDirty;

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    const [p, j, printers] = await Promise.all([
      api<Project[]>("/projects?kind=sticker"),
      api<JobsResponse>("/jobs"),
      api<Printer[]>("/printers"),
    ]);
    setProjects(p);
    setJobs(j.items);
    setQueueLength(j.queue_length);
    setNextCursor(j.next_cursor);
    setPrinter(printers[0] || null);
    setConnectionLost(false);
  }
  async function resolvePrinterRestart(outcome: "completed" | "failed") {
    if (!printerRestartJob) return;
    const sheet = printerRestartJob.sheets.find(
      (candidate) => candidate.state === "uncertain",
    );
    if (!sheet) return;
    await api(`/jobs/${printerRestartJob.id}/resolutions`, "POST", {
      sheet_id: sheet.id,
      outcome,
      expected_event_id: printerRestartJob.last_event_id,
      note:
        outcome === "completed"
          ? "Operator confirmed that the affected sheet printed."
          : "Operator confirmed that the affected sheet did not print.",
      printer_restarted: printerRestartConfirmed,
    });
    setPrinterRestartConfirmed(false);
    await refresh();
  }
  useEffect(() => {
    void perform(refresh);
    let stopped = false;
    let timer = 0;
    let controller: AbortController | null = null;
    let polling = false;
    let delay = 2500;
    const poll = async () => {
      if (polling || stopped) return;
      polling = true;
      controller = new AbortController();
      try {
        const [j, printers] = await Promise.all([
          api<JobsResponse>("/jobs", "GET", undefined, controller.signal),
          api<Printer[]>("/printers", "GET", undefined, controller.signal),
        ]);
        if (stopped) return;
        setJobs((current) => mergeJobs(j.items, current));
        setQueueLength(j.queue_length);
        setPrinter(printers[0] || null);
        setConnectionLost(false);
        delay = 2500;
      } catch (cause) {
        if (!stopped && (cause as Error).name !== "AbortError") {
          setConnectionLost(true);
          delay = Math.min(delay * 2, 15000);
        }
      } finally {
        polling = false;
        if (!stopped) timer = window.setTimeout(poll, delay);
      }
    };
    timer = window.setTimeout(poll, delay);
    const retryNow = () => {
      window.clearTimeout(timer);
      if (!stopped && !polling) void poll();
    };
    const whenVisible = () => {
      if (document.visibilityState === "visible") retryNow();
    };
    window.addEventListener("online", retryNow);
    document.addEventListener("visibilitychange", whenVisible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      controller?.abort();
      window.removeEventListener("online", retryNow);
      document.removeEventListener("visibilitychange", whenVisible);
    };
  }, []);
  useEffect(() => {
    const id = printer?.attention_job_id;
    if (!id || jobs.some((job) => job.id === id)) return;
    void api<Job>(`/jobs/${id}`)
      .then((job) => setJobs((current) => mergeJobs([job], current)))
      .catch(() => {});
  }, [printer?.attention_job_id, jobs]);
  useEffect(() => {
    setPrinterRestartConfirmed(false);
  }, [printerRestartJob?.id]);
  useEffect(() => {
    if (!anyUnsavedChanges) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyUnsavedChanges]);
  useEffect(() => {
    if (currentSticker) {
      setOutline(formatOutline(currentSticker.outline));
      setCutPadding(currentSticker.cut_padding_mm ?? 0);
      setMinimumCutWidth(currentSticker.minimum_cut_width_mm ?? 0);
    }
  }, [currentSticker]);

  function switchTab(next: "photo" | "sticker" | "history") {
    if (
      next !== tab &&
      ((tab === "photo" && photoDirty) ||
        (tab === "sticker" && outlineDirty)) &&
      !window.confirm("Discard your unsaved changes and continue?")
    )
      return;
    setTab(next);
  }

  async function loadMoreJobs() {
    if (!nextCursor) return;
    const result = await api<JobsResponse>(
      `/jobs?before=${encodeURIComponent(nextCursor)}`,
    );
    setJobs((current) => mergeJobs(current, result.items));
    setNextCursor(result.next_cursor);
  }

  async function load(p: Project) {
    const entries = await Promise.all(
      Object.entries(p.bindings).map(
        async ([id, revision]) =>
          [
            id,
            await api<Sticker>(`/stickers/${id}?revision=${revision}`),
          ] as const,
      ),
    );
    const g = await api<CutGeometry[]>(
      `/projects/${p.id}/geometry?revision=${p.revision}`,
    );
    setProject(p);
    setStickers(Object.fromEntries(entries));
    setGeometry(g);
    setRender(null);
    setAccepted(false);
    setPage(0);
    setSelectedJob(null);
  }
  async function savePages(pages: Project["pages"]) {
    if (!project) return;
    const next = await api<Project>(`/projects/${project.id}`, "PATCH", {
      name: project.name,
      bindings: project.bindings,
      pages,
      expected_revision: project.revision,
    });
    await load(next);
    await refresh();
  }
  async function unlockLayout() {
    if (!project || !hasPinnedPlacements) return;
    const pages = project.pages.map((sheet) => ({
      ...sheet,
      source_layout:
        sheet.source_layout ??
        sheet.placements.every((placement) =>
          placement.id.startsWith("extracted-"),
        ),
      placements: sheet.placements.map((placement) => ({
        ...placement,
        pinned: false,
      })),
    }));
    await savePages(pages);
    setImportSummary(
      "Layout unlocked. You can now rearrange the stickers on this sheet.",
    );
  }
  async function restoreSourceLayout() {
    if (!project || !hasSourceLayout) return;
    const restored = await api<Project>(
      `/projects/${project.id}/restore-source-layout`,
      "POST",
      { expected_revision: project.revision },
    );
    await load(restored);
    await refresh();
    setImportSummary(
      "Imported size restored from the original image. Check the cut lines before printing.",
    );
  }
  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    const asset = await api<{
      id: string;
      width_px: number;
      height_px: number;
    }>("/assets", "POST", form);
    const extraction = await api<ExtractionResult>(
      `/assets/${asset.id}/extractions`,
      "POST",
      { name: file.name },
    );
    const first = extraction.stickers[0];
    if (first) {
      const firstAsset = await api<{ width_px: number; height_px: number }>(
        `/assets/${first.asset_id}`,
      );
      setHeight(
        Number(((25 * firstAsset.height_px) / firstAsset.width_px).toFixed(2)),
      );
      setWidth(25);
    }
    setImportSummary(
      `Found ${extraction.component_count} design${extraction.component_count === 1 ? "" : "s"} and created separate cut lines.`,
    );
    await load(extraction.project);
    await refresh();
  }
  async function deleteProject(target: Project) {
    if (!window.confirm(`Delete project “${target.name}”?`)) return;
    await api<void>(`/projects/${target.id}`, "DELETE");
    if (project?.id === target.id) {
      setProject(null);
      setStickers({});
      setGeometry([]);
      setRender(null);
      setSelectedJob(null);
      setPage(0);
    }
    await refresh();
  }
  async function arrange() {
    if (!project || !currentSticker) return;
    const options =
      mode === "quantity"
        ? { quantity }
        : mode === "grid"
          ? { rows, columns }
          : {};
    const proposal = await api<{ pages: Project["pages"] }>(
      `/projects/${project.id}/layouts`,
      "POST",
      {
        revision: project.revision,
        sticker_id: currentSticker.id,
        mode,
        width_mm: width,
        height_mm: height,
        gap_mm: gap,
        margin_mm: 5,
        ...options,
      },
    );
    await savePages(proposal.pages);
  }
  async function saveCutPolicy() {
    if (!project) return;
    const result = await api<{ project: Project; layout_scale: number }>(
      `/projects/${project.id}/cut-policy`,
      "POST",
      {
        expected_revision: project.revision,
        cut_padding_mm: cutPadding,
        minimum_cut_width_mm: minimumCutWidth,
        fit_extracted_sheet: fitExtractedSheet,
      },
    );
    if (result.layout_scale < 1) {
      setImportSummary(
        `Applied the cut settings and reduced each extracted artwork envelope to ${Math.round(result.layout_scale * 100)}% around its existing center to prevent cut collisions.`,
      );
    }
    await load(result.project);
    await refresh();
  }
  async function prepare() {
    if (!project) return;
    const op = await api<{ render_id: string }>(
      `/projects/${project.id}/renders`,
      "POST",
      { revision: project.revision },
    );
    const r = await api<Render>(`/renders/${op.render_id}`);
    setRender(r);
    setAccepted(false);
    setPage(0);
    setGeometry(
      await Promise.all(
        r.pages.map((p) =>
          api<CutGeometry>(
            `/renders/${r.id}/artifacts/${p.index}/cut-geometry.json`,
          ),
        ),
      ),
    );
  }
  async function submit() {
    if (!render || !accepted || !printer) return;
    const livePrinter = await currentPrinter(printer);
    if (livePrinter.execution_mode === "hardware" && !livePrinter.ready)
      throw new Error("The printer is not ready. Check History for details.");
    const selected = selectedJob;
    if (livePrinter.execution_mode === "hardware" && selected) {
      throw new Error("Choose Review print again before sending this sheet.");
    }
    if (
      livePrinter.execution_mode === "hardware" &&
      !window.confirm(
        "Before printing and cutting, make sure 4×7 sticker paper is loaded. Is the correct paper in the printer?",
      )
    )
      return;
    const payload = selected
      ? {
          sheet_ids: selected.sheets.map((s) => s.id),
          copies,
          confirmed: true,
          accepted_profile_id: livePrinter.profile.id,
        }
      : {
          render_id: render.id,
          printer_id: livePrinter.id,
          mode: "print_cut",
          pages: render.pages.map((p) => p.index),
          copies,
          confirmed: true,
          accepted_profile_id: livePrinter.profile.id,
          name: project?.name || "Sheet",
        };
    const signature = JSON.stringify([selected?.id, payload]);
    const key =
      pending?.signature === signature ? pending.key : crypto.randomUUID();
    setPending({ key, signature });
    await api(selected ? `/jobs/${selected.id}/reprints` : "/jobs", "POST", {
      ...payload,
      idempotency_key: key,
    });
    setPending(null);
    setAccepted(false);
    setRender(null);
    setSelectedJob(null);
    if (project) await load(project);
    else setGeometry([]);
    setTab("history");
    await refresh();
  }
  async function inspect(job: Job) {
    const r = await api<Render>(`/renders/${job.render_id}`);
    const g = await Promise.all(
      r.pages.map((p) =>
        api<CutGeometry>(
          `/renders/${r.id}/artifacts/${p.index}/cut-geometry.json`,
        ),
      ),
    );
    setRender(r);
    setGeometry(g);
    setSelectedJob(realMode ? null : job);
    setPage(0);
    setAccepted(false);
    setTab("sticker");
  }
  const active = geometry[page];
  const usage = active ? calculateSheetUsage(active) : null;
  const realMode = printer?.execution_mode === "hardware";
  const exportFile = (format: "svg" | "dxf" | "stl") => {
    if (!active) return;
    try {
      const magnetPocket =
        format === "stl" && addMagnetPocket
          ? { diameter: magnetDiameter, depth: magnetDepth }
          : undefined;
      const data =
        format === "svg"
          ? exportSVG(active)
          : format === "dxf"
            ? exportDXF(active)
            : exportSTL(active, thickness, magnetPocket);
      download(
        data,
        `pixcut-${active.provenance.kind}-r${active.provenance.project_revision}-page${page + 1}${magnetPocket ? "-magnet" : ""}.${format}`,
        format === "svg" ? "image/svg+xml" : "application/octet-stream",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <>
      <header>
        <div className="brand">
          <span className="brand-mark">P</span>
          <div>
            <strong>PixCut Studio</strong>
            <small>
              {pumpkinSpice
                ? "Craft something cozy."
                : "Make room for your next idea."}
            </small>
          </div>
        </div>
        <nav>
          <button
            className={tab === "photo" ? "active" : ""}
            aria-current={tab === "photo" ? "page" : undefined}
            onClick={() => switchTab("photo")}
          >
            Photo
          </button>
          <button
            className={tab === "sticker" ? "active" : ""}
            aria-current={tab === "sticker" ? "page" : undefined}
            onClick={() => switchTab("sticker")}
          >
            Sticker
          </button>
          <button
            className={tab === "history" ? "active" : ""}
            aria-current={tab === "history" ? "page" : undefined}
            onClick={() => switchTab("history")}
          >
            History{" "}
            {printerRestartNeeded ? (
              <span
                className="queue-badge error-badge"
                aria-label="Printer needs attention"
                title="Printer needs attention"
              >
                !
              </span>
            ) : queueLength > 0 ? (
              <span
                className="queue-badge"
                aria-label={`${queueLength} sheets in progress`}
              >
                {queueLength}
              </span>
            ) : null}
          </button>
        </nav>
        <div className="header-actions">
          <button
            className={`theme-toggle ${pumpkinSpice ? "active" : ""}`}
            aria-pressed={pumpkinSpice}
            aria-label={`${pumpkinSpice ? "Turn off" : "Turn on"} pumpkin spice mode`}
            title={`${pumpkinSpice ? "Turn off" : "Turn on"} pumpkin spice mode`}
            onClick={() =>
              setPumpkinSpice((current) => {
                applyPumpkinSpice(!current);
                return !current;
              })
            }
          >
            <span aria-hidden="true">🎃</span>
            <span className="theme-toggle-label">Pumpkin spice</span>
          </button>
          <span className="status" role="status">
            <i className={printer?.ready ? "ready" : "not-ready"} />{" "}
            {realMode ? "Printer" : "Simulator"}
          </span>
        </div>
      </header>
      <div className="notice">
        {printer?.name || "PixCut S1 USB"} ·{" "}
        {printer?.found ? "Found" : "Not found"}
      </div>
      {connectionLost && (
        <div className="connection-status" role="status" aria-live="polite">
          Connection lost — trying again.
        </div>
      )}
      {error && (
        <div role="alert" className="error">
          {error}
          <button onClick={() => setError("")}>Dismiss</button>
        </div>
      )}
      {tab === "history" ? (
        <main className="history">
          <div className="section-title">
            <div>
              <small>RECENT PRINTS</small>
              <h1>Print history</h1>
            </div>
            <button onClick={() => void perform(refresh)}>Refresh</button>
          </div>
          <p className="muted">
            Check progress, reopen a sheet, or print it again.
          </p>
          {printerRestartNeeded && (
            <div className="printer-recovery" role="alert">
              <strong>Restart the printer</strong>
              <span>
                Hold the power button until the light turns off, then hold it
                again until the light turns on. Check the affected sheet first;
                it will not retry automatically.
              </span>
              <span>What happened to that sheet?</span>
              <label className="check">
                <input
                  type="checkbox"
                  checked={printerRestartConfirmed}
                  onChange={(event) =>
                    setPrinterRestartConfirmed(event.target.checked)
                  }
                />{" "}
                I restarted the printer and its light is back on.
              </label>
              <div
                className="printer-recovery-actions"
                role="group"
                aria-label="Record the affected sheet result"
              >
                <button
                  className="primary"
                  disabled={busy || !printerRestartConfirmed}
                  onClick={() =>
                    void perform(() => resolvePrinterRestart("completed"))
                  }
                >
                  It printed
                </button>
                <button
                  disabled={busy || !printerRestartConfirmed}
                  onClick={() =>
                    void perform(() => resolvePrinterRestart("failed"))
                  }
                >
                  Nothing printed
                </button>
              </div>
            </div>
          )}
          {jobs.length === 0 && (
            <div className="empty">
              Your first project starts in Photo or Sticker.
            </div>
          )}
          {jobs.map((j) => (
            <article className="job" key={j.id}>
              <AssetImage
                path={`/renders/${j.render_id}/artifacts/${j.sheets[0]?.page || 0}/thumbnail.png`}
                alt="Saved sheet thumbnail"
              />
              <div>
                <span className="eyebrow">
                  {j.execution_mode === "hardware" ? "Printer" : "Preview"}{" "}
                  {j.reprint_of_job_id ? "· Printed again" : ""}
                </span>
                <h2>{j.name}</h2>
                <p>
                  {new Date(j.created_at).toLocaleString()} · {j.sheets.length}{" "}
                  {j.sheets.length === 1 ? "sheet" : "sheets"}
                </p>
                <p>
                  {j.counts.completed} of {j.sheets.length}{" "}
                  {j.sheets.length === 1 ? "sheet" : "sheets"} finished
                  {j.counts.failed > 0
                    ? ` · ${j.counts.failed} need attention`
                    : ""}
                </p>
                <details>
                  <summary>Print details</summary>
                  {j.sheets.map((s) => (
                    <p key={s.id}>
                      Sheet {s.ordinal + 1} · page {s.page + 1} ·{" "}
                      {jobStateLabel(s.state)}
                    </p>
                  ))}
                </details>
              </div>
              <div className="job-actions">
                <span className="pill">{jobStateLabel(j.state)}</span>
                {realMode &&
                  j.execution_mode === "hardware" &&
                  j.state === "held" && (
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => {
                        const media =
                          j.request.mode === "print_only"
                            ? "4×6 photo paper"
                            : "4×7 sticker paper";
                        if (
                          !window.confirm(
                            `Resume “${j.name}”? Confirm the printer is on and ${media} is loaded. Printing begins immediately.`,
                          )
                        )
                          return;
                        void perform(async () => {
                          await api(`/jobs/${j.id}/resume`, "POST");
                          await refresh();
                        });
                      }}
                    >
                      Resume print
                    </button>
                  )}
                {j.request.mode !== "print_only" &&
                  (!realMode || j.execution_mode === "simulator") && (
                    <button onClick={() => void perform(() => inspect(j))}>
                      Open print
                    </button>
                  )}
                {j.counts.queued > 0 && (
                  <button
                    onClick={() =>
                      void perform(async () => {
                        await api(`/jobs/${j.id}/cancel`, "POST");
                        await refresh();
                      })
                    }
                  >
                    Cancel print
                  </button>
                )}
              </div>
            </article>
          ))}
          {nextCursor && (
            <button
              className="load-more"
              disabled={busy}
              onClick={() => void perform(loadMoreJobs)}
            >
              Load older prints
            </button>
          )}
        </main>
      ) : tab === "photo" ? (
        <PhotoWorkspace
          printer={printer}
          onDirtyChange={setPhotoDirty}
          onQueued={async () => {
            setPhotoDirty(false);
            setTab("history");
            await refresh();
          }}
        />
      ) : (
        <main className="workspace">
          <aside className="library">
            <small>01 / ARTWORK</small>
            <h2>Your artwork</h2>
            <label className="upload">
              <span>＋</span>
              <strong>Import an image</strong>
              <small>PNG, JPEG or WebP · up to 25 MiB</small>
              <input
                aria-label="Import an image"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                disabled={busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (
                    file &&
                    (!outlineDirty ||
                      window.confirm(
                        "Discard your unsaved cut-path changes and import new artwork?",
                      ))
                  )
                    void perform(() => upload(file));
                  e.target.value = "";
                }}
              />
            </label>
            {importSummary && <p className="import-result">{importSummary}</p>}
            <h3>Saved projects</h3>
            {projects.map((p) => (
              <div
                className={`project-row ${p.id === project?.id ? "selected" : ""}`}
                key={p.id}
              >
                <button
                  className="project"
                  aria-current={p.id === project?.id ? "true" : undefined}
                  onClick={() => {
                    if (
                      !outlineDirty ||
                      window.confirm(
                        "Discard your unsaved cut-path changes and open this project?",
                      )
                    )
                      void perform(() => load(p));
                  }}
                >
                  <span>▧</span>
                  <div>
                    {p.name}
                    <small>
                      Saved · {p.pages.length}{" "}
                      {p.pages.length === 1 ? "sheet" : "sheets"}
                    </small>
                  </div>
                </button>
                <button
                  className="delete-project"
                  aria-label={`Delete ${p.name}`}
                  title="Delete project"
                  disabled={busy}
                  onClick={() => void perform(() => deleteProject(p))}
                >
                  <TrashIcon />
                </button>
              </div>
            ))}
            <div className="library-note">
              Changes to a design update every copy of that sticker on the
              sheet.
            </div>
            <details>
              <summary>Connection settings</summary>
              <input
                aria-label="Access code"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
              <button
                onClick={() => {
                  sessionStorage.setItem("pixcut-token", token);
                  void perform(refresh);
                }}
              >
                Save access code
              </button>
            </details>
          </aside>
          <section className="canvas-area">
            <div className="canvas-title">
              <div>
                <small>
                  {selectedJob
                    ? "SAVED PRINT"
                    : render
                      ? "PRINT PREVIEW"
                      : "STICKER PROJECT"}
                </small>
                <h1>{selectedJob?.name || project?.name || "A fresh sheet"}</h1>
              </div>
              <span className="pill">
                {render
                  ? "Ready to print"
                  : project
                    ? "Saved"
                    : "Ready when you are"}
              </span>
            </div>
            <div className="canvas-toolbar">
              <span>4 × 7 in sheet</span>
              <label>
                <input
                  type="checkbox"
                  checked={showCuts}
                  onChange={(e) => setShowCuts(e.target.checked)}
                />{" "}
                Cut lines
              </label>
              <div className="zoom-control">
                <button
                  className={previewZoom === "fit" ? "active" : ""}
                  aria-pressed={previewZoom === "fit"}
                  onClick={() => setPreviewZoom("fit")}
                >
                  Fit
                </button>
                <input
                  aria-label="Preview zoom"
                  type="range"
                  min="50"
                  max="300"
                  step="10"
                  value={previewZoom === "fit" ? 100 : previewZoom}
                  onChange={(e) => setPreviewZoom(+e.target.value)}
                />
                <output>
                  {previewZoom === "fit" ? "Fit" : `${previewZoom}%`}
                </output>
              </div>
            </div>
            {usage && (
              <section className="sheet-usage" aria-label="Sheet material use">
                <div className="usage-bar" aria-hidden="true">
                  <span style={{ width: `${usage.usedPercent}%` }} />
                </div>
                <div className="usage-details">
                  <div>
                    <strong>{usage.usedPercent}%</strong>
                    <span>Sticker area</span>
                    <small>
                      {usage.usedSquareInches.toFixed(1)} in² · about $
                      {(usage.usedCostCents / 100).toFixed(2)}
                    </small>
                  </div>
                  <div>
                    <strong>{usage.unusedPercent}%</strong>
                    <span>Unused paper</span>
                    <small>
                      {usage.unusedSquareInches.toFixed(1)} in² · about $
                      {(usage.unusedCostCents / 100).toFixed(2)}
                    </small>
                  </div>
                </div>
                <p>Estimated from the cut areas at $0.60 per sheet.</p>
              </section>
            )}
            <div
              className={`sheet-surround ${previewZoom === "fit" ? "" : "zoomed"}`}
            >
              <div
                className="sheet"
                style={
                  previewZoom === "fit"
                    ? undefined
                    : {
                        width: `${(330 * previewZoom) / 100}px`,
                        maxWidth: "none",
                      }
                }
              >
                {render ? (
                  <>
                    <AssetImage
                      path={`/renders/${render.id}/artifacts/${page}/image.png`}
                      alt="Sticker sheet print preview"
                    />
                    {showCuts && (
                      <AssetImage
                        className="cut-overlay"
                        path={`/renders/${render.id}/artifacts/${page}/overlay.svg`}
                        alt="Sticker cut lines"
                      />
                    )}
                  </>
                ) : project?.pages[page] ? (
                  <svg
                    viewBox="0 0 101.6 177.8"
                    aria-label="Draft sticker sheet"
                  >
                    <rect
                      x="2"
                      y="2"
                      width="97.6"
                      height="173.8"
                      fill="none"
                      stroke="#b7cac0"
                      strokeDasharray="1 1"
                      strokeWidth="0.2"
                    />
                    {project.pages[page].placements.map((p) => (
                      <foreignObject
                        key={p.id}
                        x={p.x_mm}
                        y={p.y_mm}
                        width={p.rotation ? p.height_mm : p.width_mm}
                        height={p.rotation ? p.width_mm : p.height_mm}
                      >
                        <AssetImage
                          path={`/assets/${stickers[p.sticker_id]?.asset_id}/normalized`}
                          alt="Sticker artwork"
                          style={{ width: "100%", height: "100%" }}
                        />
                      </foreignObject>
                    ))}
                    {showCuts &&
                      active?.shapes.map((s) => (
                        <polygon
                          key={s.id}
                          points={s.outer.points
                            .map((p) => p.join(","))
                            .join(" ")}
                          fill="none"
                          stroke="#da3a77"
                          strokeWidth="0.2"
                        />
                      ))}
                  </svg>
                ) : (
                  <div className="blank-sheet">
                    <span>✳</span>
                    <h2>
                      Something good
                      <br />
                      starts here.
                    </h2>
                    <p>
                      Import your artwork, choose its size,
                      <br />
                      and give it a place on the sheet.
                    </p>
                  </div>
                )}
              </div>
            </div>
            <div className="page-nav">
              <button
                aria-label="Previous sheet"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                ←
              </button>
              <span>
                Page {geometry.length ? page + 1 : 0} of {geometry.length} ·{" "}
                {active?.shapes.length || 0} stickers
              </span>
              <button
                aria-label="Next sheet"
                disabled={page >= geometry.length - 1}
                onClick={() => setPage(page + 1)}
              >
                →
              </button>
            </div>
            <p className="caption">
              {render
                ? realMode
                  ? "This is how the printed artwork and cut lines will line up."
                  : "This is how the saved artwork and cut lines will line up."
                : "Arrange your stickers and check each cut line before printing."}
            </p>
          </section>
          <aside className="inspector">
            <small>02 / ARRANGE</small>
            <h2>Build your sheet</h2>
            <fieldset disabled={busy || !project || !!selectedJob}>
              {(hasPinnedPlacements || hasSourceLayout) && (
                <div className="layout-lock">
                  <div>
                    <strong>
                      {hasPinnedPlacements
                        ? "Layout locked"
                        : "Imported layout"}
                    </strong>
                    <span>
                      {hasPinnedPlacements
                        ? "These stickers are keeping their imported positions."
                        : "Restore the original size or choose a new arrangement below."}
                    </span>
                  </div>
                  <div className="layout-lock-actions">
                    {hasSourceLayout && (
                      <button onClick={() => void perform(restoreSourceLayout)}>
                        Restore imported size
                      </button>
                    )}
                    {hasPinnedPlacements && (
                      <button onClick={() => void perform(unlockLayout)}>
                        Unlock layout
                      </button>
                    )}
                  </div>
                </div>
              )}
              <label>
                Arrangement
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  <option value="fill">Fill sheet</option>
                  <option value="grid">Fixed grid</option>
                  <option value="quantity">Choose quantity</option>
                </select>
              </label>
              <div className="pair">
                <label>
                  Width · mm
                  <input
                    type="number"
                    min="1"
                    step="0.1"
                    value={width}
                    onChange={(e) => {
                      const next = +e.target.value;
                      if (width > 0)
                        setHeight(Number(((height * next) / width).toFixed(2)));
                      setWidth(next);
                    }}
                  />
                </label>
                <label>
                  Height · mm
                  <input
                    type="number"
                    min="1"
                    step="0.1"
                    value={height}
                    onChange={(e) => {
                      const next = +e.target.value;
                      if (height > 0)
                        setWidth(Number(((width * next) / height).toFixed(2)));
                      setHeight(next);
                    }}
                  />
                </label>
              </div>
              <small>Sticker size · proportions stay locked</small>
              <label>
                Space between stickers · mm
                <input
                  type="number"
                  min="0"
                  step="0.5"
                  value={gap}
                  onChange={(e) => setGap(+e.target.value)}
                />
              </label>
              <div className="pair">
                <label>
                  White border · mm
                  <input
                    type="number"
                    min="0"
                    max="20"
                    step="0.25"
                    value={cutPadding}
                    onChange={(e) => setCutPadding(+e.target.value)}
                  />
                </label>
                <label>
                  Smooth tight corners · mm
                  <input
                    type="number"
                    min="0"
                    max="20"
                    step="0.25"
                    value={minimumCutWidth}
                    onChange={(e) => setMinimumCutWidth(+e.target.value)}
                  />
                </label>
              </div>
              <small>
                White border moves the cut away from your artwork. Smoothing
                helps the blade move cleanly through tiny corners and narrow
                details.
              </small>
              <label className="check">
                <input
                  type="checkbox"
                  checked={fitExtractedSheet}
                  onChange={(e) => setFitExtractedSheet(e.target.checked)}
                />{" "}
                Shrink stickers slightly if their white borders would overlap.
              </label>
              <button
                className="wide"
                onClick={() => void perform(saveCutPolicy)}
              >
                Apply border settings
              </button>
              {mode === "quantity" && (
                <label>
                  Sticker quantity
                  <input
                    type="number"
                    min="1"
                    value={quantity}
                    onChange={(e) => setQuantity(+e.target.value)}
                  />
                </label>
              )}
              {mode === "grid" && (
                <div className="pair">
                  <label>
                    Rows
                    <input
                      type="number"
                      min="1"
                      value={rows}
                      onChange={(e) => setRows(+e.target.value)}
                    />
                  </label>
                  <label>
                    Columns
                    <input
                      type="number"
                      min="1"
                      value={columns}
                      onChange={(e) => setColumns(+e.target.value)}
                    />
                  </label>
                </div>
              )}
              <button className="wide" onClick={() => void perform(arrange)}>
                Arrange at this size
              </button>
              <details>
                <summary>Advanced cut path</summary>
                <p className="muted">
                  Edit one x, y point per line. This changes every copy of the
                  selected design. Coordinates are shown to three decimal
                  places.
                </p>
                <textarea
                  aria-label="Advanced cut path"
                  rows={6}
                  value={outline}
                  onChange={(e) => setOutline(e.target.value)}
                />
                <button
                  disabled={!outlineDirty}
                  onClick={() =>
                    void perform(async () => {
                      if (!project || !currentSticker) return;
                      const points = outline
                        .trim()
                        .split("\n")
                        .map((line) => line.split(",").map(Number) as Point);
                      const s = await api<Sticker>(
                        `/stickers/${currentSticker.id}`,
                        "PATCH",
                        {
                          name: currentSticker.name,
                          asset_id: currentSticker.asset_id,
                          outline: points,
                          cut_padding_mm: currentSticker.cut_padding_mm ?? 0,
                          minimum_cut_width_mm:
                            currentSticker.minimum_cut_width_mm ?? 0,
                          expected_revision: currentSticker.revision,
                        },
                      );
                      const p = await api<Project>(
                        `/projects/${project.id}`,
                        "PATCH",
                        {
                          name: project.name,
                          pages: project.pages,
                          bindings: { ...project.bindings, [s.id]: s.revision },
                          expected_revision: project.revision,
                        },
                      );
                      await load(p);
                      await refresh();
                    })
                  }
                >
                  Save cut path
                </button>
              </details>
              <button
                className="primary wide"
                disabled={!project?.pages.length}
                onClick={() => void perform(prepare)}
              >
                Review print →
              </button>
            </fieldset>
            {render && (
              <div className="approval">
                <small>03 / REVIEW & PRINT</small>
                <h3>
                  {selectedJob
                    ? "Saved print"
                    : realMode
                      ? "Ready to print"
                      : "Ready to preview"}
                </h3>
                <p>
                  {render.pages.length}{" "}
                  {render.pages.length === 1 ? "sheet" : "sheets"} ·{" "}
                  {active?.shapes.length || 0} stickers on this sheet
                </p>
                <label>
                  Copies
                  <input
                    type="number"
                    min="1"
                    max={realMode ? 1 : 100}
                    value={copies}
                    disabled={realMode}
                    onChange={(e) => setCopies(+e.target.value)}
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={accepted}
                    onChange={(e) => setAccepted(e.target.checked)}
                  />{" "}
                  {realMode
                    ? "I checked the preview and loaded 4×7 sticker paper."
                    : "I checked every sheet."}
                </label>
                <button
                  className="primary wide"
                  disabled={
                    busy ||
                    !accepted ||
                    (realMode && (render.pages.length !== 1 || !printer?.ready))
                  }
                  onClick={() => void perform(submit)}
                >
                  {selectedJob
                    ? "Print again"
                    : realMode
                      ? "Print & cut one sheet"
                      : "Save preview to history"}
                </button>
                {realMode && !printer?.ready && (
                  <p className="muted">
                    {printer?.status === "attention"
                      ? "Resolve the printer message in History first."
                      : "The printer service is not ready yet."}
                  </p>
                )}
              </div>
            )}
            {active && (
              <div className="exports">
                <h3>Download this sheet</h3>
                <p className="muted">
                  Export {active.shapes.length} cut shapes with their sheet
                  positions preserved. Use millimeters when importing DXF or STL
                  files.
                </p>
                <label>
                  Solid thickness · mm
                  <input
                    type="number"
                    value={thickness}
                    min="0.1"
                    max="100"
                    step="0.1"
                    onChange={(e) => setThickness(+e.target.value)}
                  />
                </label>
                <div className="magnet-options">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={addMagnetPocket}
                      onChange={(e) => setAddMagnetPocket(e.target.checked)}
                    />
                    Add a centered magnet pocket to the back of each STL part
                  </label>
                  {addMagnetPocket && (
                    <>
                      <div className="pair">
                        <label>
                          Magnet diameter · mm
                          <input
                            type="number"
                            value={magnetDiameter}
                            min="0.1"
                            max="100"
                            step="0.1"
                            onChange={(e) => setMagnetDiameter(+e.target.value)}
                          />
                        </label>
                        <label>
                          Pocket depth · mm
                          <input
                            type="number"
                            value={magnetDepth}
                            min="0.1"
                            max="100"
                            step="0.1"
                            onChange={(e) => setMagnetDepth(+e.target.value)}
                          />
                        </label>
                      </div>
                      <small>
                        {magnetDepth >= thickness
                          ? "The pocket reaches through the part."
                          : `Leaves ${(thickness - magnetDepth).toFixed(1)} mm of material between the magnet and sticker.`}
                      </small>
                    </>
                  )}
                </div>
                <div className="export-buttons">
                  {(["svg", "dxf", "stl"] as const).map((f) => (
                    <button key={f} onClick={() => exportFile(f)}>
                      {f.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </aside>
        </main>
      )}
      <footer>
        <span>Your projects stay on this device.</span>
        <span>{busy ? "Working…" : "PixCut Studio · ready"}</span>
      </footer>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
