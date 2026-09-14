# PixCut Studio design

This document defines the intended product behavior, architecture, data contracts,
and safety boundaries for PixCut Studio. [IMPLEMENTATION.md](IMPLEMENTATION.md)
tracks which parts are currently implemented; the [README](README.md) is the
operator-facing guide.

Hardware behavior is based on observed target-printer results and independent
protocol research, not an official protocol specification. Production profiles
must remain bound to recorded physical evidence. Current 4×6 and 4×7 profiles use
the printer's fixed 300 DPI raster and retain the exact reviewed JPEG bytes sent to
the device. Original uploads and editable working assets remain byte-preserved or
lossless.

## 1. Product scope

Build a local-first, Linux-hosted service for the Liene PixCut S1 with a browser editor and an automation-friendly API. The application must handle single-sticker uploads, repeat/tile sheet creation, extraction of multiple stickers from a complete image, individual cutline editing, client-side SVG/DXF/STL cut-geometry export, and queued combined print-and-cut jobs.

The central design rule is to keep four things independent: original artwork, foreground masks, editable cut geometry, and placement on a sheet. A background used to find outlines must not automatically disappear from the printed artwork. Repeated stickers remain separately positioned references to one shared sticker definition rather than being flattened and traced again.

The printer connection is USB only. The first release targets normal sticker media and ordinary kiss-cut jobs. A browser on another computer or phone talks to the web service over the network; only the Linux printer worker needs physical device access. Bluetooth, BLE, Web Bluetooth, pairing, and transport failover are out of scope, not deferred features. Do not make a particular browser's device APIs a prerequisite.

Source preservation and lossless editable working assets remain production requirements. Device output is separately profile-bound and evidence-driven: current 4×6 and 4×7 physical jobs use reviewed, size-bounded JPEG artifacts at the printer's fixed 300 DPI raster because PNG job creation was rejected on the tested DHP700. The service never silently changes formats or recompresses an approved job during dispatch; it stores and sends the exact reviewed bytes. See sections 11.5 and 11.5.1 for the test record.

## 2. Integration of the three upstream projects

| Project | Existing capability relevant to this design | Proposed responsibility |
|---|---|---|
| `popcornhax/PixCut-App` | Python USB control, image-to-cut processing, sheet packing, and a kiosk interface. [S01] [S02] | Primary printer adapter and reference Python rendering/packing implementation. Replace the kiosk's application model with the unified project and job model below. |
| `eastbaymakersclub/pixcut-s1` | Python USB client and detailed observed printer behavior. [S06] [S07] [S09] | USB protocol comparison, recovery behavior, and calibration references. Do not incorporate its Bluetooth clients or proprietary macOS cut-generator dependency. |
| `honeymaro/pixcut` | TypeScript contour stages, PLT parsing, statistics, and SVG previews. [S10] [S11] [S12] [S13] | A bounded geometry helper for tracing/inspection and an independent check of emitted cut data. Do not inherit its printer settings implicitly. |

PixCut-App and Honeymaro have explicit MIT license files. No license file was visible in the East Bay repository root during this review. Treat reuse of that project's code as a permission/licensing gate rather than silently incorporating it. The USB-only application must remain buildable without that code. [S16] [S17] [S06]

Do not merge three command-line programs into one shell script. Define adapters with structured inputs, outputs, progress, exceptions, and capability reports. Human-readable logs are diagnostic output, not a success signal. Pin versions, retain attribution, and record the selected adapters in every rendered job.

## 3. User workflows

### 3.1 Single image → repeated sticker sheet

PNG is the preferred upload format. Also accept JPEG or WebP for convenience without introducing further lossy encoding; existing losses in an uploaded image cannot be reversed by saving it as PNG. The browser uploads original file bytes without resizing or converting them. The user chooses a physical size, prepares one outline, and repeats that sticker. Preserve the original and generate lossless working rasters with recorded orientation/color normalization. Initial SVG support should be limited to validated vector cut geometry, with broader vector artwork import deferred.

Expose three explicit arrangement modes:

| Mode | User controls | Required behavior |
|---|---|---|
| Fixed grid | Rows, columns, size, horizontal/vertical gap | Place in predictable cells; reject impossible dimensions or offer an explicit resize. |
| Fill sheet | Sticker size, gap, permitted rotation | Keep the specified size and place as many complete instances as fit. Report the achieved count, not an unproven global optimum. |
| Exact quantity | Number of stickers, size, gap | Create the requested number and paginate overflow. The final sheet may be partly empty. |

Allow aligned or staggered rows, sheet centering, edge margins, and optional 90-degree packing rotations. More general rotations can remain manual initially. A separate “scale to fit this grid” action may resize artwork, but the normal fill operation must never silently shrink it.

Size should default to the final outer sticker width/height, with the art dimensions shown separately. Keep aspect ratio locked unless deliberately changed. Display effective source resolution at that physical size.

Spacing is measured between the final cut envelopes, not the opaque image bounds. Include physical borders, any enabled lead-in/lead-out allowance, and artwork bleed when checking interference. Use conservative bounding boxes initially; irregular nesting is a later enhancement.

Repeated placements always reference the same sticker definition. Editing its artwork, mask, or outline updates all its placements in the editable project. There is no detach, make-independent action, or per-placement artwork/mask/cut-policy override. Position, rotation, physical size, and pinning remain layout properties; they do not create a new sticker definition. A manual placement becomes pinned so the next automatic layout does not unexpectedly move it.

Each project revision binds a sticker ID to exactly one sticker-definition revision, shared by all its placements. An edit creates a new project revision and advances that binding for every copy together. Approved renders and prior project revisions remain immutable. Distinct designs extracted from a full sheet are separate sticker definitions; this does not require a detach mechanism.

Use two different controls: **stickers per sheet** and **copies of the complete sheet**. The first changes layout; the second changes production quantity. For the first release, expand sheet copies into separately tracked one-sheet device jobs rather than depending on undocumented multi-copy cutting behavior.

### 3.2 Uploaded sheet → extracted stickers

Treat a full sheet as an image containing candidate designs, not as one indivisible sticker. Show this sequence:

`Upload → crop/orient → identify background → inspect mask → identify sticker regions → split/group → edit outlines → preserve layout or repack → review → queue`

Require a physical sheet size or an explicit fit-to-target choice. Never infer physical dimensions solely from unreliable image metadata. Offer “contain without distortion,” “crop,” and explicit resizing; do not stretch a nonmatching aspect ratio invisibly.

The review screen shows numbered candidate regions, the original image, the foreground mask, and proposed cutlines. All extraction operations are nondestructive and undoable.

After extraction, offer three outcomes:

**Preserve the original arrangement.** Keep the uploaded image as a locked print layer and place the individual cutlines over it. Optional background removal changes the printable layer only when selected. Source-to-sheet transforms apply equally to art and outlines.

**Repack onto new sheets.** Convert reviewed regions into reusable sticker definitions with transparent artwork crops, independent masks, source offsets, and cut policies. Arrange selected quantities at explicit physical sizes.

**Save designs to the library.** Save any extracted sticker independently and use it later in the single-image repeat workflow. A user can extract an assorted sheet, choose one design, and immediately fill another sheet with that design.

“Upload a sheet” means importing digital artwork for a new print-and-cut job. It does not imply support for loading an already-printed sheet and performing a separate aligned cut operation.

### 3.3 Manual outline creation and correction

Support polygon/lasso creation, rectangle/ellipse/rounded-rectangle presets, node insertion/deletion/dragging, curve smoothing, simplification preview, and undo/redo. Start with manageable polygons; full Bézier editing can follow later.

Users can split two touching designs, group disconnected pieces belonging to one sticker, discard false regions, restore excluded art, and choose whether interior holes should be cut. Grouping must distinguish “keep these objects together” from “create one continuous outer sticker boundary.” Multiple disconnected regions do not automatically become one physically connected sticker.

Keep interior cuts off by default. White eyes, letters, and decorative voids should not become unintended cutouts. Do not silently select only the largest connected region; small dots, accents, stars, and detached details require review.

## 4. Background extraction and masking

### 4.1 Built-in deterministic methods

Provide existing-alpha extraction, sampled-background extraction, a mask-only color-tolerance mode, and user-painted keep/remove masks. A proposed default for flat sheets is background flood-fill from the image border or user-selected background seeds, bounded by color tolerance. OpenCV supplies mask-only flood-fill and user-seeded foreground/background segmentation primitives suitable for these operations. [S14]

This default avoids indiscriminately making every white pixel transparent, but it cannot resolve an unoutlined white subject against identical white paper. Preserve brushes and manual contours remain necessary. Optional global color removal must warn that it can remove subject colors too.

After background identification, label foreground components and expose optional small-speck filtering and gap-bridging. Show the effects before acceptance. Treat area thresholds as review aids, not proof that a region is unimportant. Preserve component ancestry, including holes and detached fragments.

### 4.2 Optional local model-assisted extraction

Add a replaceable local background-removal adapter for textured backgrounds and photographs. `rembg` supplies a Python interface and CPU/GPU options; it is a candidate, not a requirement for the basic application. Its repository also supports pre-downloading models. [S15]

Prefer extraction within a user-selected sticker region when processing an assorted sheet. A foreground/background mask is not a reliable list of distinct sticker identities. Keep model output reviewable and record model name, version, settings, and mask hash. Validate the selected model weights' license separately from the wrapper's license.

The base system must still upload, edit, tile, render, and print without a GPU, network access, or a model download. Package optional models separately and make missing-model behavior explicit.

### 4.3 Three separate editable layers

**Print layer:** the visible image that will be composited onto the sheet.

**Extraction mask:** a grayscale/alpha representation used to identify foreground and candidate regions.

**Cut layer:** vector paths or an outline policy describing where the blade should travel.

Removing the background for tracing must not force a print-layer change. Likewise, drawing a larger cut outline must not erase art outside that outline without an explicit crop action. Keep source pixels intact throughout editing.

## 5. Border, bleed, and geometry policy

Expose different parameters for:

| Parameter | Meaning |
|---|---|
| Cut offset / sticker border | Distance between the selected artwork boundary and the cutline. |
| Border appearance | White, another color, or no added print fill in the region around artwork. |
| Bleed | Artwork extended beyond the cutline to tolerate alignment error. |
| Inter-sticker gap | Minimum clear separation between finished sticker boundaries. |
| Sheet guard | Region reserved near feed and edge limits. |
| Mechanical compensation | Profile-controlled blade entry/exit behavior; not an artistic border. |

Offsets and simplification tolerances must be expressed in physical millimeters after sizing. A placement resized from 20 mm to 40 mm should not inadvertently double the shared definition's requested 1 mm border. Recompute derived paths when relevant dimensions change.

Preserve the finished design contour after physical sizing, border offsets, and accepted outline editing, but before device calibration, plotter-unit conversion, feed-axis reversal, or blade entry/exit moves. CAD exports consume this clean contour, not a reconstruction of the machine toolpath. The exact machine PLT remains a separately retained diagnostic/production artifact.

The application owns one canonical geometry pipeline. It must not ask two upstream whole-image generators to each fit, dilate, overscan, and position the same sticker. Use Honeymaro's individual tracing/inspection stages, normalize their output, and keep final serialization behind one selected, calibrated adapter. Its supplied transform has an overscan default, so it must not run invisibly after the application has already placed the image. [S10] [S13]

## 6. Browser application

Use a responsive workspace with an asset/region library, a central sheet canvas, a selected-item inspector, and a job drawer. Provide:

- Import modes for one image, several images, and a complete sheet.
- Separate image, mask, cutline, bleed, safe-region, and numbering overlays.
- Repeat/fill/grid controls, sheet pagination, physical dimensions, and pinned placements.
- Region split/group, keep/remove brushes, shared sticker-definition editing, contour editing, and undo/redo; no per-copy content variants.
- A preflight panel and explicit final print-and-cut confirmation.
- Printer connection, media profile, queue, current phase, warnings, and SQLite-backed history with per-sheet outcomes, exact previews, and explicit reprint controls.
- An Export outline action for SVG/DXF and Export solid for STL, generated locally in the browser from the selected resolved cut geometry.

A screen rendering marked “actual size” is only trustworthy after display calibration. Show dimension labels regardless. Provide a fit-to-window preview and zoom rather than treating browser CSS pixels as physical millimeters.

Generate final previews on the server from the exact render snapshot. The browser's responsive editing preview is not itself the printer payload. Serve the authoritative raster and mask previews as PNG; reduced-resolution navigation previews must be labeled and cannot become print inputs. The approval panel identifies the actual device payload format and whether its USB/media profile has been validated.

### 6.1 Client-side CAD and 3D-printing exports

**Boundary.** The browser serializes or extrudes the resolved vector contours it already uses for editing/inspection. Do not add a server conversion service, CAD executable, export queue, export database table, or printer operation. Loading a saved project or historical geometry uses the existing read-only API; that is not server-side export generation. After the application modules and selected geometry are loaded, conversion must work without a network round trip. This does not imply that initial uploads, background extraction, or the entire application become offline features.

**Workflow.** `Select a sticker, several placements, or the active sheet → Export → choose format → review dimensions and, for STL, thickness → save locally`. Default to the selected sticker/placement when there is one; otherwise require a clear scope choice. Show the source revision, final width/height, units, contour count, and enabled hole count. A current-draft export is labeled as a draft, not as the exact geometry of a previous print.

**Scopes.** Single-sticker export uses the selected placement's physical size, or the definition's explicitly selected physical size when exporting from the library. A selection or active-sheet export preserves the selected placements and their relative positions. Do not silently remove repeated copies or multiply them again. Library/single-item export can use the unrotated local outline; selection/sheet export uses the actual placement transforms. Identify which convention is selected in the preview. V1 exports one page per file; cross-page packing, CAD assemblies, and automatic multi-file archives are not required.

#### Geometry source and coordinate conventions

Export the **finished cut boundary**, including the deliberate sticker border. Keep only user-enabled interior cuts, not white pixels or disabled tracing holes. Exclude artwork rasters, crop boxes, blade-up travel, lead-in/lead-out segments, pressure commands, printer compensation, overscan, registration graphics, and labels unless they are actual approved sticker geometry. Do not retrace a PNG, a thumbnail, or the displayed SVG to obtain the contour. Do not read dimensions back from CSS pixels.

Use the canonical `cut-geometry` snapshot in section 8.1. The same physical geometry must feed both the export serializer and the printer adapter's downstream mapping. Exporting creates neither a detached sticker definition nor a content override for one repeated placement.

For a tightly cropped single object or selection with bounds `(xmin, ymin, xmax, ymax)` in the editor's top-left/Y-down frame, SVG coordinates are `(x-xmin, y-ymin)` and CAD/STL XY coordinates are `(x-xmin, ymax-y)`. For a full sheet of height `H`, preserve page coordinates in SVG; use `(x, H-y)` for DXF/STL. STL extends in positive Z from zero to the chosen thickness. Apply the Y conversion exactly once and adapt ring winding after conversion. Verify the result using asymmetric outlines; symmetric circles alone will not reveal a mirror error.

Pure vector/solid export does not require a validated PNG printer profile, a connected printer, or printer commissioning. Machine-safe-area violations can remain visible as warnings but do not justify clipping otherwise valid CAD geometry. Missing or stale resolved contours must be resolved through the ordinary editor geometry path before export; the export button must not silently send an outdated contour or implement a second background-removal/offset pipeline.

#### Format contracts

| Format | Required output | Initial settings |
|---|---|---|
| SVG | Plain vector document with closed paths, explicit physical width/height in millimeters and matching numeric viewBox; separate groups for sticker placements. | Outline-only, no image or font dependencies. Use the export scope's bounds or full-sheet extent. |
| DXF | ASCII AutoCAD 2000 profile (`AC1015`) with closed, zero-width 2D `LWPOLYLINE` entities in model space at Z=0. | Numeric coordinates in millimeters, `$INSUNITS=4`, `$MEASUREMENT=1`; deterministic layers identify each sticker and outer/hole rings. |
| STL | Binary triangulated surface of filled sticker silhouettes extruded into closed solids, preserving enabled holes. | Proposed default thickness 2 mm, editable positive value; no bevel, no baseplate, no automatic joining of separate stickers. |

SVG supports physical length units and viewport/viewBox mappings. Set both deliberately rather than relying on a viewer's implicit pixel size. [S22] Serialize explicit paths and coordinates rather than repeated `<use>` instances or CSS-dependent transforms. A visible stroke is only a display aid: do not export its stroke-expanded edges as a second pair of cut boundaries. Enabled holes are closed subpaths with preserved containment; default export remains outline-only.

Autodesk documents the `LWPOLYLINE` closed flag as group code 70 bit 1, and millimeters as `$INSUNITS` value 4. [S23] [S24] Write the required DXF sections/markers consistently with the declared version. Preserve ring identity through deterministic layers; do not pretend a loose set of polylines is already a parametric CAD solid. Consumers still need a verified units/import setting. Initially represent the existing polygonal geometry directly; advanced spline and arc entities are not required.

STL describes a triangulated 3D surface, not an editable 2D cutline; it carries neither image texture nor a standardized physical-units field. [S25] Therefore the export action must construct top and bottom faces plus outside and hole walls. Use millimeter-valued coordinates and show **Import STL in millimeters**, expected XY size and thickness. A name such as `sticker_30x20x2mm.stl` is a useful hint, not reliable unit metadata. A flat zero-thickness mesh is not an acceptable substitute for this feature.

A basic STL export is a solid silhouette or backing blank, not a cookie-cutter wall, mold, heightmap, embossed photograph, or fully modeled 3D character. Keep those operations in downstream CAD. Multiple separate outlines produce separate closed components at the chosen relative positions; report that they are separate objects. Do not silently add connecting bridges or a shared base. Reject intersecting/touching components for a combined STL unless a separately specified union operation has been implemented and validated; selecting/exporting the objects separately remains available.

#### Browser implementation and validation

Use a small pure TypeScript boundary: `exportCutGeometry(snapshot, options)` produces bytes, suggested filename, MIME type, source snapshot identity, dimensions and warnings. This is an internal function contract, not an HTTP route. Native SVG text serialization and a tested bounded DXF writer are sufficient for the initial polygon representation. An exporter does not need a full CAD kernel.

A pinned Three.js `Shape` → `ExtrudeGeometry` → `STLExporter` path is a candidate for STL conversion and preview. The documented shape model supports holes with opposite winding; extrusion has a depth option and enables bevels by default, so explicitly set `bevelEnabled: false` to preserve the specified footprint. [S26] [S27] Enable binary STL explicitly. The serializer does not by itself guarantee a printable or nonintersecting model; validate the geometry. No WebGL context should be required merely to generate/download the bytes.

Package export dependencies with the web app; no CDN calls or third-party uploads at export time. Use a browser worker for expensive validation/triangulation when necessary, with cancellation and bounded memory/vertex counts. Export failure must not freeze the editor, mutate a project, change an approved render, or submit a job. Create local download files and release temporary buffers/URLs when no longer needed. Sanitize filenames and identifiers; never serialize executable scripts or unsanitized uploaded SVG markup.

Validate finite coordinates, closed nonzero-area rings, containment, self-intersection and component overlap before extrusion. Check watertight edges, consistent outward faces, positive volume, no degenerate triangles, expected bounds and correct through-holes afterward. STL's triangle ordering and float representation need their own tolerance-aware tests. A geometric validity check is not a guarantee that an arbitrarily thin feature prints on every machine.

Preserve canonical polygon vertices by default; do not run an additional unrequested simplification during export. For future curves, any flattening uses a declared physical deviation budget. Proposed exporter-only maximum added XY deviation is 0.01 mm, covering optional tessellation plus numeric encoding, not claiming 0.01 mm printer or source-tracing accuracy. Check the chosen serialization precision against the actual model extent and reject/flag exports that cannot meet the budget.

Acceptance fixtures must include an asymmetric 20 × 30 mm shape extruded to 2 mm, a shape with a through-hole, a detached component, repeated rotated placements, and a historical geometry snapshot after the current project changes. Verify dimensions after independent SVG/DXF parsing and target CAD import, plus STL mesh checks and import into the selected slicer. Include explicit tests that no export conversion request, SQLite print record, print-counter change, or USB access occurs. With geometry/dependencies already loaded, disable the network and verify all three local downloads still work.

## 7. Service architecture and deployment

### 7.1 Components

**Python API service:** Proposed FastAPI application serving the browser build, asset/project operations, extraction/render tasks, validation, and job control.

**Image/geometry worker:** Python image processing plus an optional bounded Node helper for Honeymaro. Use structured JSON requests/results and server-owned file references over standard input/output or a local IPC channel. Keep it private; do not create a second public API. Bound input size, execution time, memory, and output size. Never interpolate upload filenames into shell commands.

**Browser export module:** Client-side SVG/DXF serialization and optional STL extrusion from loaded resolved contours. It owns no server route, database table, or printer connection. See section 6.1.

**Printer worker:** A USB-only owner of the physical device with serialized command/response handling. Other workers receive status from it; they do not open the USB interfaces independently. There is no Bluetooth worker or Bluetooth dependency.

**Persistence:** SQLite is the authoritative store for server-side print history, durable queue state, immutable revision metadata, and artifact references. Store original uploads, masks, approved images, canonical cut-geometry snapshots, PLT files, previews, and bounded diagnostic attachments in a local content-addressed artifact directory. The database stores their identities, hashes, sizes, and ownership references, not base64 image strings. Queue and history are two views of the same persisted jobs, not separate systems to synchronize. See sections 7.3–7.6.

**Optional remote worker:** The web/API service may run on another host, but the printer worker stays on the Linux machine physically connected to the printer. Use an authenticated worker channel and one device-owner lease. Do not require exposing USB hardware to every web-service replica.

### 7.2 Linux operating model

Run without root using narrowly scoped device permissions. PixCut-App documents libusb and udev setup for Linux. [S01] A systemd deployment is sufficient for the first version. Containers are optional and must have explicit device access and persisted job data. Restarting the web frontend must not restart an active device transfer.

Bind to loopback by default. LAN deployment requires authentication and origin/CSRF protection for actions that consume media or move the printer. Keep uploads local, reject path traversal and external resource references, enforce decoded-pixel and upload limits, and sanitize any SVG. Raw device commands and firmware operations are not public API features.

### 7.3 SQLite history and storage contract

Use one configurable `PIXCUT_DATA_DIR`, containing `studio.sqlite3`, `artifacts/`, `tmp/`, and an optional local `backups/` destination. Keep the live database and its WAL sidecars on a local filesystem on the server. An optional remote printer worker communicates through the service, never by opening a network-shared database. SQLite WAL requires same-host database access and allows one writer at a time; keep write transactions short. [S18]

Proposed database settings are `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, and a bounded `busy_timeout` of 5000 ms. Set connection-local settings on every connection, outside an active transaction where required, and verify their values. FULL adds a commit-time WAL sync; explicitly enabling foreign keys avoids depending on build defaults. [S19] Do not hold a transaction open while decoding images, processing geometry, making USB calls, waiting for paper, or maintaining an SSE connection.

Pin and verify the SQLite library actually loaded by Python, not only the command-line executable. Python exposes it as `sqlite3.sqlite_version`. [S21] Require SQLite 3.51.3 or newer, or an explicitly reviewed patched build: SQLite documents a rare WAL-reset corruption bug fixed in 3.51.3 and backported to 3.44.6/3.50.7. [S18] Record the runtime version in diagnostics. This version rule addresses that known bug; dependency review still occurs at implementation time.

Use versioned, reviewed migrations. A designated foundation maintainer owns migration ordering. Pause device dispatch, drain active work, and back up before schema upgrades; do not let several starting processes independently migrate the same database.

Minimum logical history entities:

| Entity | Required persisted information |
|---|---|
| `print_jobs` | Application ID, submission timestamp/actor, name and project-revision snapshot, approved render, printer identity, mode, canonical request hash, unique idempotency key, requested page/copy selection, execution mode (`hardware` or `simulator`), parent reprint reference, aggregate state. |
| `sheet_jobs` | Parent job, immutable ordinal/page/copy mapping, render-sheet reference, intended sticker count and physical layout summary, image/cut-geometry/PLT/manifest hashes, profile snapshots, current state, created/started/finished times, completion evidence and error summary. One row per requested physical sheet copy. |
| `device_attempts` | Sheet job, persisted create intent, device job ID when known, firmware and adapter identity, actual USB/profile versions, transfer summary, last observed device state, error and uncertainty evidence. V1 permits one job-creation intent per sheet; explicit reprints create new sheet jobs rather than recycling an attempt. |
| `job_events` | Durable ordered event ID, job/sheet/attempt references, timestamp, event type, state transition, evidence source, bounded structured payload, and actor for manual annotations/resolutions. Append events; do not rewrite the timeline. |
| `artifacts` and artifact references | SHA-256, relative storage key, MIME type, encoded bytes, optional dimensions/raster hash, lifecycle state, creation time, and references from assets/renders/history. Identical files may be shared without duplication. |
| Revision/profile records | Immutable project, sticker, render, media, calibration, and serializer versions needed to explain a past print. The job stores display snapshots so renaming a current project does not rewrite history. |

Use relational columns for IDs, states, timestamps, relationships, and common filters. Use versioned JSON for immutable settings/manifests and bounded device observations, not one opaque JSON blob for all history. Add foreign keys, uniqueness for idempotency and sheet ordinals, and indexes for creation order, printer/status/date, project, and reprint lineage. Store UTC timestamps; display local time in the browser. Order events and pagination by durable IDs as well as timestamps, not wall-clock time alone.

Large image/cut bytes stay in files by design. SQLite remains authoritative for whether a file exists, belongs to a render, is retained, and is eligible for submission. There is no automatic scan of arbitrary directories to invent successful jobs.

### 7.4 Durable submissions, events, and file publication

Publish each artifact to a temporary file on the same filesystem, verify its contents/hash, flush it, and atomically move it to its immutable hash-based key; synchronize the containing directory as appropriate for the deployment. Only then commit database references. A crash may leave an unreferenced file for later cleanup, but must not publish a sendable render pointing at a half-written image.

A single short transaction accepts a job, reserves its idempotency key, inserts the requested sheet rows, retains their artifacts, and records the queue event. The dispatcher sees only committed jobs. Update a sheet's current state and append its corresponding event in one transaction. Emit notifications only after commit; on reconnect, the browser reads committed history and replays durable event IDs. Repeated telemetry may be coalesced; terminal evidence, create intents, errors, and manual resolutions must not be dropped. Do not store an event for every USB packet.

Persist the create intent before any non-idempotent device operation. Persist the returned device job ID before artifact transfer. Database retry wrappers may retry safe database work; they must never encompass a USB job-creation call or automatically replay it. If the database cannot accept a required checkpoint, stop initiating additional device actions and hold the queue; a device that already started may still finish. Reconcile its outcome without asserting that the service has physically stopped it.

A process crash, database outage, or lost device response can leave the physical outcome ambiguous. Do not describe the design as guaranteed exactly-once physical printing. Its enforceable contract is durable intent, duplicate-request suppression, and no automatic resubmission after an ambiguous side effect. Preserve device ownership until handoff/reconciliation is safe.

### 7.5 History UI and exact reprints

Provide a History screen with thumbnail, job name, submission/completion time, printer, mode, requested/completed/failed/cancelled/uncertain sheet counts, and reprint lineage. Filter by date, name, project, printer, and outcome; sort newest first with cursor pagination. Simulator jobs are labeled and excluded from real production totals by default.

A detail view shows each sheet/copy and its event timeline, the exact submitted image with serialized-cut overlay, dimensions, intended sticker count, payload format, profiles, and errors. Expose downloads of retained PNG/BMP, PLT, and manifest. Retain the pre-device canonical cut-geometry snapshot alongside them so the browser can export historical outlines as SVG/DXF/STL without retracing artwork or consulting the latest project. These local exports do not create new print jobs or print-history events. Distinguish intended sticker count from physical output verified by a person; a completed device job alone does not inspect every individual sticker.

Reprint selected sheets or the complete saved job using the retained immutable render. The user explicitly selects copy count and confirms submission. Do not rerun background extraction, tracing, layout, image encoding, or cut serialization. Create a new job and idempotency key with `reprint_of_job_id`; never reopen or change the original job's result. Run current device/media/profile eligibility and hash checks first. A revoked/incompatible profile, changed calibration, missing artifact, or different required output format blocks exact reprint. An explicit new render/approval can instead create a new job, clearly identified as re-rendered rather than identical.

Uncertain outcomes require a reconciliation screen. Show the evidence and allow an operator to record physical completion, noncompletion, or continued uncertainty with a note. Record evidence provenance (`device`, `operator`, `simulator`) and append the resolution; never fabricate a device completion code or remove the original ambiguity. Resolving an outcome is separate from authorizing another print. Cosmetic history annotations may change, but submitted artwork/settings and original events remain immutable.

### 7.6 Retention, backups, and recovery

Recommended V1 retention: keep history metadata/events and submitted render artifacts until explicitly purged. Archive a project without deleting images or revisions referenced by history. Keep original project assets while referenced by an unarchived project or retained editable revision. Verbose debug traces and abandoned temporary files have a separate bounded retention policy; they are not the authoritative history. Keep errors and completion evidence in SQLite even after a debug log rotates.

Report storage usage and a configurable low-space threshold. Block new uploads/renders/jobs when required durable storage cannot be guaranteed. Never solve disk pressure by silently deleting old submitted sheets. An explicit later purge marks artifacts unavailable and disables exact reprint; history can remain as metadata. Reference-aware cleanup must protect shared files, queued jobs, active jobs, and backups in progress. V1 needs archive plus safe temporary/orphan cleanup, not an elaborate automatic retention scheduler.

Back up a consistent database snapshot with SQLite's backup API, available in Python as `Connection.backup()`. It supports backup while the database is in use. [S20] [S21] Do not treat copying the live `.sqlite3` file alone as a sufficient backup. For the complete application backup, prevent garbage collection, create the database snapshot, copy every immutable artifact referenced by that snapshot, verify hashes, and write a backup manifest. Mark the backup complete only after both database and required files verify. New work can continue because the backed-up artifacts are immutable; schedule a maintenance window for schema changes.

Restoration is a separate, tested path: start with USB dispatch disabled, verify database integrity, foreign keys and artifact hashes, and mark any restored nonterminal hardware jobs for reconciliation. A stale backup must not automatically replay its queued or active sheets, since they may have printed after the backup was taken. Require operator release before dispatch resumes. A backup stored on the same disk is a convenience snapshot, not protection against loss of that disk; document an external backup destination.

## 8. Canonical data model

| Resource | Required information |
|---|---|
| Asset | Immutable original bytes and MIME type, lossless normalized raster, file and decoded-pixel hashes, dimensions, recorded orientation/color normalization, optional declared physical size. |
| Extraction | Asset revision, method/settings, mask reference, user corrections, candidate region labels, warnings, processing-engine version. |
| Sticker definition | Source asset/crop, source-space mask/contours, grouping, cut policy, default physical size, provenance, revision. |
| Placement | Sticker ID resolved through the project's shared revision binding, position, size/rotation, lock state, explicit local-to-sheet transform. No artwork, mask, or cut-policy overrides. |
| Project revision | Shared sticker-ID-to-revision bindings, pages, placements, preserved source-sheet layers, sheet profile, defaults, and revision number. |
| Render | Immutable project revision, lossless working master where applicable, exact profile-bound device image, MIME type, document-format code, dimensions, encoded byte count, file/pixel hashes, canonical cut-geometry snapshot/hash, optional PLT, page order, profile and renderer versions, validation report. |
| Production job | SQLite-backed submission, immutable render/settings snapshot, printer, selected sheets/copies, idempotency key, child-sheet jobs and device intents, append-only state history, completion evidence, and reprint lineage. |
| Printer profile | Media identifiers, raster/cut geometry, USB dialect, calibration, per-format validation state and approved byte/dimension limits, verified capabilities, and evidence provenance. |

Use source pixels for extraction and crop data. Use millimeters with top-left origin, X right, Y down for page editing. Specify pixel-center conventions explicitly. Use float64 transformations and round only at rasterization or device serialization.

Printing and cutting share the same design-to-page transform, then pass through separately calibrated page-to-printer mappings. Do not apply print compensation to the cut path twice. Freeze every relevant revision when producing a render. A later edit requires a new render to represent the edited project; it does not mutate or delete an already approved historical render. Reprinting a historical render remains subject to current artifact-integrity and printer/profile-eligibility checks.

### 8.1 Shared cut-geometry snapshot

Define one versioned `cut-geometry` structure for editor inspection, pre-device serialization, saved renders/history and client-side export. This is persisted application geometry, not a server-generated SVG/DXF/STL file. Existing project/geometry responses deliver the current resolved geometry; a render's ordinary artifact route exposes its immutable snapshot as `cut-geometry.json`.

The contract includes schema and geometry-engine versions, snapshot identity/hash where committed, provenance (`draft`, project revision or render ID), page ID and physical size, `units: mm`, `coordinate_space: sheet_design`, top-left origin/X-right/Y-down, and `geometry_stage: finished_cut_pre_device`. Record relevant sizing/border/simplification policy references. A draft carries a local revision marker and must not borrow a saved render's hash.

Each shape contains a stable shape ID, sticker ID/revision, placement ID, one outer ring, enabled hole rings, and explicit topology/containment. A nested foreground island is another shape, not a hole guessed from winding. Rings contain finite double-precision coordinate pairs in the declared frame, at least three distinct vertices, and explicit closed status. Use implicit closure without duplicating the first vertex at the end; serializers add their format's closing marker. Normalize/validate ring orientation, but retain explicit outer/hole meaning rather than deriving it from orientation alone.

Resolved page-space coordinates already include placement transforms and the physical cut offset. Do not reapply them in the client exporter. Optional local-geometry and transform metadata may explain provenance, but the resolved points are the authority for that snapshot. Repeated placements still reference one shared sticker definition; resolved export geometry is not a detach feature.

Freeze this geometry before applying device calibration, quantization, mechanical entry/exit, or PLT coordinate ordering. Store its hash in the render manifest and retain it with the submitted artifacts. A history export selects this frozen snapshot; editing the project cannot change it. CAD exports preserve intended design dimensions, while PLT retains the calibrated machine commands actually submitted.

The export module reads a consistent snapshot already available in browser memory. It may not mix a new outline with stale placement transforms or export unfinished asynchronous geometry calculations as final. An editor request to resolve a genuinely new mask/border is part of ordinary editing; it is not a reason to introduce server export endpoints. Nonprinting export remains available without hardware format validation.

## 9. Public API contract

Version the HTTP API under `/api/v1`. Normal operations use JSON; uploads use multipart form data; artifact downloads return files. Long processing tasks return `202` with an operation ID. The fact that a render task is asynchronous is unrelated to permission to run hardware.

| Method and route | Purpose |
|---|---|
| `POST /assets` | Upload original image bytes without browser-side transcoding; return source and lossless normalized asset metadata. |
| `POST /assets/{id}/extractions` | Start a foreground-mask/region extraction task. |
| `GET /projects?kind=photo\|sticker` | List the newest editable project revisions for one workspace, excluding commissioning records. |
| `GET /operations/{id}` | Obtain processing state, output references, and warnings. |
| `POST /stickers` / `PATCH /stickers/{id}` | Create or revise reusable sticker definitions and cut geometry. |
| `POST /projects` / `GET /projects/{id}` / `PATCH /projects/{id}` / `DELETE /projects/{id}` | Create, retrieve, revise, or remove an editable Sticker project. Deletion does not remove retained history. |
| `POST /projects/{id}/restore-source-layout` | Reconstruct an extracted sheet at full source scale from its retained crop provenance and create a validated project revision. |
| `POST /projects/{id}/cut-policy` | Atomically revise the project's shared sticker definitions with physical padding/notch settings and revalidate layouts. |
| `POST /projects/{id}/layouts` | Produce a repeat, grid, fill, or pagination proposal without printing. |
| `POST /projects/{id}/renders` | Freeze a specified revision, validate it, and produce print/cut artifacts. |
| `POST /photo-projects` / `GET /photo-projects/{id}` / `PATCH /photo-projects/{id}` | Create, retrieve, or revise non-destructive Photo settings with revision checking. |
| `POST /photo-projects/{id}/draft-preview` | Render a deterministic, non-persisted preview from submitted draft settings. |
| `POST /photo-projects/{id}/renders` | Save/freeze a Photo revision and create its exact 4×6 proof artifacts. |
| `GET /renders/{id}` / `GET /renders/{id}/artifacts/{name}` | Inspect or retrieve the exact approved outputs. |
| `GET /printers` / `GET /printers/{id}` | Return every applicable profile plus USB discovery, worker heartbeat, readiness status, and attention job. |
| `POST /jobs` / `GET /jobs` / `GET /jobs/{id}` | Explicitly enqueue an approved render or inspect cursor-paginated production state and active queue length. |
| `POST /jobs/{id}/cancel` | Cancel queued work; active cancellation is capability-dependent. |
| `POST /jobs/{id}/resume` | Requeue one held hardware sheet only when no device attempt exists. |
| `POST /jobs/{id}/reprints` | Create an explicit new job from retained eligible Sticker sheets. |
| `POST /jobs/{id}/resolutions` | Revision-checked operator resolution of an uncertain sheet; power-cycle recovery requires restart confirmation. |
| `GET /events` | Server-sent updates for operations, renders, printer state, and production jobs. |

Paths in the table are relative to `/api/v1`. Existing geometry/project responses and render-artifact reads supply the cut-geometry contract from section 8.1. There are no `POST /exports`, CAD conversion tasks, or export-history endpoints; SVG/DXF/STL file generation is client-side.

### 9.1 Layout request semantics

A layout request identifies a project revision, one or more sticker revisions, mode, target physical size, quantity or grid dimensions, gaps, permitted rotations, margins, and overflow behavior. The response contains a proposed page/placement set, achieved quantities, unplaced items, and warnings. Committing the proposal creates a new project revision.

Do not accept both “fixed count” and “fill sheet” as competing authoritative constraints. Require an explicit mode and return actionable errors for incompatible fields. Placements reference shared sticker IDs; reject per-copy content overrides. Revising a sticker updates its shared project binding, never just one selected copy.

### 9.2 Extraction request semantics

Specify the source asset, optional crop, background method, color samples or mask, thresholds, small-region policy, and output intent. Use `preserve_layout` or `extract_assets` for intent. A separate `print_background` choice controls whether the original background is retained in the print layer. Return candidates rather than silently finalizing ambiguous regions.

### 9.3 Render request semantics

A Sticker render request pins the project revision and creates a 1200×2100 RGB PNG master, geometry, overlay, PLT, thumbnail, and manifest. Promotion to `sticker-4x7-jpeg` creates and freezes its bounded printer JPEG before device intent. A Photo render request pins the saved Photo revision and its selected quality, then produces the exact 1200×1800 `photo-4x6-jpeg` artifact plus provenance. Render generation never submits hardware.

Offline editing and preview generation remain possible without a printer. Printer readiness is checked again only when the reviewed render is submitted. The response reports dimensions, encoded byte count, hashes, artifacts, and profile snapshot. The worker sends the exact frozen bytes; it does not choose an output fallback at execution time.

### 9.4 Job request semantics

Require `render_id`, `printer_id`, mode, sheet selection, sheet-copy count, and explicit acceptance of the selected media profile. Require an idempotency key; repeated submission of the same request returns the same application job. A different payload with the same key returns a conflict.

Define modes explicitly as `print_cut` and `print_only`. Do not advertise `cut_only` as an available capability. Use application UUIDs separately from device job IDs. No asset upload, extraction, preview, GET request, or render operation is allowed to create a device job. Reject a render whose output exceeds its selected profile limits, has been altered since approval, uses the wrong profile/media confirmation, or is submitted while printer readiness is false.

### 9.5 Common protocol behavior

Use `409` for stale revisions or conflicting job requests, `422` for invalid geometry or incompatible options, and structured error bodies containing a stable code, message, affected objects, and remediation. Version edits using revision IDs or ETags. Provide SSE event IDs and reconnection support, with polling as a fallback.

Events carry an application resource ID, timestamp, revision or sequence, normalized state, and optional raw device status. A disconnected browser is not a cancelled job. Actionable errors include stale revisions, payload-too-large, printer-not-ready, recovery-confirmation-required, and unsupported instance overrides; none may trigger a format fallback or a second hardware job.

### 9.6 History and reprint API

All routes below are relative to `/api/v1` and read the same SQLite records used by the queue. There is no browser-only print log.

| Method and route | Contract |
|---|---|
| `GET /jobs` | Newest-first cursor pagination with optional state filter. Return job/sheet outcome totals, render ID, reprint lineage, `next_cursor`, and active-sheet `queue_length`. |
| `GET /jobs/{id}/sheets` | Ordered page/copy rows, immutable artifact/profile summaries, state, evidence origin, errors, and timestamps. |
| `GET /jobs/{id}/events` | Paginated durable events, optionally after an event ID, including original errors and later resolutions. |
| `POST /jobs/{id}/reprints` | Explicit request for selected saved sheet rows or the original page selection with a positive copy count; require a new idempotency key and confirmation. Validate exact artifacts/current eligibility, then create a new production job. Return its ID and link to the original. |
| `POST /jobs/{id}/resolutions` | Authorized, revision-checked operator reconciliation of specified uncertain sheet outcomes with notes; append events. It never starts, retries, or cancels hardware by itself. |
| `POST /jobs/{id}/resume` | Requeue a held one-sheet hardware job only if it never created device intent and no hardware activity is unresolved. |

Existing `GET /jobs/{id}`, render-artifact routes, and SSE carry the corresponding detail/updates. Device IDs are diagnostic data, not publicly usable job-control identifiers. An HTTP timeout after acceptance is resolved by idempotency lookup, not by issuing another device job.

`POST /jobs/{id}/reprints` returns a conflict for missing/purged artifacts, revoked profiles, or a request needing a different render; provide `HISTORY_ARTIFACT_UNAVAILABLE` or `REPRINT_PROFILE_MISMATCH` with remediation. Copy expansion must have a documented, stable order. Recommended order: requested pages in ascending order for each set copy, with explicit persisted sheet ordinals. Selecting existing sheet rows reproduces exactly those rows, including repeated copies already selected; do not multiply them a second time invisibly.

## 10. Render, preflight, and artifact contract

### 10.1 Render and exact artifacts

A Sticker simulator render produces a 1200×2100 RGB PNG, canonical `cut-geometry.json`, final simulator PLT, SVG inspection overlay, navigation thumbnail, and manifest. Hardware promotion freezes a separate bounded `sticker-4x7-jpeg` artifact and native polygon PLT for the selected reviewed page. A Photo render produces the exact 1200×1800 RGB `photo-4x6-jpeg`, thumbnail, empty cut geometry and overlay, plus its adjustment/layout provenance manifest. Photo artifacts intentionally contain no cut command.

The manifest binds files to source/project revisions, dimensions, color/alpha policy, engine versions, media/calibration profiles, encoder settings, and hashes. Sticker manifests retain canonical geometry independently from native PLT because CAD intent and machine instructions are distinct immutable artifacts. Photo manifests retain the crop, framing, layout, orientation, preset, film look, adjustment-pipeline version, grain settings, print quality, source asset, and project revision needed to reproduce the approved proof.

Preserve source alpha in Sticker assets and masks, original bytes for every upload, and lossless normalized working rasters. Sizing, rotation, masking, color conversion, photo balancing, framing, grain, and explicit compositing are deliberate render operations. Rasterize exact output from the normalized source rather than repeatedly resampling a displayed preview. Once approved, neither the worker nor an exact reprint may transcode the frozen printer artifact.

### 10.2 File limits and format verification

The upstream PixCut-App workflow is JPEG-oriented: its README specifies 1200 × 2100 output and a 1 MiB JPEG limit, and its sender enforces a JPEG byte guard. That is evidence for the existing JPEG path, not a demonstrated universal PNG/BMP limit. [S01] [S04]

Keep browser upload limits, decoded-image resource limits, working-artifact limits, and printer-format payload limits separate. A large source file may render to an acceptable device payload; a small compressed upload may decode into an unsafe allocation. Limits in the service remain explicit even when a device limit is unknown.

Use approved, format-specific encoded-byte and raster-dimension limits tied to validation evidence. Unknown does not mean unlimited. Record tested sizes and a conservative approved operating ceiling without claiming to have found the firmware's absolute maximum. Current production keeps the printer's 1200×1800 or 1200×2100 300 DPI raster fixed and varies only JPEG quality within the explicit profile range. Oversized output is blocked with an actionable error; the worker never changes quality or format after review.

The format candidates and validation steps are specified in section 11.5. Transferring more bytes in chunks does not establish that the device can decode a larger document. Validate transfer timing and decoder acceptance separately.

### 10.3 Geometry and approval preview

Validate closed finite contours, topology, holes, duplicates, scale, sheet guards, potential cut/bleed collisions, and profile-specific complexity budgets. Never silently drop contours to meet a limit. Inspect lead-in/lead-out movements as well as the main outline; validate blade-up travel against machine limits separately from printable-area limits.

Generate cut geometry from the source/mask and canonical transforms, not from a thumbnail or an encoded print preview. Decode the exact device image for approval and verify that its pixels match the intended final raster. For BMP, serve a PNG preview of those decoded pixels and check raster equality. Parse the final PLT back into a page-space overlay to detect serialization or transform mistakes before submission.

The logical 4×7 canvas is 101.6 × 177.8 mm. It is not a promise that every edge is printable or safely cuttable. Store raster extent, usable print region, cut region, and physical stock dimensions independently. Nominal cut units are 40/mm, but local calibration must be separate: East Bay's profile explicitly marks its correction factors as local to its printer/media path. Do not copy those factors as universal defaults. [S12] [S09]

## 11. Device protocol: USB

### 11.1 Observed interface and control envelope

The community implementations identify USB VID/PID `302c:3101`, with command interface 2 using OUT `0x06` and IN `0x86`, and data interface 3 using OUT `0x04` and IN `0x84`. Verify descriptors and device identity when connecting. [S07]

Control messages are compact UTF-8 JSON prefixed by the ASCII bytes `cmd json\n`. Correlate responses using their `id`; distinguish responses from unsolicited events and tolerate fragmented/coalesced reads. This is a printer-specific protocol, not a general HTTP or standards-complete JSON-RPC endpoint. [S03] [S05]

Use read-only identity/status queries during discovery. Restrict normal operation to the commands the adapter needs: job creation, `get-prop`, `get-job-info`, and verified lifecycle controls. Do not expose arbitrary `set-prop`, diagnostic probes, or firmware updates.

### 11.2 Combined job sequence

Preflight the selected media, acquire exclusive device ownership, and persist a create-job intent. Declare `combo-job`, containing a nested `print-job` followed by a nested `cut-job`, each with the selected adapter's metadata and exact artifact sizes. The nested cut declaration uses `file-size`, not the standalone cut command's `plot-file-size`. Persist the returned device job ID before beginning payload transfer. [S04] [S07]

The examined JPEG path sends PLT bytes before image bytes, regardless of the declaration order, and uses job type 600 and sticker media identifiers 5013/2030. [S04] [S07] Preserve cut-first/image-second ordering for the current Sticker profile and validate the complete sequence independently before adding another format. Upload and monitor a single logical combined job; do not substitute separate print and cut jobs.

Select the image's protocol format, matching filename extension, exact encoded byte count, and hash from the approved render, not from a hard-coded JPEG template. The uploader sends those immutable image bytes without transcoding them. Hash metadata must correspond to the selected profile and actual artifact; do not inherit captured placeholder hashes. See section 11.5.

### 11.2.1 Standalone cutter investigation

The protocol surface includes a standalone `cut-job`, but it is not established as a working cutter-only mode. East Bay's client defines a minimal request with `channel`, `copies`, media identifiers, `job-type`, and `plot-file-size`; its documentation leaves the path unverified. PixCut-App's protocol report records a cut-only attempt being discarded with all-zero job fields. [S05] [S07]

The bounded harness tests only that minimal `job-type=0` form and one `job-type=600` form using the captured PLT metadata and `file-size`. It transfers PLT only. It does not send a blank raster, construct a combo job, fuzz job types, expose raw device commands, or infer success from an ACK. A valid device job ID, nonzero lifecycle, cutter progress, unchanged `printed` counter, absence of an observed print pass, and an operator observation are retained separately.

A genuine cutter-only path would not activate the dye-transfer ribbon, so ribbon adhesion caused by a second print pass is not the relevant failure mode. The intended second layer is a thin self-adhesive glitter laminate applied flat across the completed sticker sheet—not loose glitter or a wet coating. Reload feed alignment, sheet flatness, added thickness, edge adhesion, blade pressure, and backing integrity remain separate physical risks. Production recutting requires three completed reload trials at no more than 0.5 mm maximum registration error, with no print/overcoat activity, counter change, jam, or through-cut. Curled media, lifted edges, trapped bubbles, and shedding surfaces are excluded. If either declaration is discarded, begins printing, or remains ambiguous, no public cut-only profile or UI is created.

The `copies` field represents additional output sheets, not repeated dye exposure of one physical sheet. Same-sheet physical double printing remains unsupported. A double-exposure appearance belongs in the image pipeline as a single composited raster; that editor capability is deferred.

### 11.3 Data envelope and incompatible framing assumptions

The shared outer structure is:

```text
cmd data EXTLEN=<decimal length>\n
<4-byte little-endian device job ID><chunk bytes>
```

**The meaning of `EXTLEN` is not consistent between the two implementations.**

| Dialect | Length field | Existing transfer behavior |
|---|---|---|
| PixCut-App | Chunk bytes plus four job-ID bytes. | Default EXTLEN 4075 yields 4071 artifact bytes per chunk; its documented pacing is approximately 120 ms. [S03] [S01] |
| East Bay SDK-style | Chunk bytes only, despite the job ID still being present. | 10215 artifact bytes per chunk, framed packet split into 1024-byte USB writes, ACK then approximately 20 ms delay. [S07] |

Do not average these settings, fix the discrepancy by guesswork, or switch dialects during a job. Implement them as distinct adapter profiles. Start with the PixCut-App profile; activate another only after an explicitly approved test and validation on the target hardware. Use read-only probes for discovery, not print jobs that silently test alternatives.

Validate ACK contents, not merely a nonempty read. Keep logical byte progress separate from USB framing overhead. Serialize polling and ACK consumption so one task cannot consume another task's reply. A missing ACK may mean lost feedback rather than lost payload; do not blindly resend a non-idempotent chunk.

### 11.4 Completion and errors

Keep progress stages distinct: creating the device job, transferring, device processing, printing, cutting, and completion. Only display a specific physical phase when supported by the observed telemetry; otherwise show “processing.” Unknown status codes remain visible in diagnostics.

The protocol notes report successful completion using job-state 9 with sub-state 9000 and reason 90001. Treat those as profile-validated observations, not a universal assumption for all firmware. [S05]

Printer-idle alone is insufficient proof of success. Status loss after activity creates an uncertain outcome until completion is reconciled. Empty paper can leave a job resumable after media is loaded, as reported by East Bay; do not enqueue a duplicate automatically. [S06]

The observed data-channel signatures `Unexpected data ACK` and device event `-8013` add a structured `power_cycle_printer` recovery action to the uncertain sheet evidence. The hardware worker stays alive in a blocked state and never retries that sheet. Its database heartbeat exposes starting, idle, working, blocked, stopped, and stale/offline states to the API. While a power-cycle action is present, the browser replaces the History queue count with an accessible `!` attention marker and shows the full power-off/power-on sequence in History. The operator must confirm the restart before **It printed** or **Nothing printed** can resolve the uncertain sheet through the event-ID-guarded API; neither action resubmits it. After resolution, the worker performs fresh discovery and constructs a new USB session for the next job, so device handles and interface claims do not survive a printer reboot. This marker is intentionally narrower than a generic failed-job indicator: explicit creation rejections and ordinary held jobs do not imply that the printer needs a reboot.

### 11.5 Device image formats: candidates and acceptance gate

The community protocol notes list the following identifiers. Their presence is not a successful target-printer USB test. PixCut-App's examined sender hard-codes JPEG format 9 and `.jpg` naming, so it needs an explicit format-aware adaptation. [S05] [S04]

| Artifact | `document-format` | Proposed application treatment |
|---|---:|---|
| PNG image | 10 | Research candidate; the tested 1200×1800 print-only declaration was rejected before transfer and is not a production profile. |
| BMP image | 11 | Untested research candidate; not a production profile. |
| JPEG image | 9 | Current bounded production format for `photo-4x6-jpeg` and `sticker-4x7-jpeg`. |
| PLT cut data | 18 | Retain the selected calibrated cut serializer. |

Use a generic image-artifact contract instead of `jpg_path`, `jpg_bytes`, `.jpg` filenames, or a payload slot restricted to `jpg`. Required fields are artifact reference, MIME type, document-format code, encoded byte count, dimensions, content hash, and profile ID. The sender must validate file signature/decodability against the declared format and transmit the exact approved bytes. Upload PLT first and then the selected image under the validated USB framing profile.

Hardware verification is an explicit commissioning activity, not something an ordinary print request does automatically. First establish read-only discovery and inspect a format-aware dry-run declaration. Then run a manually approved, profile-bound job on correctly matched media using simple asymmetric artwork and conservative outlines. Verify image decoding, colors, orientation, print/cut registration when applicable, final completion, and physical output. A successful job creation or transfer ACK alone does not prove format support.

Each new format remains a separate commissioning activity rather than a mid-job retry. Record firmware, media, USB dialect, encoder settings, artifact hashes, byte sizes, timing, and results. Test both representative flat sticker sheets and higher-entropy photographic sheets; successful printing of one small artifact would not validate every sheet. Size/timeout experiments use bounded, explicit test profiles. Preserve the worker heartbeat and serialize it with ACK handling during longer transfers.

Only promote a profile after successful physical tests across its intended operating range. Do not label PNG or BMP as supported based only on a protocol enum. The present JPEG profiles were promoted from recorded successful device and operator-confirmed output; any future PNG/BMP profile must earn its own evidence and may not replace a reviewed JPEG within an existing job.

### 11.5.1 DHP700 payload tests on September 11, 2026

The following one-copy, print-only tests used firmware `1.0.34_0073`, 4×6 photo paper, a 1200×1800 RGB raster, and the official-SDK data framing profile. Each failed attempt was made once and was not replayed.

| Device job | Artifact | Declared bytes | Creation result | Transfer result | Physical result |
|---:|---|---:|---|---|---|
| none | PNG, document format 10, Huffman-only DEFLATE | 2,178,925 | Rejected with error `8008` | No image bytes sent | Nothing printed |
| none | JPEG, document format 9, quality 100, 4:4:4 | 1,085,726 | Rejected with error `8008` | No image bytes sent | Nothing printed |
| 23 | JPEG, document format 9, quality 99, 4:4:4 | 917,029 | Accepted | 531,172 bytes reported received, then data ACK `unsupported cmd` | Timed out with event `-8013`; terminal state `7/7000/70000`; `print-time=0` |
| 24 | Production JPEG, document format 9, quality 95, 4:4:4 | 861,895 | Accepted | Host recorded one 10,215-byte ACK, then data ACK `unsupported cmd`; device later reported 12,272 bytes | Timed out with event `-8013`; terminal state `7/7000/70000`; `print-time=0` |
| 25 | Same production JPEG as job 24, quality 95, 4:4:4 | 861,895 | Accepted after a full printer power cycle | All 861,895 bytes acknowledged | Completed with state `9/9000/90001`; operator confirmed a good physical print |

The PNG result is a format/declaration rejection, not a payload-size measurement, because job creation failed before the data channel opened. The two JPEG declarations bracket an observed creation boundary between 917,029 and 1,085,726 bytes, consistent with the existing 1 MiB guard, but they do not locate an exact threshold. The accepted JPEG then exposed a separate transfer failure.

The first transfer failure occurred after about 52 configured 10,215-byte chunks. The host had received valid ACKs before `unsupported cmd`; device telemetry later reported 531,172 bytes, eight bytes below 52 complete payload chunks. A subsequent ordinary production job below 1 MiB then failed on its second data frame, showing that neither 531,172 bytes nor the selected JPEG quality is a general payload ceiling. After the printer was fully powered off and back on, the exact 861,895-byte artifact from job 24 transferred and printed successfully as job 25. This rules out that artifact's size, encoding, and quality as the cause of job 24's rejection and strongly supports persistent printer/data-channel state following the interrupted upload, although the internal device cause remains unknown. Known successful JPEG photo jobs on the same printer have now transferred 802,091, 861,895, and 876,537 bytes.

Do not retry device jobs 23 or 24 or promote the larger JPEG test profile. If a transfer produces `unsupported cmd` or times out with `-8013`, the current worker records the attempt as uncertain and remains alive but blocked. Fully power-cycle the printer: hold the power button until its light turns off, then hold it again until the light turns on. Confirm that restart in History and record whether the affected sheet printed. A manual worker-process restart is optional and safe, but it does not clear the SQLite reconciliation block.

## 12. Device protocol: PLT and calibration

The examined serializer emits an ASCII token stream using `IN`, `VER`, `KP`, blade-up `U`, blade-down `D`, and terminal `@`. A serialized coordinate pair writes feed Y before carriage X. Initial blade-up/down moves may intentionally share a point. Preserve this behavior rather than treating it as a redundant vertex. [S11]

Nominal units are 0.025 mm. Honeymaro's transform also reverses the feed axis relative to image coordinates and may apply overscan and edge clipping. [S12] [S13] The service must perform these conversions explicitly through its selected serializer profile, not by guessing a universal rotation from one example.

Closed sticker contours use the stock-style three-point overcut ramp measured by Honeymaro: a 15-pixel maximum reach and 45-degree swing derived independently from each seam's incoming and outgoing edges. The blade approaches from the white margin, completes the closed boundary, and exits through the seam on the mirrored side. Do not reuse fixed lower-left rectangle offsets for arbitrary contours; doing so can leave a small uncut tab when the local edge directions differ. Keep these device entry/exit moves in the retained PLT rather than the canonical design polygon or CAD exports. [S10] [S11]

Use asymmetric calibration artwork with labeled corners, horizontal and vertical rulers, a non-square rectangle, and center/edge targets. Validate orientation first, then print scale, cut scale, relative offset, and edge guards. Save corrections against printer identity, media, firmware, transport, and serialization version.

**Knife-pressure settings conflict:** Honeymaro's documentation describes a 0–3 range, whereas PixCut-App and East Bay use KP42 in their working sticker workflows. Do not invent a mathematical conversion between those ranges. The ordinary UI should select a named, hardware-validated kiss-cut profile. Raw pressure values, perf-cut, and through-backing experiments remain outside the initial workflow. [S10] [S01] [S06]

Cut-only behavior remains unvalidated/unsuitable for the normal workflow; the PixCut-App protocol notes report jobs discarded without execution. Consequently, omit “cut an already printed sheet” from the initial app. [S05]

## 13. Browser-to-service and USB ownership boundary

The application has one printer path:

```text
Browser --HTTPS--> Python API / queue --local IPC--> printer worker --USB--> PixCut S1
```

A single-process/single-host implementation may use direct internal calls instead of IPC. A separately hosted API may use the authenticated remote-worker arrangement from section 7.1. In either deployment, the printer worker owns the USB device and the browser uses ordinary authenticated HTTP/SSE. Access over a phone's Wi-Fi connection does not require a wireless connection to the printer.

Do not ship Bluetooth scanning, pairing, GATT framing, BLE senders, Web Bluetooth permissions, a transport picker, or USB-to-wireless fallback. Remove Bluetooth-only dependencies from the deployment bundle. Only reuse the USB-related portions of the East Bay work, subject to its licensing gate. This is a scope exclusion, not an optional later milestone.

## 14. Queue, recovery, and cancellation

SQLite stores queue state and history together. Every accepted submission is visible in history from acceptance onward, including failures before USB access. Refer to sections 7.3–7.6 for durable intent, events, exact reprints, and restoration.

A production job expands into child sheet jobs. Track device activity separately from preparation tasks. Proposed lifecycle:

```text
queued → preflight → creating → transferring → processing → completed
```

Optional observed subphases refine `processing` into printing and cutting. Side states include `waiting_for_media`, `failed`, `cancelled`, and `outcome_unknown`. An unknown outcome is neither success nor an invitation to retry.

Persist a create-job intent before sending the command, device IDs once received, artifact hashes, and state transitions. A crash after creation but before receipt of the device ID is still ambiguous; durable intent records must prevent an automatic fresh submission.

Queued work can be cancelled reliably. An active job may not be physically cancellable using the validated adapter. Expose a capability report and honest UI wording. Stopping the service process or closing a browser must not be presented as a guaranteed hardware stop. Requesting cancellation of a batch prevents subsequent sheets while preserving the current sheet's true outcome.

One physical printer has one device owner and at most one active sheet job. Other applications controlling that printer must be closed during service operation. Reprinting is an explicit new user action with a new idempotency key and a link to the original job.

Use the shown lifecycle for individual sheets. Summarize the parent batch separately: all confirmed sheets means completed; settled mixed outcomes mean partially completed; any unresolved sheet requires attention. Show component counts rather than collapsing a partly printed batch into an unqualified failure. Operator-confirmed and device-confirmed outcomes remain distinguishable.

On an ordinary restart, never resubmit a sheet with a persisted device-create intent automatically. Untouched queued work may continue only after the device is known to have no unresolved prior activity and any media/error hold is cleared. After a backup restoration, all nonterminal hardware work requires explicit reconciliation/release, including apparently untouched queued rows. No blind USB reconnect retry, media-error retry, or lease expiry may bypass this rule.

## 15. Development roadmap and acceptance gates

| Stage | Deliverables | Acceptance requirement |
|---|---|---|
| 1. Baseline and contracts | License review, upstream pins, USB-only adapter, format-aware payload contract, profile-bound media/output rules, geometry conventions. | Read-only discovery and manually approved target-printer tests; record actual results and operating limits for each promoted profile. |
| 2. Editor and reusable stickers | Original-byte upload, lossless working assets, masks/outlines, shared sticker definitions, project revisions, resolved contour contract and client exports. | Source files remain intact; copies share content; no detach; SVG/DXF preserve physical geometry and STL is a validated positive-thickness solid. |
| 3. Sheet building and extraction | Repeat/grid/fill/exact-quantity modes, pagination, full-sheet extraction, split/group, preserve/repack/library choices. | Requested sizes/counts stay correct; extracted designs can be tiled immediately. |
| 4. Rendering and preflight | Immutable working master, exact profile-bound device image, optional PLT, manifest, exact-raster preview, inverse PLT overlay, and per-format limits. | Lossless masters round-trip exactly; JPEG output has the required raster, color mode, DPI metadata, quality and byte bound; print/cut transforms agree; no silent format or quality fallback occurs after review. |
| 5. Production service | Exclusive worker, SQLite queue/history and migrations, API/SSE, per-sheet events, exact reprints, retention/backup, and uncertain-outcome handling. | History survives restarts; duplicate requests, recovery and backup restore never silently repeat physical sheets; saved-file reprints preserve artifact hashes. |
| 6. Optional improvements | Local model extraction, advanced nesting and editing. | Each enhancement passes the same reproducible render and profile-bound USB job contracts; no additional transport or sender. |

Hardware access must not be required to develop the editor, extraction, layout, or renderer. Recorded protocol fixtures and a nonprinting device simulator keep those paths testable without consuming media.

## 16. Essential acceptance cases

The test set should cover a transparent single sticker, opaque white artwork, a flat white sheet, a colored-background sheet, touching designs, disconnected text accents, and nested holes. Every extraction result must remain manually correctable.

For layouts, verify fixed physical size, exact quantities across pages, final partial sheets, shared definition edits, pinned placements, rotations, and overflow without silent scaling. An artwork/mask/outline edit must update every placement of that sticker in the new project revision. Assert that the UI and API provide no detach operation and reject per-copy content overrides. Prior approved renders must remain unchanged. Changing the shared border must trigger collision revalidation for every affected placement.

For rendering, compare asymmetric art/cut targets, edge guards, shared transforms, pressure-profile selection, format-specific byte limits, inverse-serialized previews, and stable outputs from frozen revisions. Assert exact equality for lossless working masters and verify each JPEG's final dimensions, RGB mode, DPI metadata, configured quality, byte bound, manifest provenance, and deterministic output. Keep source hashes intact, test alpha compositing and high-detail sheets, reject oversize output clearly, and block unapproved formats. Manual edits must not disappear during render, and the worker must send the reviewed bytes without transcoding.

For device handling, simulate rejected creation, missing ACKs, partial status responses, paper exhaustion, explicit errors, status loss after activity, restart between job creation and ID persistence, and a duplicate HTTP submission. The acceptable result of uncertainty is a visible hold requiring reconciliation—not an automatic extra print.

For deployment, verify unprivileged USB access, authorization of print actions, malicious/oversized upload rejection, artifact persistence, browser reconnection, and operation without optional models or internet access. Verify that Bluetooth libraries, services, hardware, browser permissions, and configuration are not required.

History tests must cover a successful multi-sheet batch, mixed outcomes, explicit cancellation before device access, uncertainty after activity, exact selected-sheet reprints, and simulator/hardware separation. Editing or archiving a source project must not change its past previews or reprint bytes. Verify atomic state/event commits, idempotency conflicts, migration order, low disk space, incomplete file publication, referenced-artifact protection, and complete database-plus-artifact backup/restore. A stale restored queue must remain held, even when it appears to contain unstarted work.

Client export tests cover section 6.1: chosen physical size, correct orientation, enabled holes, valid solids, historical snapshots, all-repeat selection semantics, no implicit simplification, network-disabled conversion after loading, and no history mutation or hardware access. Client export does not depend on successful printer commissioning.

## 17. Target product definition

A user can upload one image, prepare its outline, and make a repeat sheet at a chosen size. They can instead upload an assorted sheet, remove or retain the print background independently of tracing, correct the detected sticker regions, preserve the original positions or repack them, and save any extracted design for later use. They can review exact frozen artifacts, queue a combined USB job through an evidence-backed media/output profile, and see an honest outcome without duplicate printing caused by retries. Repeated copies remain linked; uploads and editable working assets remain byte-preserved or lossless, while the printer receives the exact reviewed profile-bound JPEG. The browser can save a selected outline, selection or active sheet as SVG/DXF and create an STL solid of chosen thickness, without server-side conversion or a print job. Server-side SQLite history survives browser/server restarts, shows per-sheet evidence and partial outcomes, and can create an explicit new reprint from retained approved artifacts without rerendering.

Bluetooth support, detached instances, and silent output fallback are excluded. Cloud accounts, a proprietary cut-generator dependency, general-purpose cut-only operation, aggressive through-backing cuts, and a distributed multi-service deployment are not prerequisites. Each production USB profile requires recorded physical validation across its declared media, raster, format, and payload limits.


## Sources

[S01]: https://github.com/popcornhax/PixCut-App
[S02]: https://github.com/popcornhax/PixCut-App/blob/main/pixcut/image_to_cut.py
[S03]: https://github.com/popcornhax/PixCut-App/blob/main/pixcut/framing.py
[S04]: https://github.com/popcornhax/PixCut-App/blob/main/pixcut/orchestrator.py
[S05]: https://github.com/popcornhax/PixCut-App/blob/main/docs/pixcut-usb-protocol.md
[S06]: https://github.com/eastbaymakersclub/pixcut-s1
[S07]: https://github.com/eastbaymakersclub/pixcut-s1/blob/main/pixcut_usb.py
[S09]: https://github.com/eastbaymakersclub/pixcut-s1/blob/main/pixcut_s1_profile.json
[S10]: https://github.com/honeymaro/pixcut
[S11]: https://github.com/honeymaro/pixcut/blob/main/src/plt.ts
[S12]: https://github.com/honeymaro/pixcut/blob/main/src/units.ts
[S13]: https://github.com/honeymaro/pixcut/blob/main/src/transform.ts
[S14]: https://docs.opencv.org/4.13.0/d7/d1b/group__imgproc__misc.html
[S15]: https://github.com/danielgatis/rembg
[S16]: https://github.com/popcornhax/PixCut-App/blob/main/LICENSE
[S17]: https://github.com/honeymaro/pixcut/blob/main/LICENSE
[S18]: https://sqlite.org/wal.html
[S19]: https://sqlite.org/pragma.html
[S20]: https://sqlite.org/backup.html
[S21]: https://docs.python.org/3/library/sqlite3.html
[S22]: https://www.w3.org/TR/SVG11/coords.html
[S23]: https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-DXF/files/GUID-748FC305-F3F2-4F74-825A-61F04D757A50.htm
[S24]: https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-DXF/files/GUID-A85E8E67-27CD-4C59-BE61-4DC9FADBE74A.htm
[S25]: https://threejs.org/docs/pages/STLExporter.html
[S26]: https://threejs.org/docs/pages/Shape.html
[S27]: https://threejs.org/docs/pages/ExtrudeGeometry.html

S01 — PixCut-App overview and documented workflow.  
S02 — Python image/cut processing implementation.  
S03 — PixCut-App USB framing implementation.  
S04 — PixCut-App job orchestration.  
S05 — Community USB protocol observations and cautions.  
S06 — East Bay reverse-engineering reports and workflow notes.  
S07 — East Bay USB implementation.  
S09 — Explicitly local calibration example.  
S10 — Honeymaro toolchain capabilities and documented defaults.  
S11 — PLT serializer/parser implementation.  
S12 — Nominal plotter units and sheet geometry.  
S13 — Source-to-plotter transformation implementation.  
S14 — OpenCV image segmentation primitives.  
S15 — Optional local background-removal component.  
S16/S17 — Explicit upstream license files.

S18 — SQLite WAL concurrency, local-filesystem requirements, and the documented WAL-reset fix; checked September 10, 2026.  
S19 — SQLite connection settings, durability and foreign-key pragmas; checked September 10, 2026.  
S20 — SQLite online backup API; checked September 10, 2026.  
S21 — Python sqlite3 runtime-version reporting and backup interface; checked September 10, 2026.  

S22 — W3C SVG physical units and coordinate/viewBox conventions; checked September 10, 2026.  
S23/S24 — Autodesk DXF closed-polyline entities, header version and millimeter units; checked September 10, 2026.  
S25 — Three.js STL export, binary option, geometry-only representation and absent standardized units; checked September 10, 2026.  
S26/S27 — Three.js shape holes, winding and extrusion settings, including bevel defaults; checked September 10, 2026.  
