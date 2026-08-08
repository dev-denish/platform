import { test, expect } from "@playwright/test";
import { gotoAnalysisView } from "./helpers.js";

/**
 * Maps tab -> Analysis view (Wave: GEE analysis registry).
 *
 * The "Run analysis" test hits REAL Google Earth Engine from the ephemeral
 * stack (see docker-compose.test.yml's DMRV_GEE_PROJECT_ID/credentials mount) -
 * there is no meaningful mock for a real dataset query, and a fake one would
 * only prove the mock works. Hence the long per-test timeout: Hansen takes
 * ~7s server-side, plus tile loading.
 *
 * gotoAnalysisView() itself now lives in helpers.js (Wave: enriched index
 * results added a second spec file, analysis-enriched.spec.js, that needs
 * the exact same "get to the Analysis view" steps - shared rather than
 * copy-pasted).
 */

test.describe("Analysis view", () => {
  test("lists the whole registry, in-development entries included but de-emphasized", async ({ page }) => {
    await gotoAnalysisView(page);
    // 16 catalog entries, grouped by category. 10 are "available" (Wave:
    // vegetation indices flipped NDVI/EVI/SAVI/MNDWI/NBR on) - 6 remain
    // "in-development" and render muted.
    await expect(page.locator(".analysis-row")).toHaveCount(16);
    await expect(page.locator(".analysis-row-muted")).toHaveCount(6);
    await expect(page.getByRole("button", { name: /^Forest Change$/ })).toBeVisible();

    // An in-development entry: honest empty state, no numbers, no run button.
    await page.getByRole("button", { name: /^SAR/ }).click();
    const results = page.locator(".analysis-results-body");
    await expect(results.getByText("This analysis isn't built yet")).toBeVisible();
    await expect(page.getByRole("button", { name: /Run analysis|Refresh/ })).toHaveCount(0);
  });

  test("computing Hansen renders real stats, the canopy threshold, and a map overlay", async ({ page }) => {
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    const results = page.locator(".analysis-results-body");

    await page.getByRole("button", { name: /Global Forest Change/ }).click();
    await expect(results.getByText("Not computed yet")).toBeVisible();
    // Nothing computed -> nothing on the map.
    await expect(page.locator('.leaflet-container img[src*="earthengine"]')).toHaveCount(0);

    await page.getByRole("button", { name: "Run analysis" }).click();
    await expect(page.locator(".analysis-callout")).toBeVisible({ timeout: 120_000 });
    await expect(page.locator(".analysis-callout")).toContainText(/canopy cover/);
    await expect(page.locator(".analysis-callout-value")).toContainText("ha");
    // stats.note is always rendered, never hidden.
    await expect(page.locator(".analysis-note").first()).toContainText(/2000-2012/);
    // Real GEE tiles over the project's own layers.
    await expect(page.locator('.leaflet-container img[src*="earthengine"]').first()).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
    if (process.env.ANALYSIS_SHOT) await page.screenshot({ path: process.env.ANALYSIS_SHOT });
  });

  test("a class breakdown renders with legend colors", async ({ page }) => {
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    await page.getByRole("button", { name: /ESA WorldCover/ }).click();
    await page.getByRole("button", { name: "Run analysis" }).click();
    // 11 WorldCover classes, every one with a legend swatch (legend[].color).
    await expect(page.locator(".analysis-bar-row")).toHaveCount(11, { timeout: 120_000 });
    await expect(page.locator(".analysis-bar-row .legend-swatch")).toHaveCount(11);
    await expect(page.locator('.leaflet-container img[src*="earthengine"]').first()).toBeVisible({
      timeout: 60_000,
    });
    if (process.env.ANALYSIS_SHOT) await page.screenshot({ path: process.env.ANALYSIS_SHOT });
  });

  test("a per-year breakdown defaults to the latest year and switches years", async ({ page }) => {
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    await page.getByRole("button", { name: /Annual Land Cover/ }).click();
    await page.getByRole("button", { name: "Run analysis" }).click();
    const year = page.locator(".analysis-year-select select");
    await expect(year).toBeVisible({ timeout: 120_000 });
    // Latest year the backend computed (_ESRI_LULC_YEARS ends at 2023).
    await expect(year).toHaveValue("2023");
    const barCount = await page.locator(".analysis-bar-row").count();
    expect(barCount).toBeGreaterThan(0);
    await year.selectOption("2018");
    await expect(page.locator(".analysis-bar-row")).toHaveCount(barCount);
  });

  test("computing NDVI polls the async job to completion and renders a real per-year trend", async ({ page }) => {
    // NDVI (execution: "async" - Wave: vegetation indices) computes fresh
    // from raw Sentinel-2 imagery via the job-queue/polling path, not a
    // direct request/response - measured 5-59s end-to-end against real
    // boundaries, hence the long timeout (matches the other slow specs here).
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    const results = page.locator(".analysis-results-body");

    await page.getByRole("button", { name: /^NDVI/ }).click();
    await expect(results.getByText("Not computed yet")).toBeVisible();
    await expect(page.locator('.leaflet-container img[src*="earthengine"]')).toHaveCount(0);

    // Scoped by container + class, not by name text: this button's own
    // label changes under us (Run analysis -> Queued…/Computing… -> Refresh
    // - see runningLabel() in AnalysisPanel.jsx), and a name-filtered
    // getByRole re-queries on every use, so a locator bound to "Run
    // analysis" stops matching anything the instant the label changes -
    // confirmed: that's exactly what made the assertion below "element(s)
    // not found" even though the button was on screen and correctly reading
    // "Computing…". `.primary-button` is the only button
    // AnalysisPanel.jsx's renderResults() ever renders inside
    // .analysis-results-body, so this locator is stable across every one of
    // its label changes, not just past this one instance of it.
    const runButton = results.locator(".primary-button");
    await runButton.click();
    // The in-flight label reflects the real job status while polling
    // (queued, then running) rather than a static "Computing…" the whole
    // time - see runningLabel() in AnalysisPanel.jsx.
    await expect(runButton).toHaveText(/Queued…|Computing…/);

    const yearInput = page.locator(".analysis-year-select input[type=range]");
    await expect(yearInput).toBeVisible({ timeout: 120_000 });

    // A real computed series: the mono-cell caption next to the scrubber
    // shows "<year>: <value>", never "no data" for a boundary with real
    // land cover, and the value is a plausible NDVI reading (-1..1, and in
    // practice well above 0 for vegetated land).
    const valueLabel = page.locator(".analysis-year-select .mono-cell");
    await expect(valueLabel).not.toContainText("no data");
    const [, latestValueText] = (await valueLabel.textContent()).match(/:\s*(-?[\d.]+)/);
    const latestValue = Number(latestValueText);
    expect(latestValue).toBeGreaterThan(-1);
    expect(latestValue).toBeLessThan(1);

    // The compositing note (cloud-masked, pre-monsoon Sentinel-2) is always
    // rendered, and the map caption says which year's tile is showing.
    await expect(page.locator(".analysis-note").filter({ hasText: "cloud-masked" })).toBeVisible();
    await expect(page.locator(".analysis-note").filter({ hasText: "Map shows the latest year" })).toBeVisible();
    await expect(page.locator('.leaflet-container img[src*="earthengine"]').first()).toBeVisible({
      timeout: 60_000,
    });

    // The scrubber drives which year is highlighted using the already-
    // fetched series - dragging it changes the caption with no new request.
    const initialLabel = await valueLabel.textContent();
    await yearInput.fill("0");
    await expect(valueLabel).not.toHaveText(initialLabel);

    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
    if (process.env.ANALYSIS_SHOT) await page.screenshot({ path: process.env.ANALYSIS_SHOT });
  });

  test("Identify on a computed NDVI result shows a real per-pixel value, not a raw code or a no-op", async ({
    page,
  }) => {
    // Real value = real GEE round trip through GET .../analyses/ndvi/point,
    // same as the trend chart above - there is no meaningful mock for this.
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    const results = page.locator(".analysis-results-body");

    await page.getByRole("button", { name: /^NDVI/ }).click();
    // NDVI may already be computed here (this same seeded QA project is
    // shared with the "computing NDVI…" test above, which runs first in a
    // full suite run and leaves a cached result behind) - the primary
    // button reads "Run analysis" OR "Refresh" depending on which, but
    // either one (re)computes a FRESH result, which is what this test
    // actually needs; asserting "Not computed yet" would only hold when
    // this spec happens to run in isolation.
    await results.locator(".primary-button").click();
    await expect(page.locator(".analysis-year-select input[type=range]")).toBeVisible({ timeout: 120_000 });
    await expect(page.locator('.leaflet-container img[src*="earthengine"]').first()).toBeVisible({
      timeout: 60_000,
    });

    // Toolbar/Layers panel default collapsed (Wave: floating map controls) -
    // reach in via the same wrench toggle map.spec.js's openMapPanels() uses,
    // scoped to THIS view's own map instance rather than importing that
    // helper (it assumes a single map on the page; the Analysis view is a
    // second, independently-mounted ProjectMap - see that component's own
    // "two separate ProjectMap instances" note).
    const toolbarToggle = page.getByRole("button", { name: /Show map tools|Hide map tools/ });
    if ((await toolbarToggle.getAttribute("aria-expanded")) !== "true") await toolbarToggle.click();
    await page.getByRole("button", { name: "Identify" }).click();

    const popup = page.locator(".pixel-popup");

    // The project's OTHER layers (seeded by other specs sharing this same QA
    // project) span a much wider extent than this Boundary, and even INSIDE
    // the Boundary's own on-screen bounding box a cloud-masked Sentinel-2
    // composite has real gaps (confirmed live: at some points every layer -
    // the uploaded COG rasters too, not just NDVI - agreed "No data", so
    // that's real per-pixel coverage, not a bug in the new endpoint).
    // Guessing a screen pixel to click is unreliable; instead, ask the SAME
    // endpoint under test directly (GET .../analyses/ndvi/point) for a grid
    // of candidate lon/lats inside the Boundary's real bounds, keep the
    // first one with real data, then convert that lon/lat to a screen pixel
    // via the standard spherical-Mercator slippy-map formula (Leaflet's own
    // projection) using the map's current center/zoom (read from
    // CoordinateBadge) - so the final UI click lands on a point already
    // proven to have data, instead of hoping a visually-guessed spot does.
    const projectId = page.url().match(/\/projects\/([\w-]+)/)[1];
    const token = await page.evaluate(() => sessionStorage.getItem("dmrv.access_token"));
    const apiBase = "http://localhost:8091/api/v1";
    const authHeaders = { Authorization: `Bearer ${token}` };

    const layersRes = await page.request.get(`${apiBase}/projects/${projectId}/layers`, {
      headers: authHeaders,
    });
    const { layers: projectLayers } = await layersRes.json();
    const boundaryLayer = projectLayers.find((l) => l.type === "Boundary");
    const [[latMin, lngMin], [latMax, lngMax]] = boundaryLayer.bounds;

    // Leaflet's default spherical-Mercator slippy-map projection: world
    // pixel coordinates at a given zoom, standard 256px tiles.
    function worldPx(lat, lon, zoom) {
      const scale = 256 * 2 ** zoom;
      const sin = Math.sin((lat * Math.PI) / 180);
      return {
        x: ((lon + 180) / 360) * scale,
        y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale,
      };
    }
    const badgeText = await page.locator(".map-coord-badge").innerText();
    const [, centerLatText, centerLonText, zoomText] = badgeText.match(
      /Lat:\s*(-?[\d.]+).*Lon:\s*(-?[\d.]+).*Zoom:\s*(\d+)/s
    );
    const zoom = Number(zoomText);
    const centerPx = worldPx(Number(centerLatText), Number(centerLonText), zoom);
    const mapBox = await page.locator(".leaflet-container").boundingBox();
    // The coordinate badge (a Leaflet control, bottom-right of the map,
    // `L.DomEvent.disableClickPropagation`'d) never forwards clicks to the
    // map underneath - confirmed live: a computed click point that happened
    // to land on it produced no popup at all, not even a nodata one. Any
    // candidate whose screen position falls under it must be skipped, not
    // just clicked anyway.
    const badgeBox = await page.locator(".map-coord-badge").boundingBox();
    function toScreenPx(lat, lon) {
      const p = worldPx(lat, lon, zoom);
      return {
        x: mapBox.x + mapBox.width / 2 + (p.x - centerPx.x),
        y: mapBox.y + mapBox.height / 2 + (p.y - centerPx.y),
      };
    }
    function underBadge(pt) {
      const pad = 6;
      return (
        pt.x >= badgeBox.x - pad &&
        pt.x <= badgeBox.x + badgeBox.width + pad &&
        pt.y >= badgeBox.y - pad &&
        pt.y <= badgeBox.y + badgeBox.height + pad
      );
    }

    // Ask the SAME endpoint under test directly (GET .../analyses/ndvi/point)
    // for a grid of candidate lon/lats inside the Boundary's real bounds -
    // even INSIDE the Boundary's own on-screen bounding box a cloud-masked
    // Sentinel-2 composite has real gaps (confirmed live: at some points
    // every layer - the uploaded COG rasters too, not just NDVI - agreed
    // "No data", so that's real per-pixel coverage, not a bug in the new
    // endpoint) - so a visually-guessed screen pixel is unreliable, but a
    // point already proven (via this same API) to have data is not.
    let target = null;
    outer: for (const yf of [0.5, 0.3, 0.7, 0.15, 0.85]) {
      for (const xf of [0.5, 0.3, 0.7, 0.15, 0.85]) {
        const lat = latMin + (latMax - latMin) * yf;
        const lon = lngMin + (lngMax - lngMin) * xf;
        const pt = toScreenPx(lat, lon);
        if (pt.x < mapBox.x || pt.x > mapBox.x + mapBox.width) continue;
        if (pt.y < mapBox.y || pt.y > mapBox.y + mapBox.height) continue;
        if (underBadge(pt)) continue;
        const res = await page.request.get(
          `${apiBase}/projects/${projectId}/analyses/ndvi/point?lon=${lon}&lat=${lat}`,
          { headers: authHeaders }
        );
        const body = await res.json();
        if (body.value != null) {
          target = { lat, lon, pt };
          break outer;
        }
      }
    }
    expect(target, "no candidate lon/lat in the Boundary's bounds returned a real NDVI value").not.toBeNull();

    let ndviRow = null;
    // A tight jitter around the computed pixel absorbs any small rounding
    // error in the badge's displayed (5-decimal) lat/lon/zoom read above.
    for (const [dx, dy] of [
      [0, 0],
      [-2, 0],
      [2, 0],
      [0, -2],
      [0, 2],
    ]) {
      await page.mouse.click(target.pt.x + dx, target.pt.y + dy);
      await expect(popup).toBeVisible();
      // inspectPixel is async (real GET .../point round trips, one per
      // active target) - the popup stays visible with its PREVIOUS content
      // while "Reading pixel…" loads, so checking rows immediately would
      // read stale data from the last click, not this one.
      await expect(popup).not.toContainText("Reading pixel", { timeout: 60_000 });
      const row = popup.locator(".pixel-popup-row", { hasText: "NDVI" });
      if ((await row.count()) > 0 && !(await row.innerText()).includes("No data at this point")) {
        ndviRow = row;
        break;
      }
    }
    expect(ndviRow, "no candidate point returned a real NDVI value - all were nodata").not.toBeNull();

    // The row is labeled by analysis name (not a raw layer id/date - GEE rows
    // use activeAnalysis.name, not layer.type) and shows a plausible NDVI
    // reading, not "Run 'NDVI' first" (this ran successfully above) and not
    // a raw class code (NDVI has no legend - only the land-cover analyses
    // translate through gee_class_legends.py).
    const rowText = await ndviRow.innerText();
    expect(rowText).not.toContain("Run 'NDVI' first");
    const [, valueText] = rowText.match(/(-?\d+\.\d+)\s*NDVI/) ?? [];
    expect(valueText, `NDVI row text was: ${rowText}`).toBeDefined();
    const value = Number(valueText);
    expect(value).toBeGreaterThan(-1);
    expect(value).toBeLessThan(1);

    if (process.env.ANALYSIS_SHOT) await page.screenshot({ path: process.env.ANALYSIS_SHOT });
  });
});

// Not covered here: the refresh button's role gating (a plain canUpload check
// in AnalysisPanel.jsx). The seeded QA project only has qa_gis as a member,
// and a non-member Viewer can't open the project at all (project-level RBAC),
// so asserting it would mean seeding a Viewer membership for a one-line check.
