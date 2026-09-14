# Implementation status

This document tracks the boundary between working behavior and remaining design work. The integrated Photo, Sticker, History, simulator, and USB-worker paths are covered by API and real-browser tests. The target product definition in section 17 of [DESIGN.md](DESIGN.md) is not yet fully implemented.

## Implemented

| Area | Working behavior |
| --- | --- |
| Discovery | Read-only Linux USB self-test CLI: all matching PixCut devices, interface drivers/endpoints, process-visible device-node access, text/JSON output; no USB commands or device claims in enumeration; separate explicit identity/status probe with fixed get-prop queries on interface 2; persistent `302c:3101` udev permissions for `plugdev` and desktop `uaccess` across device-address changes |
| Photo editing and print | Revisioned non-destructive free crop with drag and 1% fine controls; automatic/portrait/landscape output; Fill/white Contain; six presets; five analog film looks; exposure, contrast, highlights, shadows, temperature, tint, saturation, sharpening and deterministic grain; clickable before/after; Single, 2×3 and 1½×2 yearbook, and two-strip photobooth layouts; exact 1200×1800 quality-60–95 JPEG and provenance; one-copy `photo-4x6-jpeg` hardware queue, no cuts or automatic retries; physically verified transport on DHP700 firmware 1.0.34_0073 |
| Sticker commissioning | One-copy 4x7 JPEG+PLT queue adapter, canonical polygon conversion, contour-aware three-point exterior seam ramps for clean closure, durable intent/evidence, and no automatic retries; rectangular 12-contour baseline physically verified and arbitrary outer contours enabled for operator-reviewed testing |
| Cut-only research | Private dry-run-first PLT-only harness for the two evidence-based standalone `cut-job` declarations; centered fresh-sheet fixture, conservative artwork-bounds outline, or exact completed-sticker PLT lineage; one default-pressure pass for printed or fully adhered adhesive glitter-laminate sheets; durable device intent, counter/state evidence, discard/print-activity detection, operator observations, and no retry or public API/UI exposure before physical validation |
| Foundation | Python/FastAPI/Pydantic, SQLAlchemy Core, explicit Alembic baseline, locked dependencies, actual SQLite runtime gate, committed OpenAPI and geometry contracts |
| Assets | Byte-preserving image upload, bounded decoding, lossless RGBA normalization, orientation/color provenance, original/normalized/pixel hashes, alpha and edge-connected-white component extraction |
| Editing | Immutable sticker/project revisions, project-wide shared bindings, explicit rectangular or normalized polygon outline, physical cut padding and minimum notch width; API accepts 0/90-degree placements |
| Layout | Fixed grid, fill, exact quantity pagination, physical sizing, artwork and adjusted-cut collision/guard rejection; source-arranged transparent crops retain their full imported scale even when empty rectangular bounds overlap; legacy shrunken imports can restore their original pixel-derived positions; optional reported uniform fitting makes room for bulk cut padding; locked source layouts have explicit restore and unlock actions |
| Rendering | Frozen source revision, RGB PNG, round-trip raster equality, content-addressed artifacts, manifest/profile snapshot, canonical geometry with resolved cut policy, simulator PLT and independently parsed overlay |
| Queue/history | Durable per-sheet queue, atomic state/events, concurrent idempotency suppression for Sticker and Photo, exclusive workers with durable heartbeat/readiness, held-job resume before intent, cancellation before intent, uncertainty/reboot hold, guarded operator resolution, cursor pagination, active-sheet badge, retained-file reprints, and readable thumbnail fallback |
| Browser | Photo-first navigation; filtered photo/sticker libraries with trash actions; dirty-state navigation/unload guards; responsive editors; accessible selected/toggle/live state, visible focus, larger touch controls and reduced-motion support; serialized polling with retry/backoff; connection-loss feedback; paper confirmation; Printer/Simulator status; remembered pumpkin-spice theme and favicon/home-screen art |
| Sticker usability | Cut overlay and 50–300% zoom, material-use/waste percentages and $0.60-sheet estimate, physical white-border and narrow-corner controls, uniform collision fitting, and Advanced normalized cut coordinates shown to three decimal places |
| Exports | Local active-page SVG/DXF/STL, saved or draft geometry, physical units/conventions, explicit holes in contract, configurable filled STL thickness, optional back-face centered circular magnet pocket or through-hole, no server conversion |
| Verification | Asymmetric art, partial pages, old render stability, linked edits, race deduplication, corrupted files, worker heartbeat/readiness, blocked-to-fresh-USB recovery, failure/restart holds, schema consistency, auth/origin checks, mesh dimensions/manifold edges/pocket volume, offline browser downloads, unsaved-change/crop/compare interactions, axe accessibility scan and 390×844 mobile overflow |

## Current limitations

The current physical width/height controls describe the artwork envelope. Cut padding and minimum notch width are resolved afterward in physical millimeters and revalidate the sheet guard and cut-to-cut collisions. Finished-boundary sizing, printable border fills, bleed, and their collision rules need the next geometry iteration.

Projects can reference multiple stickers and automatically extracted sheets create one definition per detected region. The arranger and polygon editor still target the first shared definition; selected-sticker editing, keep/remove corrections, split/group controls, and mixed-asset libraries need additional UI.

Rotation is supported by the authoritative geometry/raster paths through the API. Automatic rotation search, staggered rows, manual drag/pin interaction, pinned-obstacle packing, and per-item/selection export are not implemented. The current arranger refuses to replace pinned placements silently.

Sticker rendering uses ordinary raster resizing/compositing from normalized source assets with explicit sheet endpoint rounding. The simulator geometry has no calibrated page-to-printer transform. Photo rendering uses the physically validated print-only profile and intentionally contains no cut geometry.

The standalone cutter request remains a commissioning experiment. The harness can prove or reject PLT-only transport without sending an image, but automated tests cannot prove blade movement, ribbon inactivity, reload registration, or material safety. `cut_only`, a production profile, and a History **Cut again** action remain deliberately absent until the recorded fresh-sheet and reload gates pass. Physical double-printing is unsupported; digital double-exposure compositing is deferred.

## Next implementation work

1. Extend assorted-sheet extraction with reviewable mask overlays, keep/remove corrections, split/group operations, hole selection, independent print-background choice, and shared cut-policy models.
2. Extend the editor with canvas node tools, explicit finished-sticker sizing, printable borders/bleed, manual placement/pinning, undo/redo, per-design selection scopes, and richer library reuse. Preserve the existing canonical geometry contract and historical snapshots.
3. Complete persistence/operations: relational source/profile retention references, background operation execution, richer history filters/annotations/eligibility reporting, general evidence/timeline views beyond the implemented reboot reconciliation, safe storage cleanup, and verified database-plus-artifact backup/restore with dispatch held after restore.
4. Complete API/output typing and browser request typing for every response, richer stable validation codes/remediation, revision-conflict UI, and operator identity/session/revocable-token authorization. The committed generated contract currently types inputs and canonical geometry; some response shapes are explicitly maintained in TypeScript.
5. Extract the validated photo and sticker transports into a shared adapter interface with recorded protocol fixtures.
6. Validate the arbitrary-path sticker profile physically, measure registration tolerances, and add reviewed hole fixtures. Establish additional media and format limits before enabling broader physical print-and-cut submission.

The browser can explicitly run in simulator or hardware mode. The server prepares immutable jobs while the exclusive worker owns USB access; no device attempt is retried automatically. There is no hidden hardware switch, detached-instance operation, or server CAD endpoint.
