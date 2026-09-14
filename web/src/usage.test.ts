import { describe, expect, it } from "vitest";
import type { CutGeometry, Point } from "./types";
import { calculateSheetUsage } from "./usage";

function geometry(outer: Point[], holes: Point[][] = []): CutGeometry {
  return {
    schema_version: "1.0",
    geometry_engine: "fixture",
    provenance: {
      kind: "draft",
      project_id: "project",
      project_revision: 1,
      render_id: null,
    },
    page_id: "page",
    page_mm: [100, 100],
    units: "mm",
    coordinate_space: "sheet_design",
    origin: "top_left",
    x_direction: "right",
    y_direction: "down",
    geometry_stage: "finished_cut_pre_device",
    shapes: [
      {
        id: "shape",
        sticker_id: "sticker",
        sticker_revision: 1,
        placement_id: "placement",
        outer: { closed: true, points: outer },
        holes: holes.map((points) => ({ closed: true, points })),
      },
    ],
  };
}

describe("sheet material usage", () => {
  it("splits sheet area and cost into complementary totals", () => {
    const usage = calculateSheetUsage(
      geometry([
        [0, 0],
        [50, 0],
        [50, 100],
        [0, 100],
      ]),
    );
    expect(usage.usedPercent).toBe(50);
    expect(usage.unusedPercent).toBe(50);
    expect(usage.usedCostCents).toBe(30);
    expect(usage.unusedCostCents).toBe(30);
    expect(usage.usedSquareInches).toBeCloseTo(7.75, 2);
  });

  it("subtracts holes from the sticker area", () => {
    const usage = calculateSheetUsage(
      geometry(
        [
          [0, 0],
          [100, 0],
          [100, 100],
          [0, 100],
        ],
        [
          [
            [0, 0],
            [25, 0],
            [25, 100],
            [0, 100],
          ],
        ],
      ),
    );
    expect(usage.usedPercent).toBe(75);
    expect(usage.unusedPercent).toBe(25);
    expect(usage.usedCostCents + usage.unusedCostCents).toBe(60);
  });
});
