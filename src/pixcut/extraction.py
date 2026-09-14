"""Deterministic sticker-sheet foreground extraction and contour tracing."""

from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image
from shapely.geometry import Polygon


@dataclass
class ExtractedSticker:
    image: Image.Image
    outline: list[tuple[float, float]]
    bbox: tuple[int, int, int, int]
    area_px: int


def _edge_white_foreground(pixels: np.ndarray) -> np.ndarray | None:
    """Remove only near-white pixels connected to the image edge."""
    rgb = pixels[:, :, :3].astype(np.int16)
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    background = np.median(border, axis=0)
    if int(background.min()) < 220:
        return None
    candidate = (np.max(np.abs(rgb - background), axis=2) <= 32) & (
        np.min(rgb, axis=2) >= 210
    )
    height, width = candidate.shape
    exterior = np.zeros_like(candidate)
    queue = deque()
    for x in range(width):
        if candidate[0, x]:
            exterior[0, x] = True
            queue.append((0, x))
        if candidate[height - 1, x] and not exterior[height - 1, x]:
            exterior[height - 1, x] = True
            queue.append((height - 1, x))
    for y in range(height):
        if candidate[y, 0] and not exterior[y, 0]:
            exterior[y, 0] = True
            queue.append((y, 0))
        if candidate[y, width - 1] and not exterior[y, width - 1]:
            exterior[y, width - 1] = True
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if (
                0 <= ny < height
                and 0 <= nx < width
                and candidate[ny, nx]
                and not exterior[ny, nx]
            ):
                exterior[ny, nx] = True
                queue.append((ny, nx))
    return ~exterior


def foreground(image: Image.Image) -> tuple[np.ndarray, str]:
    pixels = np.asarray(image.convert("RGBA"))
    alpha = pixels[:, :, 3]
    transparent = np.count_nonzero(alpha < 250)
    border = np.concatenate((alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]))
    if transparent >= alpha.size // 100 or np.count_nonzero(border < 250) >= (
        len(border) // 10
    ):
        return alpha >= 16, "alpha"
    white = _edge_white_foreground(pixels)
    if white is not None and np.count_nonzero(white) < white.size * 0.98:
        return white, "edge-connected-white"
    return np.ones(alpha.shape, dtype=bool), "opaque-image"


def _components(mask: np.ndarray):
    height, width = mask.shape
    labels = np.zeros(mask.shape, dtype=np.int32)
    records = []
    next_label = 0
    for y, x in np.argwhere(mask):
        y, x = int(y), int(x)
        if labels[y, x]:
            continue
        next_label += 1
        labels[y, x] = next_label
        queue = [(y, x)]
        area = 0
        x0 = x1 = x
        y0 = y1 = y
        while queue:
            cy, cx = queue.pop()
            area += 1
            x0, x1 = min(x0, cx), max(x1, cx)
            y0, y1 = min(y0, cy), max(y1, cy)
            for ny, nx in (
                (cy - 1, cx),
                (cy + 1, cx),
                (cy, cx - 1),
                (cy, cx + 1),
            ):
                if (
                    0 <= ny < height
                    and 0 <= nx < width
                    and mask[ny, nx]
                    and not labels[ny, nx]
                ):
                    labels[ny, nx] = next_label
                    queue.append((ny, nx))
        records.append(
            {
                "label": next_label,
                "area": area,
                "bbox": (x0, y0, x1 + 1, y1 + 1),
            }
        )
    return labels, records


def _outer_ring(component: np.ndarray) -> list[tuple[float, float]]:
    """Trace directed pixel edges, then simplify the largest exterior ring."""
    top = component & ~np.pad(component[:-1], ((1, 0), (0, 0)))
    bottom = component & ~np.pad(component[1:], ((0, 1), (0, 0)))
    left = component & ~np.pad(component[:, :-1], ((0, 0), (1, 0)))
    right = component & ~np.pad(component[:, 1:], ((0, 0), (0, 1)))
    edges = set()
    for y, x in np.argwhere(top):
        edges.add(((int(x), int(y)), (int(x) + 1, int(y))))
    for y, x in np.argwhere(right):
        edges.add(((int(x) + 1, int(y)), (int(x) + 1, int(y) + 1)))
    for y, x in np.argwhere(bottom):
        edges.add(((int(x) + 1, int(y) + 1), (int(x), int(y) + 1)))
    for y, x in np.argwhere(left):
        edges.add(((int(x), int(y) + 1), (int(x), int(y))))

    directions = ((1, 0), (0, 1), (-1, 0), (0, -1))
    rings = []
    while edges:
        start_edge = min(edges)
        edges.remove(start_edge)
        start, current = start_edge
        prior = start
        ring = [start, current]
        while current != start:
            incoming = (current[0] - prior[0], current[1] - prior[1])
            index = directions.index(incoming)
            found = None
            for turn in (1, 0, -1, 2):
                direction = directions[(index + turn) % 4]
                target = (current[0] + direction[0], current[1] + direction[1])
                edge = (current, target)
                if edge in edges:
                    found = edge
                    break
            if found is None:
                break
            edges.remove(found)
            prior, current = found
            ring.append(current)
        if current == start and len(ring) >= 5:
            rings.append(ring[:-1])
    if not rings:
        raise ValueError("EXTRACTION_CONTOUR: could not trace a foreground contour")

    def signed_area(points):
        return sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
        ) / 2

    ring = max(rings, key=lambda points: abs(signed_area(points)))
    polygon = Polygon(ring)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda item: item.area)
    tolerance = max(1.25, min(component.shape) / 300)
    simplified = polygon.simplify(tolerance, preserve_topology=True)
    while len(simplified.exterior.coords) - 1 > 500:
        tolerance *= 1.5
        simplified = polygon.simplify(tolerance, preserve_topology=True)
    return [(float(x), float(y)) for x, y in simplified.exterior.coords[:-1]]


def extract(image: Image.Image) -> tuple[list[ExtractedSticker], str]:
    image = image.convert("RGBA")
    mask, method = foreground(image)
    labels, records = _components(mask)
    foreground_area = int(np.count_nonzero(mask))
    minimum = max(16, int(foreground_area * 0.005))
    selected = [
        record
        for record in records
        if record["area"] >= minimum
        and record["bbox"][2] - record["bbox"][0] >= 4
        and record["bbox"][3] - record["bbox"][1] >= 4
    ]
    selected.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    if not selected:
        raise ValueError("EXTRACTION_EMPTY: no usable foreground regions were found")
    if len(selected) > 100:
        raise ValueError("EXTRACTION_LIMIT: at most 100 sticker regions are supported")

    results = []
    source_pixels = np.asarray(image).copy()
    for record in selected:
        x0, y0, x1, y1 = record["bbox"]
        component = labels == record["label"]
        ring = _outer_ring(component)
        crop_mask = mask[y0:y1, x0:x1]
        crop_pixels = source_pixels[y0:y1, x0:x1].copy()
        crop_pixels[:, :, 3] = np.where(
            crop_mask, crop_pixels[:, :, 3], 0
        ).astype(np.uint8)
        width, height = x1 - x0, y1 - y0
        outline = [
            (
                min(1.0, max(0.0, (x - x0) / width)),
                min(1.0, max(0.0, (y - y0) / height)),
            )
            for x, y in ring
        ]
        results.append(
            ExtractedSticker(
                Image.fromarray(crop_pixels, "RGBA"),
                outline,
                (x0, y0, x1, y1),
                record["area"],
            )
        )
    return results, method
