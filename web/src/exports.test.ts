import { describe, it, expect, vi } from "vitest";
import type { BufferGeometry } from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { exportSVG, exportDXF, exportSTL } from "./exports";
import type { CutGeometry, Ring } from "./types";

const ring = (points: [number, number][]): Ring => ({ closed: true, points });
const fixture: CutGeometry = {
  schema_version: "1.0",
  geometry_engine: "fixture",
  provenance: {
    kind: "render",
    project_id: "p",
    project_revision: 1,
    render_id: "r",
  },
  page_id: "page",
  page_mm: [101.6, 177.8],
  units: "mm",
  coordinate_space: "sheet_design",
  origin: "top_left",
  x_direction: "right",
  y_direction: "down",
  geometry_stage: "finished_cut_pre_device",
  shapes: [
    {
      id: "a",
      sticker_id: "s",
      sticker_revision: 1,
      placement_id: "a",
      outer: ring([
        [5, 5],
        [25, 5],
        [25, 35],
        [5, 35],
      ]),
      holes: [
        ring([
          [10, 10],
          [10, 15],
          [15, 15],
          [15, 10],
        ]),
      ],
    },
  ],
};

function measureSTL(data: ArrayBuffer) {
  const geometry = new STLLoader().parse(data);
  geometry.computeBoundingBox();
  const positions = geometry.getAttribute("position");
  const edges = new Map<string, number>();
  let volume = 0;
  const point = (index: number) => [
    positions.getX(index),
    positions.getY(index),
    positions.getZ(index),
  ];
  for (let index = 0; index < positions.count; index += 3) {
    const a = point(index),
      b = point(index + 1),
      c = point(index + 2);
    volume +=
      (a[0] * (b[1] * c[2] - b[2] * c[1]) +
        a[1] * (b[2] * c[0] - b[0] * c[2]) +
        a[2] * (b[0] * c[1] - b[1] * c[0])) /
      6;
    for (const [first, second] of [
      [a, b],
      [b, c],
      [c, a],
    ]) {
      const key = [first.join(","), second.join(",")].sort().join("|");
      edges.set(key, (edges.get(key) || 0) + 1);
    }
  }
  return { geometry, edges, volume };
}

function horizontalFaceCovers(
  geometry: BufferGeometry,
  z: number,
  x: number,
  y: number,
) {
  const positions = geometry.getAttribute("position");
  const side = (ax: number, ay: number, bx: number, by: number) =>
    (x - bx) * (ay - by) - (ax - bx) * (y - by);
  for (let index = 0; index < positions.count; index += 3) {
    const triangle = [0, 1, 2].map((offset) => ({
      x: positions.getX(index + offset),
      y: positions.getY(index + offset),
      z: positions.getZ(index + offset),
    }));
    if (!triangle.every((point) => Math.abs(point.z - z) < 1e-5)) continue;
    const signs = triangle.map((point, offset) => {
      const next = triangle[(offset + 1) % triangle.length];
      return side(point.x, point.y, next.x, next.y);
    });
    if (
      !(
        signs.some((value) => value < -1e-5) &&
        signs.some((value) => value > 1e-5)
      )
    )
      return true;
  }
  return false;
}

describe("local canonical geometry exports", () => {
  it("keeps millimeters, Y-down SVG and Y-up DXF without network calls", () => {
    const network = vi.fn(() => {
      throw new Error("Network disabled");
    });
    vi.stubGlobal("fetch", network);
    const svg = exportSVG(fixture);
    expect(svg).toContain('width="101.6mm"');
    expect(svg).toContain("M5 5 L25 5 L25 35 L5 35 Z");
    const pairs = exportDXF(fixture).trim().split("\n");
    const vertices: [number, number][] = [];
    for (let i = 0; i < pairs.length; i += 2)
      if (pairs[i] === "10")
        vertices.push([Number(pairs[i + 1]), Number(pairs[i + 3])]);
    expect(vertices.slice(0, 4)).toEqual([
      [5, 172.8],
      [25, 172.8],
      [25, 142.8],
      [5, 142.8],
    ]);
    expect(exportDXF(fixture)).toContain("$INSUNITS\n70\n4");
    exportSTL(fixture);
    expect(network).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
  it("creates a closed 20 × 30 × 2 mm solid with a real through-hole", () => {
    const geometry = new STLLoader().parse(exportSTL(fixture, 2));
    geometry.computeBoundingBox();
    const b = geometry.boundingBox!;
    expect(b.max.x - b.min.x).toBeCloseTo(20, 4);
    expect(b.max.y - b.min.y).toBeCloseTo(30, 4);
    expect(b.max.z - b.min.z).toBeCloseTo(2, 4);
    const p = geometry.getAttribute("position");
    const edges = new Map<string, number>();
    let volume = 0;
    const point = (i: number) => [p.getX(i), p.getY(i), p.getZ(i)];
    for (let i = 0; i < p.count; i += 3) {
      const a = point(i),
        b = point(i + 1),
        c = point(i + 2);
      volume +=
        (a[0] * (b[1] * c[2] - b[2] * c[1]) +
          a[1] * (b[2] * c[0] - b[0] * c[2]) +
          a[2] * (b[0] * c[1] - b[1] * c[0])) /
        6;
      for (const [x, y] of [
        [a, b],
        [b, c],
        [c, a],
      ]) {
        const key = [x.join(","), y.join(",")].sort().join("|");
        edges.set(key, (edges.get(key) || 0) + 1);
      }
    }
    expect([...edges.values()].every((n) => n === 2)).toBe(true);
    expect(volume).toBeCloseTo((20 * 30 - 5 * 5) * 2, 1);
    geometry.dispose();
  });
  it("adds a manifold back-opening magnet pocket or through-hole", () => {
    const circleArea = 0.5 * 64 * 3 ** 2 * Math.sin((2 * Math.PI) / 64);
    const baseVolume = (20 * 30 - 5 * 5) * 2;

    const pocket = measureSTL(exportSTL(fixture, 2, { diameter: 6, depth: 1 }));
    expect([...pocket.edges.values()].every((count) => count === 2)).toBe(true);
    expect(pocket.volume).toBeCloseTo(baseVolume - circleArea, 1);
    expect(horizontalFaceCovers(pocket.geometry, 0, 15, 157.8)).toBe(false);
    expect(horizontalFaceCovers(pocket.geometry, 2, 15, 157.8)).toBe(true);
    pocket.geometry.dispose();

    const through = measureSTL(
      exportSTL(fixture, 2, { diameter: 6, depth: 3 }),
    );
    expect([...through.edges.values()].every((count) => count === 2)).toBe(
      true,
    );
    expect(through.volume).toBeCloseTo(baseVolume - circleArea * 2, 1);
    expect(horizontalFaceCovers(through.geometry, 0, 15, 157.8)).toBe(false);
    expect(horizontalFaceCovers(through.geometry, 2, 15, 157.8)).toBe(false);
    through.geometry.dispose();
  });
  it("preserves repeated resolved placements and historical input", () => {
    const saved = JSON.stringify(fixture);
    const copies = structuredClone(fixture);
    copies.shapes.push({
      ...structuredClone(copies.shapes[0]),
      id: "b",
      placement_id: "b",
      outer: ring([
        [40, 5],
        [70, 5],
        [70, 25],
        [40, 25],
      ]),
      holes: [],
    });
    expect(exportSVG(copies).match(/<path /g)?.length).toBe(2);
    expect(exportDXF(copies).match(/LWPOLYLINE/g)?.length).toBe(3);
    expect(
      new STLLoader().parse(exportSTL(copies)).getAttribute("position").count,
    ).toBeGreaterThan(0);
    expect(JSON.stringify(fixture)).toBe(saved);
  });
  it("rejects invalid thickness and nonfinite geometry", () => {
    expect(() => exportSTL(fixture, 0)).toThrow();
    expect(() => exportSTL(fixture, 2, { diameter: 50, depth: 1 })).toThrow(
      /does not fit inside part 1/,
    );
    expect(() => exportSTL(fixture, 2, { diameter: 6, depth: 0 })).toThrow(
      /Magnet depth/,
    );
    const bad = structuredClone(fixture);
    bad.shapes[0].outer.points[0][0] = NaN;
    expect(() => exportSVG(bad)).toThrow();
  });
});
