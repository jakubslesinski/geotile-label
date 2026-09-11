import { describe, expect, it } from "vitest";
import { PIXEL_TILE_ZOOM_DELTA, PIXEL_TILE_ZOOM_SNAP } from "./pixelTileZoom";

describe("pixel tile zoom contract", () => {
  it("moves only between integer backend zoom levels", () => {
    expect(PIXEL_TILE_ZOOM_SNAP).toBe(1);
    expect(PIXEL_TILE_ZOOM_DELTA).toBe(1);
    expect(Number.isInteger(PIXEL_TILE_ZOOM_SNAP)).toBe(true);
  });
});
