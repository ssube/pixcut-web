"""Regenerate public OpenAPI and a canonical geometry fixture without printer work."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from pixcut.cli import migrate
from pixcut.api import create_app
from pixcut.imaging import resolve
from pixcut.models import CutGeometry

root = Path(__file__).resolve().parents[1]
with TemporaryDirectory(prefix="pixcut-contracts-") as tmp:
    migrate(Path(tmp))
    app = create_app(Path(tmp))
    (root / "contracts/openapi.json").write_text(
        json.dumps(app.openapi(), indent=2) + "\n"
    )
    app.state.service.engine.dispose()
project = {
    "id": "fixture-project",
    "revision": 1,
    "bindings": {"fixture-sticker": 1},
    "pages": [
        {
            "id": "page-0",
            "placements": [
                {
                    "id": "placement-0",
                    "sticker_id": "fixture-sticker",
                    "x_mm": 5,
                    "y_mm": 5,
                    "width_mm": 20,
                    "height_mm": 30,
                    "rotation": 0,
                    "pinned": False,
                }
            ],
        }
    ],
}
stickers = {
    "fixture-sticker": {"revision": 1, "outline": [(0, 0), (1, 0), (1, 1), (0, 1)]}
}
geometry = resolve(project, stickers, "fixture-render")[0]
(root / "contracts/cut-geometry-v1.json").write_text(
    json.dumps(geometry, indent=2) + "\n"
)

(root / "contracts/cut-geometry.schema.json").write_text(
    json.dumps(CutGeometry.model_json_schema(), indent=2) + "\n"
)
