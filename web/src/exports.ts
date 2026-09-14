import {
  BufferGeometry,
  ExtrudeGeometry,
  Float32BufferAttribute,
  Mesh,
  MeshBasicMaterial,
  Path,
  Shape,
  ShapeUtils,
  Vector2,
} from "three";
import { STLExporter } from "three/addons/exporters/STLExporter.js";
import type { CutGeometry, Ring } from "./types";

function validate(g: CutGeometry) {
  if (
    g.schema_version !== "1.0" ||
    g.units !== "mm" ||
    g.coordinate_space !== "sheet_design" ||
    g.geometry_stage !== "finished_cut_pre_device" ||
    g.origin !== "top_left" ||
    g.y_direction !== "down" ||
    g.x_direction !== "right"
  )
    throw new Error("Unsupported geometry coordinate contract");
  if (!g.shapes.length) throw new Error("Select a nonempty sheet");
  for (const dimension of g.page_mm)
    if (!Number.isFinite(dimension) || dimension <= 0 || dimension > 1000)
      throw new Error("Invalid page size");
  for (const shape of g.shapes)
    for (const ring of [shape.outer, ...shape.holes]) {
      if (
        !ring.closed ||
        ring.points.length < 3 ||
        new Set(ring.points.map((p) => p.join(","))).size !== ring.points.length
      )
        throw new Error("Invalid closed ring");
      for (const [x, y] of ring.points)
        if (
          !Number.isFinite(x) ||
          !Number.isFinite(y) ||
          x < 0 ||
          y < 0 ||
          x > g.page_mm[0] ||
          y > g.page_mm[1]
        )
          throw new Error("Invalid geometry extent");
    }
}
const n = (v: number) => Number(v.toFixed(6)).toString();

export interface MagnetPocket {
  diameter: number;
  depth: number;
}

type FlatPoint = [number, number];
type Vertex = [number, number, number];

function signedArea(points: FlatPoint[]) {
  let twiceArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index];
    const [x2, y2] = points[(index + 1) % points.length];
    twiceArea += x1 * y2 - x2 * y1;
  }
  return twiceArea / 2;
}

function oriented(points: FlatPoint[], clockwise: boolean) {
  return signedArea(points) < 0 === clockwise ? points : [...points].reverse();
}

function addTriangle(
  positions: number[],
  first: Vertex,
  second: Vertex,
  third: Vertex,
) {
  positions.push(...first, ...second, ...third);
}

function addCap(
  positions: number[],
  outer: FlatPoint[],
  holes: FlatPoint[][],
  z: number,
  upward: boolean,
) {
  const contour = outer.map(([x, y]) => new Vector2(x, y));
  const cutouts = holes.map((ring) => ring.map(([x, y]) => new Vector2(x, y)));
  const vertices = [...contour, ...cutouts.flat()];
  for (const [a, b, c] of ShapeUtils.triangulateShape(contour, cutouts)) {
    const first = vertices[a],
      second = vertices[b],
      third = vertices[c];
    const cross =
      (second.x - first.x) * (third.y - first.y) -
      (second.y - first.y) * (third.x - first.x);
    const triangle: [Vertex, Vertex, Vertex] = [
      [first.x, first.y, z],
      [second.x, second.y, z],
      [third.x, third.y, z],
    ];
    if (cross > 0 !== upward)
      [triangle[1], triangle[2]] = [triangle[2], triangle[1]];
    addTriangle(positions, ...triangle);
  }
}

function addWall(
  positions: number[],
  ring: FlatPoint[],
  bottom: number,
  top: number,
) {
  for (let index = 0; index < ring.length; index += 1) {
    const [x1, y1] = ring[index];
    const [x2, y2] = ring[(index + 1) % ring.length];
    addTriangle(positions, [x1, y1, bottom], [x2, y2, bottom], [x2, y2, top]);
    addTriangle(positions, [x1, y1, bottom], [x2, y2, top], [x1, y1, top]);
  }
}

function pointInRing([x, y]: FlatPoint, ring: Ring) {
  let inside = false;
  for (
    let index = 0, previous = ring.points.length - 1;
    index < ring.points.length;
    previous = index++
  ) {
    const [x1, y1] = ring.points[index];
    const [x2, y2] = ring.points[previous];
    if (y1 > y !== y2 > y && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1)
      inside = !inside;
  }
  return inside;
}

function distanceToRing([x, y]: FlatPoint, ring: Ring) {
  let minimum = Infinity;
  for (let index = 0; index < ring.points.length; index += 1) {
    const [x1, y1] = ring.points[index];
    const [x2, y2] = ring.points[(index + 1) % ring.points.length];
    const dx = x2 - x1,
      dy = y2 - y1;
    const lengthSquared = dx * dx + dy * dy;
    const amount =
      lengthSquared === 0
        ? 0
        : Math.max(
            0,
            Math.min(1, ((x - x1) * dx + (y - y1) * dy) / lengthSquared),
          );
    minimum = Math.min(
      minimum,
      Math.hypot(x - (x1 + amount * dx), y - (y1 + amount * dy)),
    );
  }
  return minimum;
}

function pocketGeometry(
  g: CutGeometry,
  thickness: number,
  pocket: MagnetPocket,
) {
  if (
    !Number.isFinite(pocket.diameter) ||
    pocket.diameter <= 0 ||
    pocket.diameter > 100
  )
    throw new Error(
      "Magnet diameter must be greater than 0 and at most 100 mm",
    );
  if (!Number.isFinite(pocket.depth) || pocket.depth <= 0 || pocket.depth > 100)
    throw new Error("Magnet depth must be greater than 0 and at most 100 mm");

  const positions: number[] = [];
  const radius = pocket.diameter / 2;
  const throughHole = pocket.depth >= thickness;
  const pocketTop = Math.min(thickness, pocket.depth);
  for (const [shapeIndex, shape] of g.shapes.entries()) {
    const xs = shape.outer.points.map(([x]) => x);
    const ys = shape.outer.points.map(([, y]) => y);
    const center: FlatPoint = [
      (Math.min(...xs) + Math.max(...xs)) / 2,
      (Math.min(...ys) + Math.max(...ys)) / 2,
    ];
    const fits =
      pointInRing(center, shape.outer) &&
      !shape.holes.some((hole) => pointInRing(center, hole)) &&
      [shape.outer, ...shape.holes].every(
        (ring) => distanceToRing(center, ring) > radius + 1e-6,
      );
    if (!fits)
      throw new Error(
        `Magnet pocket does not fit inside part ${shapeIndex + 1}; reduce its diameter`,
      );

    const modelRing = (ring: Ring, clockwise: boolean) =>
      oriented(
        ring.points.map(([x, y]) => [x, g.page_mm[1] - y] as FlatPoint),
        clockwise,
      );
    const outer = modelRing(shape.outer, false);
    const holes = shape.holes.map((hole) => modelRing(hole, true));
    const modelCenter: FlatPoint = [center[0], g.page_mm[1] - center[1]];
    const circle = Array.from({ length: 64 }, (_, index) => {
      const angle = (-index / 64) * Math.PI * 2;
      return [
        modelCenter[0] + Math.cos(angle) * radius,
        modelCenter[1] + Math.sin(angle) * radius,
      ] as FlatPoint;
    });

    addCap(positions, outer, [...holes, circle], 0, false);
    addCap(
      positions,
      outer,
      throughHole ? [...holes, circle] : holes,
      thickness,
      true,
    );
    addWall(positions, outer, 0, thickness);
    for (const hole of holes) addWall(positions, hole, 0, thickness);
    addWall(positions, circle, 0, pocketTop);
    if (!throughHole)
      for (let index = 0; index < circle.length; index += 1)
        addTriangle(
          positions,
          [modelCenter[0], modelCenter[1], pocketTop],
          [...circle[index], pocketTop],
          [...circle[(index + 1) % circle.length], pocketTop],
        );
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  geometry.computeVertexNormals();
  return geometry;
}

export function exportSVG(g: CutGeometry): string {
  validate(g);
  const ringPath = (r: Ring) =>
    r.points.map(([x, y], i) => `${i ? "L" : "M"}${n(x)} ${n(y)}`).join(" ") +
    " Z";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${n(g.page_mm[0])}mm" height="${n(g.page_mm[1])}mm" viewBox="0 0 ${g.page_mm.map(n).join(" ")}"><g fill="none" stroke="black" stroke-width="0.1" fill-rule="evenodd">${g.shapes.map((s) => `<path d="${[s.outer, ...s.holes].map(ringPath).join(" ")}"/>`).join("")}</g></svg>`;
}

export function exportDXF(g: CutGeometry): string {
  validate(g);
  const parts: (string | number)[] = [
    0,
    "SECTION",
    2,
    "HEADER",
    9,
    "$ACADVER",
    1,
    "AC1015",
    9,
    "$INSUNITS",
    70,
    4,
    0,
    "ENDSEC",
    0,
    "SECTION",
    2,
    "ENTITIES",
  ];
  for (const shape of g.shapes)
    for (const [i, ring] of [shape.outer, ...shape.holes].entries()) {
      parts.push(
        0,
        "LWPOLYLINE",
        100,
        "AcDbEntity",
        8,
        i ? "HOLES" : "OUTLINES",
        100,
        "AcDbPolyline",
        90,
        ring.points.length,
        70,
        1,
      );
      for (const [x, y] of ring.points)
        parts.push(10, n(x), 20, n(g.page_mm[1] - y));
    }
  parts.push(0, "ENDSEC", 0, "EOF");
  return parts.join("\n") + "\n";
}

export function exportSTL(
  g: CutGeometry,
  thickness = 2,
  magnetPocket?: MagnetPocket,
): ArrayBuffer {
  validate(g);
  if (!Number.isFinite(thickness) || thickness <= 0 || thickness > 100)
    throw new Error("Thickness must be greater than 0 and at most 100 mm");
  const geometry = magnetPocket
    ? pocketGeometry(g, thickness, magnetPocket)
    : new ExtrudeGeometry(
        g.shapes.map((s) => {
          const shape = new Shape();
          const draw = (target: Path, ring: Ring) => {
            ring.points.forEach(([x, y], i) =>
              i
                ? target.lineTo(x, g.page_mm[1] - y)
                : target.moveTo(x, g.page_mm[1] - y),
            );
            target.closePath();
          };
          draw(shape, s.outer);
          shape.holes = s.holes.map((h) => {
            const path = new Path();
            draw(path, h);
            return path;
          });
          return shape;
        }),
        {
          depth: thickness,
          steps: 1,
          bevelEnabled: false,
          curveSegments: 1,
        },
      );
  const material = new MeshBasicMaterial();
  try {
    const data = new STLExporter().parse(new Mesh(geometry, material), {
      binary: true,
    });
    return data.buffer.slice(
      data.byteOffset,
      data.byteOffset + data.byteLength,
    ) as ArrayBuffer;
  } finally {
    geometry.dispose();
    material.dispose();
  }
}

export function download(data: BlobPart, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
