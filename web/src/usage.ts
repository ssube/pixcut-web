import type { CutGeometry, Point } from "./types";

const MM2_PER_SQUARE_INCH = 25.4 * 25.4;

function polygonArea(points: Point[]) {
  if (points.length < 3) return 0;
  let twiceArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const [x1, y1] = points[index];
    const [x2, y2] = points[(index + 1) % points.length];
    twiceArea += x1 * y2 - x2 * y1;
  }
  return Math.abs(twiceArea) / 2;
}

export interface SheetUsage {
  usedPercent: number;
  unusedPercent: number;
  usedSquareInches: number;
  unusedSquareInches: number;
  usedCostCents: number;
  unusedCostCents: number;
}

export function calculateSheetUsage(
  geometry: CutGeometry,
  sheetCostCents = 60,
): SheetUsage {
  const sheetArea = Math.max(0, geometry.page_mm[0] * geometry.page_mm[1]);
  const stickerArea = geometry.shapes.reduce((total, shape) => {
    const holes = shape.holes.reduce(
      (area, hole) => area + polygonArea(hole.points as Point[]),
      0,
    );
    return (
      total + Math.max(0, polygonArea(shape.outer.points as Point[]) - holes)
    );
  }, 0);
  const usedArea = Math.min(sheetArea, Math.max(0, stickerArea));
  const usedFraction = sheetArea > 0 ? usedArea / sheetArea : 0;
  const usedPercent = Math.round(usedFraction * 100);
  const usedCostCents = Math.round(usedFraction * sheetCostCents);

  return {
    usedPercent,
    unusedPercent: 100 - usedPercent,
    usedSquareInches: usedArea / MM2_PER_SQUARE_INCH,
    unusedSquareInches: (sheetArea - usedArea) / MM2_PER_SQUARE_INCH,
    usedCostCents,
    unusedCostCents: sheetCostCents - usedCostCents,
  };
}
