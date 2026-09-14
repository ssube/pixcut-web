# Third-party provenance

Application source is an independent implementation of the supplied design and observed protocol behavior. Upstream repositories are cloned into ignored `.upstream/` directories for local protocol research; no repository is vendored into the application.

The optional local runtime build uses the official SQLite 3.51.3 amalgamation:

- Source: https://sqlite.org/2026/sqlite-amalgamation-3510300.zip
- SHA-256: `acb1e6f5d832484bf6d32b681e858c38add8b2acdfd42ac5df24b8afb46552b4`
- License: SQLite is public domain; see https://sqlite.org/copyright.html.
- Build recipe: `scripts/build_sqlite.py`; generated Linux shared library is ignored by version control.

Python and JavaScript dependencies and resolved artifacts are enumerated in `uv.lock` and `web/package-lock.json`. Browser solids use Three.js extrusion and STL serialization; no CAD executable or backend conversion service is installed.

The independent read-only USB probe uses observed `get-prop` framing and property names from PixCut-App commit `bc243ce63d9f458b818cb9e19b3c93f281b85f7a`:

- https://github.com/popcornhax/PixCut-App/blob/bc243ce63d9f458b818cb9e19b3c93f281b85f7a/docs/pixcut-usb-protocol.md
- https://github.com/popcornhax/PixCut-App/blob/bc243ce63d9f458b818cb9e19b3c93f281b85f7a/pixcut/orchestrator.py

The private 4x6 photo and 4x7 rectangular sticker commissioning paths also reimplement protocol behavior documented by these pinned local sources:

- PixCut-App commit `bc243ce63d9f458b818cb9e19b3c93f281b85f7a`: standalone USB JPEG request fields, USB channel, endpoint framing, and the 1 MiB JPEG limit. The repository declares the MIT license.
- eastbay-pixcut-s1 commit `a73cd65b7374e5b9e1eb7a0c54594f3276c14f8f`: official-SDK-derived 10,215-byte job chunks, 1,024-byte USB sub-writes, 20 ms post-ACK pacing, combo-job fields and payload order, and the native rectangular PLT coordinate observations. No license file was present in the cloned revision, so only independently expressed protocol facts are used.
- sapodilla commit `efac7f4062ec7fcba92f36365e3a4ed62f12e4db`: comparison source for media/document codes and transport-specific channel values. No source code was copied.
- honeymaro-pixcut commit `08580a69cc8cd6823ad58297f9594000e6ad06ff`: error-code comparison source. The repository declares the MIT license.

The optional `usb` dependency group pins PyUSB 1.3.1 and uses the host libusb backend.
