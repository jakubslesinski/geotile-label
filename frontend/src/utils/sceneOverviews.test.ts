import { describe, expect, it } from "vitest";
import {
  deferDisplayOverviewUntilRasterReady,
  sceneResolutionPhase,
} from "./sceneOverviews";

describe("deferDisplayOverviewUntilRasterReady", () => {
  it("defers a pending native JPEG2000 pyramid", () => {
    expect(deferDisplayOverviewUntilRasterReady({
      overview_status: "pending",
      overview_type: "native_multiresolution",
    })).toBe(true);
  });

  it("does not defer sources without a useful native preview", () => {
    expect(deferDisplayOverviewUntilRasterReady({
      overview_status: "pending",
      overview_type: "none",
    })).toBe(false);
  });

  it("does not resubmit an already queued or ready pyramid", () => {
    expect(deferDisplayOverviewUntilRasterReady({
      overview_status: "queued",
      overview_type: "native_multiresolution",
    })).toBe(false);
    expect(deferDisplayOverviewUntilRasterReady({
      overview_status: "ready",
      overview_type: "native_multiresolution",
    })).toBe(false);
  });
});

describe("sceneResolutionPhase", () => {
  it("distinguishes a loading and usable native preview", () => {
    const scene = { overview_status: "pending", overview_type: "native_multiresolution" };
    expect(sceneResolutionPhase(scene, false)).toBe("native_preview_loading");
    expect(sceneResolutionPhase(scene, true)).toBe("native_preview_ready");
  });

  it("reports durable preparation states", () => {
    expect(sceneResolutionPhase({ overview_status: "queued" }, true)).toBe("high_resolution_queued");
    expect(sceneResolutionPhase({ overview_status: "building" }, true)).toBe("high_resolution_building");
    expect(sceneResolutionPhase({ overview_status: "ready" }, true)).toBe("high_resolution_ready");
    expect(sceneResolutionPhase({ overview_status: "error" }, true)).toBe("error");
  });
});

describe("kontrakt R0.4 - podgląd a pełna rozdzielczość", () => {
  it("gotowa piramida 2x nie obiecuje już pełnej rozdzielczości", () => {
    // To jest sedno R0.4: wcześniej ta sama scena raportowała "high_resolution_ready".
    expect(
      sceneResolutionPhase(
        { preview_status: "ready", fullres_derivative_status: "missing" },
        true,
      ),
    ).toBe("preview_ready_fullres_missing");
  });

  it("pełna rozdzielczość ready daje high_resolution_ready", () => {
    expect(
      sceneResolutionPhase(
        { preview_status: "ready", fullres_derivative_status: "ready" },
        true,
      ),
    ).toBe("high_resolution_ready");
  });

  it("stan zadania ma pierwszeństwo nad gotowym podglądem", () => {
    expect(
      sceneResolutionPhase(
        { preview_status: "ready", fullres_derivative_status: "building" },
        true,
      ),
    ).toBe("high_resolution_building");
    expect(
      sceneResolutionPhase(
        { preview_status: "ready", fullres_derivative_status: "validating" },
        true,
      ),
    ).toBe("high_resolution_validating");
  });

  it("błąd derywatu jest błędem niezależnie od podglądu", () => {
    expect(
      sceneResolutionPhase(
        { preview_status: "ready", fullres_derivative_status: "error" },
        true,
      ),
    ).toBe("error");
  });

  it("bez nowych pól zachowanie jest identyczne jak wcześniej", () => {
    // Zgodność wsteczna: starszy backend nie przysyła fullres_derivative_status.
    expect(sceneResolutionPhase({ overview_status: "ready" }, true)).toBe(
      "high_resolution_ready",
    );
    expect(sceneResolutionPhase({ overview_status: "native" }, true)).toBe("native_ready");
    expect(sceneResolutionPhase({ overview_status: "queued" }, true)).toBe(
      "high_resolution_queued",
    );
  });

  it("preview_status ma pierwszeństwo nad overview_status", () => {
    expect(
      sceneResolutionPhase(
        { overview_status: "pending", preview_status: "ready", fullres_derivative_status: "ready" },
        true,
      ),
    ).toBe("high_resolution_ready");
  });
});
