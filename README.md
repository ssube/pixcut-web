# PixCut Studio

[![CI](https://github.com/ssube/pixcut-web/actions/workflows/ci.yml/badge.svg)](https://github.com/ssube/pixcut-web/actions/workflows/ci.yml)

A local-first photo and sticker studio built around a documented [product and service design](DESIGN.md). Photo editing works with or without a printer, while Printer mode provides one-copy 4×6 photo and 4×7 sticker workflows through the shared queue.

The browser opens in Photo and provides non-destructive free cropping, automatic/manual orientation, Fill/Contain framing, presets, nine balancing and grain controls, debounced backend previews, exact JPEG proofs, and revisioned saves. Sticker provides shared polygon sheets, exact render artifacts, simulated jobs, durable history, reprints, and local SVG/DXF/STL exports with optional magnet pockets.

## Feature tour

### Photo

- Free rectangular crop with drag handles, one-percent move/resize buttons, reset, and automatic, portrait, or landscape output.
- Fill framing trims around the chosen crop center; Contain keeps the whole crop and adds white paper borders without distortion.
- Original, Auto Balance, Vivid, Warm, Cool, and Black & White presets, plus exposure, contrast, highlights, shadows, temperature, tint, saturation, sharpening, and film-grain sliders.
- None, Warm negative, Faded print, Vivid slide, and Cool chrome film looks. Grain is deterministic, so the same project revision always produces the same proof.
- Single 4×6 prints, four-photo 2×3 yearbook grids, six-photo 1½×2 mini yearbook grids, and two 2×6 photobooth strips per sheet.
- A clickable **Show original / Show edits** comparison, explicit Save, and **Review print**, which saves first and creates the authoritative 1200×1800 RGB JPEG.
- A quality slider from 60–95 for detailed photographs that would otherwise exceed the printer's 1 MiB JPEG limit. The exact proof reports its encoded size and quality.

### Sticker

- PNG, JPEG, and WebP import with automatic alpha or edge-connected-white component extraction. Multi-design artwork becomes independently cuttable stickers while preserving its source arrangement.
- Fill-sheet, fixed-grid, and exact-quantity layouts with locked artwork proportions, physical millimeter sizing, configurable gaps, and page overflow.
- Multi-design imports preserve their original full-sheet scale and positions. **Restore imported size** repairs projects saved by the older shrinking importer; **Unlock layout** allows replacing the source arrangement with Fill sheet, Fixed grid, or Choose quantity.
- A physical **White border** offset and **Smooth tight corners** control. Extracted sheets can shrink uniformly to keep the finished cut envelopes separated.
- Optional cut-line overlay, fit-to-window or 50–300% preview zoom, and an advanced normalized-coordinate editor. Advanced coordinates are displayed to three decimal places.
- Live sheet-use estimates show sticker area, unused paper, square inches, and approximate cost at $0.60 per 4×7 sheet.
- Local SVG, DXF, and solid STL exports. STL supports a circular magnet pocket centered on the non-sticker back face; a pocket at least as deep as the part creates a through-hole.

### Projects, printing, and History

- Photo, Sticker, and History tabs share one revisioned project store. Photo opens by default, each library is filtered to its own project type, and its trash button removes the editable project without removing immutable print history.
- The printer profiles are named `photo-4x6-jpeg` and `sticker-4x7-jpeg`. Both physical workflows require one reviewed page, one copy, and confirmation that the matching paper is loaded immediately before submission.
- History shows recent jobs, per-sheet progress, exact saved previews, explicit reprints, cancellation before device intent, and **Load older prints** pagination. Its badge counts held, queued, creating, transferring, and processing sheets; `!` replaces the count when printer recovery needs attention.
- A job started while the printer is unavailable becomes **Needs attention** without creating device intent. Turn on and load the printer, then use **Resume print**; the app asks for paper confirmation again.
- Printer readiness combines USB discovery with a worker heartbeat. New physical jobs are disabled when the printer is missing, the worker is offline, or an uncertain job is blocking dispatch. Temporary browser/API loss is shown as **Connection lost — trying again** with bounded, non-overlapping polling.
- Simulator mode keeps Photo editing and exact proofs available, and saves Sticker previews to History without physical output.

### Everyday usability

- Unsaved Photo settings and advanced cut-path changes are protected when switching tabs, opening another project, importing another file, or closing/reloading the page.
- Selected tabs and choices expose accessible state, status changes use live regions, before/after works by keyboard or touch, crop controls have larger touch targets, and focus remains visible. Missing thumbnails use a readable fallback rather than a broken image.
- The responsive layout supports phone testing over the local network. Pumpkin spice mode is remembered in the browser, and the project includes favicon and home-screen artwork.

## Run locally

Prerequisites: Linux, a dynamically linked system Python 3.12 (tested with 3.12.13), uv, Node 20.20.2, npm 10.8.2, and a C compiler. Use a local filesystem for application data. Python dependencies are locked in `uv.lock`; browser dependencies in `web/package-lock.json`.

```bash
# Use the system Python: some standalone Python distributions statically embed
# an older SQLite library and cannot load the pinned runtime below.
uv sync --python /usr/bin/python3.12 --extra dev --locked
.venv/bin/python scripts/build_sqlite.py
npm --prefix web ci
npm --prefix web run build
.venv/bin/pixcut migrate
.venv/bin/pixcut serve
```

### Linux USB permissions

Install the included udev rule once on the printer host:

```bash
sudo bash scripts/install-udev-rule.sh
```

The source rule is [`config/udev/70-pixcut-s1.rules`](config/udev/70-pixcut-s1.rules). It matches the PixCut S1's stable USB identity (`302c:3101`), grants the `plugdev` group read/write access, and adds the desktop `uaccess` tag. The installer copies it to `/etc/udev/rules.d/`, reloads udev, and reapplies it to an attached printer.

Check that the account running the hardware worker belongs to `plugdev`:

```bash
id -nG
sudo usermod -aG plugdev "$USER"  # only if plugdev is missing above
```

Sign out and back in after adding the group; starting a new terminal is not enough. Then reconnect or power-cycle the printer and verify access:

```bash
.venv/bin/pixcut self-test
ls -l /dev/bus/usb/<bus>/<device>
```

The numeric bus/device path can change whenever the printer reconnects. That is expected: udev reapplies permission by vendor/product identity, and the worker discovers the current address for each job. Do not hard-code a device such as `/dev/bus/usb/001/027`.

If `self-test` still reports that the device is not writable, confirm that `udevadm` and the `plugdev` group exist, reinstall the rule, reconnect the printer, and restart the worker so it opens a fresh USB session. To remove the rule later, delete `/etc/udev/rules.d/70-pixcut-s1.rules`, run `sudo udevadm control --reload-rules`, and reconnect the printer.

Open <http://127.0.0.1:8000>. Run the independent simulator worker in a second terminal:

```bash
.venv/bin/pixcut worker
# Or process at most one queued sheet:
.venv/bin/pixcut worker --once
```

For frontend development, keep the API running and use `npm --prefix web run dev`. Vite proxies `/api` to the loopback API on port 8000. A browser refresh never creates a printer job. The worker must be running for queued sheets to progress; API and browser restarts do not restart the worker.

For temporary phone testing on a trusted local network, bind Uvicorn to all interfaces and allow only the machine's exact LAN IP or hostname:

```bash
PIXCUT_TRUSTED_HOSTS=192.168.1.50 \
  .venv/bin/uvicorn pixcut.api:create_app --factory --host 0.0.0.0 --port 8000
```

Replace `192.168.1.50` with the host's current LAN address, then open `http://<address>:8000` on the phone. The default `pixcut serve` command remains loopback-only.

This checkout uses `.pixcut/` by default. Set `PIXCUT_DATA_DIR` to the same absolute directory for migration, API, and worker processes to use another location. It contains `studio.sqlite3`, its WAL sidecars, the worker lock, and `artifacts/`. Never delete this directory as part of an upgrade. Automatic retention/purge and complete backup/restore tooling are not implemented yet.

The pinned SQLite build downloads the official 3.51.3 amalgamation, verifies its SHA-256, and builds `.runtime/libsqlite3.so.0`. An existing source archive can be passed as `scripts/build_sqlite.py /path/to/sqlite-amalgamation-3510300.zip`. Startup checks the actual Python-loaded SQLite version and rejects versions below 3.51.3. This implements the design's [SQLite WAL-reset fix requirement](https://sqlite.org/wal.html#walreset). Tests check WAL, FULL synchronization, foreign keys, migration compatibility, and restart persistence. The build is Linux-specific; binaries are generated locally and not committed.

## Read-only USB self-test

```bash
.venv/bin/pixcut self-test
.venv/bin/pixcut self-test --json
.venv/bin/pixcut self-test --all
```

Scans Linux `/sys/bus/usb/devices` for every `302c:3101` PixCut S1 and lists manufacturer/product/serial, USB port and device node, per-interface bound drivers and endpoint addresses. It compares interfaces 2/3 with the design's observed command/data endpoints. `--all` also lists other USB devices to help investigate a missing match. JSON reports use schema version `1.0`.

The scan only reads OS enumeration metadata and checks device-node visibility and read/write permissions for the invoking process. It does not open a USB device, claim/detach an interface, reset the printer, send commands, or keep the printer awake. A missing interface driver is reported as `unbound`, not treated as a discovery failure. Permission checks are observations, not proof that a future USB transfer will work; run on the host for accurate visibility if a container/sandbox hides `/dev/bus/usb`.

Exit codes: `0` when at least one PixCut matches, `1` when none match, `2` when the sysfs scan is unavailable. Missing optional strings and disconnect races produce partial results/warnings. The self-test needs neither a database migration nor the application dependencies: `PYTHONPATH=src python3 -m pixcut.cli self-test` also works. This read-only command cannot submit a print.

## Read-only identity/status probe

```bash
uv sync --python /usr/bin/python3.12 --extra dev --extra usb --locked
.venv/bin/pixcut probe
.venv/bin/pixcut probe --json
```

This explicit hardware diagnostic requires libusb on the Linux host and read/write access to the USB device node. It requires exactly one attached PixCut, takes the shared worker lock, verifies the active command-interface bulk endpoints, claims only interface 2, and sends three fixed `get-prop` queries for identity, serial/media, and status. It releases its claim afterward. Stop the simulator worker before probing if it holds the worker lock.

Unlike `self-test`, this command communicates with the printer. It never detaches drivers, sets a USB configuration, resets the device, clears endpoint halts, touches the data interface, creates jobs, or uploads artifacts. Replies are bounded, incrementally framed, and correlated by request ID. Queries are not resent after timeout. `--json` retains raw replies and the pinned protocol reference; exit status is 0 for correlated replies to all queries and 1 for probe failure. A successful probe confirms communication, not printing or media compatibility.

The first host probe on September 10, 2026 (Chicago time) returned firmware `1.0.34_0073`, hardware `A`, model `DHP700`, SKU `DHP700W#US2`, media code `5009`, and raw status `["30", "2000", "::0"]`. State 30 is described as sleep in the community reference; the media code is retained without assigning a physical stock type.

## 4×6 photo printing

The `photo-4x6-jpeg` profile is a bounded one-copy, print-only hardware path. The browser prepares a 1200×1800 RGB JPEG below 1 MiB at the selected quality, with a thumbnail, empty cut geometry, empty overlay, source/revision provenance, and adjustment pipeline version. Queueing requires review and explicit 4×6 photo-paper confirmation. The worker freezes the request before device intent, uploads 10,215-byte frames through 1,024-byte bulk writes, waits for every ACK, and never sends a cut command or automatically retries an ambiguous attempt.

The photo workflow prints a 1200×1800 RGB JPEG at quality 60–95 using the 4×6 photo-paper profile.

## 4×7 sticker printing

The `sticker-4x7-jpeg` profile promotes one reviewed preview page into a frozen one-copy printer job. It converts outer polygon contours from canonical millimeter geometry to the device's rotated native PLT coordinates with `KP42`, adds a three-point exterior lead-in and lead-out aligned to each contour's seam, encodes a quality-92 JPEG below 1 MiB, creates a type-600 combo job, and uploads PLT bytes before JPEG bytes through the printer's USB framing. The lead-out passes through the seam in the incoming direction so the closed cut peels cleanly. Holes remain unsupported.

Printer jobs contain one reviewed 4×7 page and one copy. Outer polygon contours are supported; holes and multiple-page printer jobs are not.

## Cut-only protocol research

The printer protocol has a standalone `cut-job` message shape, but cutter-only operation is not a production feature yet. One community implementation exposes that request without claiming it works, while another reverse-engineering report says the DHP700 accepts and then silently discards a cut job without a print document. The private commissioning harness tests only those two evidence-based request shapes; it never uploads an image and is not reachable from the web API. See the [East Bay implementation](https://github.com/eastbaymakersclub/pixcut-s1/blob/main/pixcut_usb.py) and [PixCut-App protocol notes](https://github.com/popcornhax/PixCut-App/blob/main/docs/pixcut-usb-protocol.md#51-combo-job).

Generate and inspect a dry-run record first. This stores a centered 20 mm square, PLT, preview, manifest, declaration, and held job without opening USB or moving the printer:

```bash
PIXCUT_EXECUTION_MODE=hardware .venv/bin/python scripts/cut-only-test.py test \
  --variant standalone-zero \
  --sheet-kind fresh \
  --idempotency-key cut-only-zero-fresh-try1
```

Stop the normal hardware worker before a physical test so the harness can take the exclusive device lock. Add `--execute`, review the printed declaration, and type the exact phrase it requests. A physical attempt is committed before the standalone command is sent and is never retried. The harness uploads `cut.plt` only, checks the printer's `printed` counter before and after, recognizes an all-zero discarded job, and makes unexpected print activity or an ambiguous result explicit.

Afterward, retain the physical observation in the same history record:

```bash
.venv/bin/python scripts/cut-only-test.py observe <job-id> \
  --cutter-moved yes \
  --print-passes no \
  --visible-overcoat no \
  --jam no \
  --through-backing no \
  --notes "Centered square cut cleanly"
```

Do not proceed to reloaded media unless a fresh-sheet trial moves the cutter without Y/M/C/overcoat passes or a changed print counter. A later printed-sheet trial must name the completed physical sticker job whose immutable PLT will be reused:

```bash
PIXCUT_EXECUTION_MODE=hardware .venv/bin/python scripts/cut-only-test.py test \
  --variant standalone-zero \
  --sheet-kind printed \
  --source-job-id <completed-sticker-job-id> \
  --idempotency-key cut-only-zero-printed-try1
```

For a conservative printed-sheet commissioning pass, add `--outline artwork-bounds`. That substitutes one rectangle exactly 2 mm beyond the source artwork bounds while retaining source-job lineage. It is intended only to prove refeed registration and cutter movement; glitter-laminate recuts reject this option and require the exact individual source contours.

The target craft workflow is: print and cut the sticker sheet normally, apply a thin self-adhesive glitter laminate across the completed sheet, then reload it for one cut-only pass along the exact retained contours. The harness preserves the source PLT byte-for-byte and verifies the normal `KP42` knife pressure before sending it; it does not increase pressure or add another pass. Prepare that validation record with:

```bash
PIXCUT_EXECUTION_MODE=hardware .venv/bin/python scripts/cut-only-test.py test \
  --variant standalone-zero \
  --sheet-kind glitter-laminate \
  --source-job-id <completed-sticker-job-id> \
  --laminate-description "Self-adhesive glitter vinyl, brand and thickness" \
  --idempotency-key cut-only-zero-glitter-try1
```

Validate in order: fresh stock, a previously printed unmodified sheet, then a fully adhered adhesive glitter-laminate sheet. Do not test loose glitter, wet coatings, curled media, lifted laminate edges, trapped bubbles, or shedding surfaces. Record `--max-offset-mm` for every reloaded-sheet registration trial. Production exposure requires three reload trials no worse than 0.5 mm, no printing or overcoat, no counter change, no jam, and no through-cut. Until those gates pass, there is intentionally no **Cut again** button, `cut_only` public job mode, or production cut-only printer profile.

The first `standalone-zero` physical trial on September 11, 2026 used device job `27` and a 134-byte `KP42` artwork-bounds PLT derived from the completed faces sheet. The printer LED changed from white to purple and back to white, acknowledging the job, but the sheet never moved and there was no cut, print, or overcoat. All PLT bytes were acknowledged before the device emitted `-8013 job timeout`. The attempt remains a recorded negative result and was not retried.

This distinction matters: a genuine cut-only path would avoid the dye ribbon, so the adhesive glitter laminate would not contact a print or overcoat pass. Mechanical refeeding, added thickness, edge adhesion, and repeat registration still require physical evidence. Liene's current [user manual](https://manuals.plus/m/0d78f872f77554e3287b04ecc560f0ea5645e043e6af22fddd8bf39e1d06d03d_optim.pdf) advises against reusing sheets in the normal print-and-cut workflow; this private harness exists to validate the isolated cutter path before exposing it in the app.

Physical double-printing is not supported. The protocol's `copies` value produces separate sheets, and refeeding a laminated sheet through the dye-transfer path is outside the supported workflow. A double-exposure appearance should be composited into one raster and printed once; that editor feature is not implemented yet.

To run the browser with the real printer profile, start both processes with the explicit mode variable. The server prepares reviewed jobs but never opens USB; the exclusive hardware worker performs the device operation and records its progress. Real mode accepts exactly one page and one copy per confirmation.

```bash
PIXCUT_EXECUTION_MODE=hardware .venv/bin/pixcut serve
PIXCUT_EXECUTION_MODE=hardware .venv/bin/pixcut hardware-worker
```

A reviewed browser job enters the hardware queue immediately. The app accepts it only while both the printer and its worker are ready. If the worker cannot reach the printer before creating device intent, it safely moves that sheet to **held** and keeps running. After turning on the printer and loading the already-confirmed media, use **Resume print** in History; the API requeues only a held hardware sheet with no prior attempt. Once device intent exists, automatic or manual replay remains prohibited. The worker updates a database heartbeat while idle, transferring, printing, or blocked, so the browser can distinguish a missing printer from a stopped worker.

If a transfer fails with `unsupported cmd` or device timeout `-8013`, the worker records a power-cycle recovery action, stays alive in a blocked state, and does not retry the affected sheet. The web app replaces the History queue badge with an accessible `!` marker and shows the recovery steps in History. Fully power-cycle the printer by holding its power button until the light turns off, then holding it again until the light turns on. Confirm the restart, then choose **It printed** or **Nothing printed** to record the observed result and clear the warning without resubmitting the sheet. The next dispatched job performs fresh USB discovery and opens a new device session, interface claim, and status check; no pre-reboot USB handle is reused. This recovery cleared a persistent transfer failure on the tested DHP700; merely seeing the printer online was not sufficient.

Restarting the hardware-worker process after a printer power cycle is also safe and provides a completely clean process boundary. An unresolved uncertain sheet remains blocked in SQLite across that restart, so starting the worker cannot accidentally replay it. Stop the old worker, start `.venv/bin/pixcut hardware-worker` again with the same `PIXCUT_DATA_DIR` and `PIXCUT_EXECUTION_MODE=hardware`, then finish the guarded History resolution.

## Photo workflow

1. Open **Photo** and import a PNG, JPEG, or WebP. The original and normalized source remain unchanged.
2. Move or resize the free crop, then choose a full 4×6, four 2×3 yearbook photos, six centered 1½×2 yearbook photos, or two matching 2×6 photobooth strips. Repeat layouts use a portrait sheet; each photobooth strip contains four repeated 2×1½ frames.
3. Choose Fill or white-background Contain framing, then apply a preset or manual adjustments. Single photos also support automatic/portrait/landscape orientation.
4. Choose **Show original** to compare, then **Show edits** to return to the adjusted proof. The toggle works with mouse, keyboard, or touch. Changes stay in the editor until you save or review the print.
5. Leave **Print quality** at 95 for maximum detail. If the photo is too large for the printer, lower it and review again; this changes only the final JPEG, not the crop or color edits.
6. Save explicitly, or choose **Review print** to save first and prepare the 4×6 image. The review card shows the exact file size and selected quality.
7. In Printer mode, review the page, confirm 4×6 photo paper, and print one copy. Simulator mode keeps editing and previewing available without sending anything to the printer.

The analog controls follow established open-source darkroom practice: film grain is deterministic Gaussian texture applied to luminance rather than independent RGB noise, while the color choices combine global tone curves, color casts, and saturation. This mirrors darktable's [luminance-channel grain model](https://docs.darktable.org/usermanual/4.6/en/module-reference/processing-modules/grain/) and the global-transform constraints documented for RawTherapee [film-simulation CLUTs](https://rawpedia.rawtherapee.com/Film_Emulation). The generic look names describe this implementation; they do not claim to reproduce a branded film stock.

### Photo quality test matrix

Generate reproducible JPEG quality/raster points without touching the printer:

```bash
.venv/bin/python scripts/photo-test-matrix.py photo.png \
  --output /tmp/pixcut-photo-matrix \
  --sizes 600x900,900x1350,1200x1800 \
  --qualities 75,85,90,95
```

The output contains one JPEG per encodable point plus `matrix.json` and `matrix.csv` with dimensions, megapixels, quality, bytes, percentage of its bounded test limit, SHA-256, and eligibility. Rows are ordered by payload size, largest first. The normal Photo profile remains capped at 1 MiB; one-point JPEG tests are capped at 4 MiB. Sizes must remain 2:3 portrait rasters between 300×450 and 2400×3600; quality must be 50–100.

To compare realistic lossless PNG payloads at the printer's fixed 1200×1800 (300 DPI) raster, select PNG compression strategies:

```bash
.venv/bin/python scripts/photo-test-matrix.py photo.png \
  --output /tmp/pixcut-photo-png-matrix \
  --formats png \
  --sizes 1200x1800 \
  --png-strategies filtered,huffman,rle
```

PNG rows are marked as under 2 MiB, within 2–4 MiB, or over the 4 MiB test cap. Every strategy is a standards-compliant, pixel-identical lossless encoding; the tool does not pad files or change their dimensions to hit a target byte count. This is a device capability test only; the normal Photo workflow remains the reviewed 1200×1800 JPEG profile.

Physical testing is deliberately one point per invocation. Stop the normal hardware worker so the matrix script can own its exclusive lock, then select a generated point and supply both confirmations. The script still requires typing the exact point before it creates device intent, and an interrupted or ambiguous attempt is never retried. A PNG point is written as `1200x1800@png-huffman` and must fall within 2–4 MiB.

```bash
PIXCUT_EXECUTION_MODE=hardware .venv/bin/python scripts/photo-test-matrix.py photo.png \
  --output /tmp/pixcut-photo-matrix \
  --execute-point 900x1350@90 \
  --idempotency-key photo-900x1350-q90-try1 \
  --confirm-4x6-photo-paper \
  --confirm-payload-test
```

## Sticker workflow

1. Import a PNG, JPEG, or WebP. Original bytes are retained unchanged; EXIF orientation and embedded ICC conversion are recorded in the normalized PNG asset. Meaningful alpha or an edge-connected white background is split into independent sticker regions with traced outer contours.
2. Choose artwork-envelope dimensions, then fill a sheet, choose a grid, or request an exact quantity. Width and height remain linked in the UI. Quantity overflow creates additional pages without shrinking the artwork.
3. Set the **White border** in millimeters and use **Smooth tight corners** when a design has tiny or narrow details. Apply the border settings to every design on the sheet. The optional fit control shrinks stickers slightly if their borders would overlap.
4. If needed, open **Advanced cut path** to edit the selected design's normalized cut points. The editor shows each x/y coordinate to three decimal places, and changes apply to every copy of that design.
5. Choose **Review print** and check every sheet's artwork and cut lines.
6. In Printer mode, confirm the loaded sticker paper and print one copy. In Simulator mode, save the preview to History without sending it to a printer.
7. Open history, review a saved job, and explicitly queue its exact reprint. Artifacts and profile hashes remain unchanged even after editing the source project.
8. Export the active page as SVG/DXF or a filled STL with a chosen thickness. The default thickness is 2 mm. STL exports can add a circular magnet pocket centered on the back/bottom face of every part while leaving the sticker-facing top unchanged; choose its diameter and depth in millimeters. A pocket depth equal to or greater than the part thickness creates a through-hole. Exports preserve page positions and all repeats. SVG uses Y-down; DXF/STL reflect to Y-up. STL has no standard units metadata: import it as millimeters. Exporting does not contact the API or create history.

## History and recovery workflow

1. Open **History** to see the newest 25 jobs; choose **Load older prints** to page backward without losing live updates for recent work.
2. Read the job and per-sheet labels: **Waiting**, **Starting**, **Sending**, **Printing**, **Finished**, **Problem**, **Cancelled**, **Needs attention**, or **Check printer**.
3. For a held job that never reached the printer, turn the printer on, load the paper named in the prompt, and choose **Resume print**. Resume is unavailable once a device attempt exists.
4. For a transfer whose result is uncertain, inspect the physical sheet before doing anything else. The worker blocks later hardware dispatch and never retries automatically.
5. When History asks for a printer restart, fully power it off and on, check **I restarted the printer and its light is back on**, then record **It printed** or **Nothing printed**. Recording the outcome clears the block but never creates another print.
6. Use **Open print** for eligible Sticker history and explicitly confirm any reprint. Historical files and profile settings stay pinned even if the editable project has changed.

## Contracts and behavior

- API: `/api/v1`; interactive request documentation at `/docs`; committed [OpenAPI](contracts/openapi.json), [geometry schema](contracts/cut-geometry.schema.json), and [geometry fixture](contracts/cut-geometry-v1.json). Frontend geometry types are generated from OpenAPI.
- Photo and sticker project revisions are immutable. `GET /projects?kind=photo|sticker` filters the shared revision table and excludes old commissioning records; projects without a kind are treated as legacy stickers. A sticker project binds one revision per sticker ID. `POST /projects/{id}/restore-source-layout` reconstructs an extracted sheet from its original pixel positions, while `POST /projects/{id}/cut-policy` atomically revises every bound sticker and the project after validating adjusted geometry.
- Render generation never submits a job. The initial bounded renderer completes in a request thread, then returns `202` with a persisted completed operation ID; asynchronous processing and crash recovery for render operations remain future work.
- Every sticker simulator render retains PNG, canonical geometry, PLT, inverse-serialized SVG overlay, PNG navigation thumbnail, and a manifest for each page. Exact photo renders retain the selected quality-60–95 JPEG, thumbnail, empty geometry/overlay, and provenance manifest. Content-addressed files are flushed and atomically published before database references are committed; the photo encoder has no format fallback.
- The preview canvas is 101.6 × 177.8 mm at 1200 × 2100 pixels, with a 2 mm guard, 40 units/mm PLT convention, and 32 MiB payload ceiling.
- Job acceptance reserves a unique idempotency key in the same transaction as all sheet rows and the initial event. Repeating the same request returns the original job; changing its payload with the same key returns `409`. Sheet order is ascending page index within each copy of the set. Reprints selected by sheet rows preserve the original row order, including intentionally selected repeated copies.
- A process-level advisory lock gives one worker ownership. Create intent is durable before the simulated side effect. Recovery holds an interrupted attempt as uncertain and stops all dispatch until explicit operator reconciliation. A durable worker heartbeat reports starting, idle, working, blocked, stopped, and stale/offline states. Fault injection supports creation rejection, missing ACK, status loss, and interruption after intent. No attempt is automatically repeated.
- `POST /jobs/{id}/cancel` cancels only queued sheets; active sheets continue. `POST /jobs/{id}/resume` requeues exactly one held hardware sheet only when it has no device attempt. `POST /jobs/{id}/resolutions` accepts a note, uncertain sheet ID, expected latest event ID, and outcome. Printer power-cycle recoveries also require `printer_restarted: true`. It appends operator evidence without deleting the original uncertainty and never creates another print. History provides the guarded resolution controls.
- `GET /events` supports durable event IDs and `Last-Event-ID`; the browser uses polling for queue/history updates. History pagination is implemented, and `GET /jobs` reports active sheet `queue_length` for the navigation badge.

## Access and limits

The supplied command binds to loopback and only accepts loopback/test hosts. For temporary LAN testing, start Uvicorn with `--host 0.0.0.0` and list the exact LAN hostname or IP in the comma-separated `PIXCUT_TRUSTED_HOSTS` variable; do not use a wildcard. An optional `PIXCUT_API_TOKEN` protects API requests with a bearer token; enter it as the **Access code** under **Connection settings** in Sticker. Browser mutations must use the same origin. Upload requests must have Content-Length and fit within 26 MiB including multipart overhead; each image is limited to 25 MiB and 20 million decoded pixels. Animated and vector uploads are rejected. The default disk reserve is 128 MiB, configurable with `PIXCUT_MIN_FREE_BYTES`.

This is a local development access model. The trusted-host override supports temporary LAN testing, not production LAN deployment: administrator sessions, revocable automation credentials, HTTPS integration, and full authorization/CSRF commissioning remain unimplemented. USB access is isolated in explicit CLI workers; the server process does not open the printer.

## Verify

```bash
.venv/bin/pytest -q
npm --prefix web test
npm --prefix web run build

# Optional real-browser test; invoking Python needs Pillow + Playwright + Chromium.
python tests/browser_smoke.py

# Regenerate request/geometry contracts after API changes.
.venv/bin/python scripts/generate_contracts.py
web/node_modules/.bin/openapi-typescript contracts/openapi.json -o web/src/api.generated.ts
```

The browser smoke test uses its own temporary database and loopback server. It exercises Photo editing/save/exact proof, before/after and fine crop controls, unsaved-change protection, Sticker upload/layout/render, simulated completion, page reload, History reprint, network-blocked SVG/DXF/STL downloads, serious/critical axe accessibility checks, and mobile overflow. Screenshots go to `/tmp/pixcut-studio-desktop.png` and `/tmp/pixcut-studio-mobile.png`. No hardware is accessed by any test.

## Remaining design work

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the implementation boundary and next work packages. Uploads now split meaningful alpha regions or foreground regions separated from an edge-connected white background into independent sticker definitions and contour paths. The source arrangement is preserved, with a small uniform reduction when rectangular artwork envelopes would overlap. Saved projects can be deleted from the Studio sidebar; immutable render and print history remains available.

The printer workflow supports 4×6 photo output and 4×7 outer-contour sticker sheets. Hole cutting remains future work.

## License

PixCut Studio is released under the [MIT License](LICENSE), matching the licensed upstream protocol references. Third-party projects and documentation cited in the design retain their own copyright and license terms.
