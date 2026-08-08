import { readFileSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";
import { test, expect } from "@playwright/test";
import {
  login, readTokens, ADMIN, GIS_ASSOCIATE, QA_PROJECT_NAME, API_BASE, collectConsoleErrors, openMapPanels,
  clickMapToActivate,
} from "./helpers.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// New-in-this-redesign coverage (Phase 1: icon system, admin layer rename,
// measure units, draw tools, popover scroll fix; Phase 2: visual redesign,
// per-project dashboard, map toolbar). Reuses the QA_PROJECT_NAME fixture
// project global-setup.js seeds (2 dated classified LULC layers - one
// 3-class, one 9-class - a raw raster, and a small vector boundary) rather
// than each test building its own data - see global-setup.js's seedProject.

async function gotoQaProject(page, { role = ADMIN } = {}) {
  await login(page, role);
  await page.goto("/projects");
  await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
  await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  // Wave: floating map controls - the toolbar/Layers panel are collapsed by
  // default (floating overlays, not always-visible docked chrome), but almost
  // every test in this file was written against the old always-open docked
  // versions and reaches straight into their contents - open both here once
  // instead of touching every individual test.
  await openMapPanels(page);
  // The map's initial fitBounds (fitting the QA project's seeded layers) pans
  // and zooms on load - under load (the full suite, not this file run in
  // isolation) that animation can still be settling when a test starts
  // interacting, so "save current view" captures a mid-transition center/zoom
  // that never matches the readout's later, fully-settled value (confirmed:
  // reproduces 2/2 in the full suite, 0/3 isolated). Wait for two consecutive
  // identical reads before treating the view as ready to interact with.
  const readout = page.locator(".map-coord-badge");
  await expect(async () => {
    const a = await readout.textContent();
    await page.waitForTimeout(150);
    const b = await readout.textContent();
    expect(a).toEqual(b);
    expect(a).not.toContain("—");
  }).toPass({ timeout: 10000 });
}

async function openLayerInfoPopover(page, layerRowNamePattern) {
  const row = page.locator(".layer-row", { hasText: layerRowNamePattern }).first();
  await row.getByRole("button", { name: "Layer info" }).click();
  return page.locator(".symbology-popover", { hasText: "layer info" });
}

test.describe("Admin-only layer rename", () => {
  test("Administrator can rename a layer, and the new name shows in the layer row", async ({ page }) => {
    await gotoQaProject(page);
    const popover = await openLayerInfoPopover(page, "LULC · 2023-06-01");
    const input = popover.getByLabel("Layer name");
    await expect(input).toBeVisible();
    await input.fill("QA Renamed 3-class LULC");
    await popover.getByRole("button", { name: "Save" }).click();
    await expect(popover.getByText("Saving…")).toHaveCount(0);
    await popover.getByRole("button", { name: "Close" }).click();
    await expect(page.locator(".layer-row", { hasText: "QA Renamed 3-class LULC" })).toBeVisible();
  });

  test("GIS Associate sees the layer name as plain text, no rename field", async ({ page }) => {
    await gotoQaProject(page, { role: GIS_ASSOCIATE });
    const popover = await openLayerInfoPopover(page, /LULC/);
    await expect(popover.getByLabel("Layer name")).toHaveCount(0);
    await expect(popover.locator("dt", { hasText: "Name" })).toBeVisible();
  });
});

test.describe("Measure tools: unit selector", () => {
  test("switching distance units changes the displayed unit and persists across reload", async ({ page }) => {
    const map = (await gotoQaProject(page), page.locator(".leaflet-container"));
    await clickMapToActivate(map);
    await page.getByRole("button", { name: /Measure/i }).click();
    await page.getByRole("button", { name: "Distance" }).click();
    const box = await map.boundingBox();
    // Right-of-center (>0.6) - the open floating toolbar panel spans roughly
    // the left ~57% of the map canvas width (confirmed via a real run's
    // failure screenshot) and would otherwise intercept these clicks.
    await map.click({ position: { x: box.width * 0.7, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.9, y: box.height * 0.6 } });
    const result = page.locator(".measure-result");
    await expect(result).toContainText(/\bm\b/);

    const unitSelect = page.getByLabel("Distance units");
    await unitSelect.selectOption("km");
    await expect(result).toContainText(/km/);

    // Preference persists (lib/measure.js's storeUnit -> localStorage), so a
    // reload defaults straight to km instead of resetting to meters.
    await page.reload();
    await clickMapToActivate(map);
    await page.getByRole("button", { name: /Measure/i }).click();
    await page.getByRole("button", { name: "Distance" }).click();
    await expect(page.getByLabel("Distance units")).toHaveValue("km");
  });
});

test.describe("Draw tools", () => {
  test("drawing a polygon and downloading it produces a shapefile zip", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await clickMapToActivate(map);
    await page.getByRole("button", { name: "Draw" }).click();
    await page.getByRole("button", { name: "Polygon" }).click();
    const box = await map.boundingBox();
    // Right-of-center - see the Measure tools test above for why.
    await map.click({ position: { x: box.width * 0.65, y: box.height * 0.25 } });
    await map.click({ position: { x: box.width * 0.9, y: box.height * 0.25 } });
    await map.click({ position: { x: box.width * 0.78, y: box.height * 0.55 } });
    await page.getByRole("button", { name: "Finish" }).click();
    await page.getByLabel("Shape name").fill("qa-drawn-shape");

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download shapefile" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe("qa-drawn-shape.zip");
  });

  test("drawing a point and saving it to the project adds an Added layer", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await clickMapToActivate(map);
    await page.getByRole("button", { name: "Draw" }).click();
    await page.getByRole("button", { name: "Point" }).click();
    const box = await map.boundingBox();
    // Single click IS the whole shape for point mode (ProjectMap.jsx's
    // addDrawPoint) - "finished" flips true with no separate Finish click.
    // Right-of-center, not the dead center - see the Measure tools test
    // above for why.
    await map.click({ position: { x: box.width * 0.75, y: box.height / 2 } });
    const shapeName = `qa-saved-point-${Date.now()}`;
    await page.getByLabel("Shape name").fill(shapeName);
    await page.getByRole("button", { name: "Save to project" }).click();
    await expect(page.getByText("Saved to project.")).toBeVisible({ timeout: 15000 });

    const addedGroup = page.getByRole("button", { name: /Added layers/i });
    await expect(addedGroup).toBeVisible();
    await expect(page.locator(".layer-row", { hasText: shapeName })).toBeVisible();
  });
});

test.describe("Symbology popover scroll (9-class LULC)", () => {
  test("a 9-class legend scrolls in the middle only - header and footer stay put", async ({ page }) => {
    await gotoQaProject(page);
    const popover = await (async () => {
      const row = page.locator(".layer-row", { hasText: "LULC · 2024-06-01" }).first();
      await row.getByRole("button", { name: "Visualization parameters" }).click();
      return page.locator(".symbology-popover", { hasText: "visualization parameters" });
    })();
    await expect(popover).toBeVisible();
    await popover.getByRole("button", { name: "Edit classes" }).click();
    await expect(popover.locator(".legend-row")).toHaveCount(9);

    const header = popover.locator(".symbology-popover-header");
    // ">" (direct child): ClassLegendEditor's own Cancel/Save-classes row
    // reuses the SAME "symbology-popover-footer" class name one level deeper
    // (inside .symbology-popover-scroll) - a real naming collision, confirmed
    // via strict-mode violation - so a plain class locator here matches 2
    // elements. The popover's own Close/Apply footer is a direct child of
    // .symbology-popover; the nested one isn't.
    const footer = popover.locator("> .symbology-popover-footer");
    const headerBoxBefore = await header.boundingBox();
    const footerBoxBefore = await footer.boundingBox();

    const scroller = popover.locator(".symbology-popover-scroll");
    await scroller.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });

    await expect(header).toBeVisible();
    await expect(footer).toBeVisible();
    const headerBoxAfter = await header.boundingBox();
    const footerBoxAfter = await footer.boundingBox();
    expect(headerBoxAfter).toEqual(headerBoxBefore);
    expect(footerBoxAfter).toEqual(footerBoxBefore);
  });

  test("popover always resolves to the shared above-map-chrome z-index tier, expanded or after a collapse/re-expand cycle", async ({ page }) => {
    await gotoQaProject(page);
    const panelToggle = page.getByRole("button", { name: "Hide the Layers panel" });

    // Wave: floating map controls restructured .map-overlay-topleft so the
    // floating Layers panel (and this popover, anchored off it) always
    // renders BELOW the toggle row/toolbar panel now, not beside them at the
    // same Y - the two literally don't share screen space to overlap any
    // more (confirmed: their bounding boxes never intersect in the new
    // layout, toolbar open or collapsed). The real invariant the original
    // bug was about - this popover must always resolve above map-overlay-
    // topleft's whole chrome tier, wherever it happens to sit on screen - is
    // still real and still worth guarding, just checked directly via the
    // shared z-index tokens (see index.css's --z-map-chrome/--z-map-overlay)
    // instead of a screen-position collision that no longer occurs.
    async function openPopoverAndAssertAboveChrome() {
      const row = page.locator(".layer-row", { hasText: "LULC · 2024-06-01" }).first();
      await row.getByRole("button", { name: "Visualization parameters" }).click();
      const popover = page.locator(".symbology-popover", { hasText: "visualization parameters" });
      await expect(popover).toBeVisible();
      const title = popover.locator(".symbology-popover-header");
      await expect(title).toContainText("LULC");

      const [popoverZ, chromeZ] = await Promise.all([
        popover.evaluate((el) => Number(getComputedStyle(el).zIndex)),
        page.locator(".map-overlay-topleft").evaluate((el) => Number(getComputedStyle(el).zIndex)),
      ]);
      expect(popoverZ).toBeGreaterThan(chromeZ);

      await popover.getByRole("button", { name: "Close" }).click();
    }

    // Panel expanded (the reported case).
    await openPopoverAndAssertAboveChrome();

    // Collapse, then re-expand - a basic regression-safety re-run confirming
    // the popover still renders (and still resolves above chrome) after a
    // full collapse/reopen cycle of the panel it's anchored off.
    await panelToggle.click();
    await page.getByRole("button", { name: "Show the Layers panel" }).click();
    await openPopoverAndAssertAboveChrome();
  });
});

test.describe("Map toolbar: new capabilities", () => {
  test("Compare shows a before/after swipe divider once 2+ dated layers exist", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await clickMapToActivate(map);
    // Wave: toolbar overflow - Compare now lives inside the "More" dropdown,
    // not directly on the main toolbar row.
    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("button", { name: "Compare" }).click();
    await expect(page.locator(".map-swipe-divider")).toBeVisible();
    await expect(page.getByRole("button", { name: "Before" })).toHaveCount(0); // labels are plain text, not buttons
    await expect(page.getByText("Before")).toBeVisible();
    await expect(page.getByText("After")).toBeVisible();
  });

  test("Jump to coordinates moves the map and rejects garbage input", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await clickMapToActivate(map);
    // Wave: toolbar overflow - the lat/lon field is now collapsed behind a
    // pin icon until clicked (JumpToCoords's own `open` state).
    await page.getByRole("button", { name: "Go to coordinates" }).click();
    const input = page.getByLabel("Go to coordinates (latitude, longitude)");
    const readout = page.locator(".map-coord-badge");
    await input.fill("12.9716, 77.5946");
    await page.getByRole("button", { name: "Go" }).click();
    await expect(readout).toContainText("Lat: 12.9716");

    // Confused-user case: garbage input must be rejected inline, not silently
    // move the map somewhere meaningless.
    await input.fill("not a coordinate");
    await page.getByRole("button", { name: "Go" }).click();
    await expect(input).toHaveAttribute("aria-invalid", "true");
    await expect(readout).toContainText("Lat: 12.9716"); // unchanged
  });

  test("click-to-copy on the coordinate readout shows a confirmation", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await clickMapToActivate(map);
    await page.locator(".map-toolbar-copy").click();
    await expect(page.getByText("Copied!")).toBeVisible();
  });

  test("Save image downloads a PNG of the current view", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await clickMapToActivate(map);
    // Wave: toolbar overflow - Save image now lives inside "More".
    await page.getByRole("button", { name: "More" }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 15000 }),
      page.getByRole("button", { name: /Save image/ }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^map-\d{4}-\d{2}-\d{2}\.png$/);
  });

  test("Saved views: save the current view, jump to it, then remove it", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    // The readout shows the last-CLICKED point (mapView.pos), not the map's
    // true center (mapView.center) - "+ Save current view" saves the latter.
    // A hardcoded (400, 300) only happened to land near center at the map
    // canvas's PRE-toolbar-overflow height; Wave: toolbar overflow shrank the
    // canvas (100px toolbar vs 41px), shifting (400, 300) further from true
    // center and exposing the pos/center gap as a real mismatch after
    // recall. Click the map's actual center instead, so pos and center are
    // the same point regardless of canvas size.
    //
    // Wave: floating map controls - gotoQaProject leaves both floating
    // panels open, and unlike every other map-click in this suite, THIS
    // click genuinely needs to land at the canvas's true pixel center (not
    // just anywhere clear of the panels) for pos===center to hold - so
    // close both panels first instead of relocating the click. A real user
    // could do exactly this (collapse both, click center, reopen the
    // toolbar for Views below).
    await page.getByRole("button", { name: "Hide map tools" }).click();
    await page.getByRole("button", { name: "Hide the Layers panel" }).click();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width / 2, y: box.height / 2 } });
    await page.getByRole("button", { name: "Show map tools" }).click();

    // window.prompt() has no Playwright-native handler (unlike dialog()) for
    // a plain synchronous prompt - stub it directly on the already-loaded
    // page instead of addInitScript (which only applies to FUTURE
    // navigations, not this already-open one).
    await page.evaluate(() => {
      window.prompt = () => "QA saved view";
    });
    // Wave: toolbar overflow - Views now lives inside "More"; opening it once
    // here is enough for the rest of this test (More itself stays open the
    // whole time - clicking a NESTED trigger like Views only toggles its own
    // sub-picker, never the outer More panel, same "one open at a time, one
    // level deeper" reasoning as MoreMenu's own subOpen state).
    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("button", { name: "Views" }).click();
    await page.getByRole("button", { name: "+ Save current view" }).click();
    // The menu stays open after saving (BookmarksMenu's save() doesn't close
    // it) - no second "Views" click needed, and one would actually TOGGLE IT
    // CLOSED (confirmed manually), hiding the very row this asserts on next.
    // exact: true - a plain substring match also hits "Remove QA saved view"
    // (the row's own remove button).
    const savedRow = page.getByRole("button", { name: "QA saved view", exact: true });
    await expect(savedRow).toBeVisible();

    // Change zoom away from the saved value first, so clicking the bookmark
    // is a real, verifiable recall (not a no-op because nothing moved).
    // jumpTo() now closes any open Identify popup before setView (fixed:
    // its auto-pan was fighting recall/coordinate-jump alike) - this
    // assertion stays zoom-only anyway since that's sufficient to prove the
    // bookmark mechanism itself works; full lat/lon jump behavior is already
    // covered by the "Jump to coordinates" test above.
    const readout = page.locator(".map-coord-badge");
    const zoomBefore = await readout.textContent();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(async () => expect(await readout.textContent()).not.toEqual(zoomBefore)).toPass();
    // Leaflet's zoomIn() animates (~250ms CSS transition). If recall's
    // setView() lands while that animation is still resolving, the OLD
    // animation's own completion can fire its zoomend AFTER our new setView
    // and silently override it back toward the zoom-in target - confirmed:
    // this exact race reproduced the SAME wrong zoom every time, not random
    // noise, meaning it was deterministic given the animation's fixed
    // duration, not a one-off flake. Wait for the zoom-in transition to
    // fully settle - THREE consecutive identical reads (not two), a longer
    // gap between them, and a longer overall budget: two-reads-150ms-apart
    // occasionally still landed inside the transition under load (confirmed:
    // reproduces under a busy host, not just in theory) and let the stale
    // zoomend outlive this check, silently overriding the recall below.
    await expect(async () => {
      const a = await readout.textContent();
      await page.waitForTimeout(250);
      const b = await readout.textContent();
      await page.waitForTimeout(250);
      const c = await readout.textContent();
      expect(a).toEqual(b);
      expect(b).toEqual(c);
    }).toPass({ timeout: 8000 });
    await savedRow.click();
    // Recall reapplies the bookmark's exact stored lat/lon/zoom, but a
    // combined pan+zoom setView() re-derives the resulting center through
    // Leaflet's own pixel<->latlng transform at the new zoom level, which can
    // introduce a tiny sub-degree rounding difference from the original
    // (confirmed: zoom always matches exactly, lat/lon only off in the
    // 3rd-4th decimal - not the race the comment above guards against, which
    // would show a completely different, unsettled zoom). A byte-exact text
    // match was never really what "recall" promises - compare parsed values
    // with a tolerance instead.
    function parseReadout(text) {
      const m = text.match(/Lat: (-?\d+\.\d+)° Lon: (-?\d+\.\d+)° \| Zoom: (\d+)/);
      return { lat: Number(m[1]), lon: Number(m[2]), zoom: Number(m[3]) };
    }
    const before = parseReadout(zoomBefore);
    await expect(async () => {
      const after = parseReadout(await readout.textContent());
      expect(after.zoom).toBe(before.zoom);
      expect(after.lat).toBeCloseTo(before.lat, 1);
      expect(after.lon).toBeCloseTo(before.lon, 1);
    }).toPass({ timeout: 5000 });

    await page.getByRole("button", { name: "Views" }).click();
    await page.getByRole("button", { name: "Remove QA saved view" }).click();
    await expect(page.getByText("No saved views yet")).toBeVisible();
  });

  test("only one toolbar dropdown is open at a time, each anchored under its own button", async ({ page }) => {
    await gotoQaProject(page);
    const menus = page.locator(".map-toolbar-menu");

    // Every open menu must sit directly under ITS OWN trigger button. The CSS
    // does this on its own (.map-toolbar-menu is absolute inside the button's
    // own position:relative .map-toolbar-dropdown wrapper) - this asserts it
    // stays true rather than assuming it. `menus.last()` for the nested case
    // (Wave: toolbar overflow): Compare/Views' own picker renders as a
    // DESCENDANT of More's outer panel, so it's the LAST .map-toolbar-menu in
    // document order once both are open.
    async function expectOnlyMenuUnder(buttonName, { count = 1 } = {}) {
      await expect(menus).toHaveCount(count);
      const trigger = page.getByRole("button", { name: buttonName, exact: true });
      const btn = await trigger.boundingBox();
      const menu = await menus.last().boundingBox();
      expect(Math.abs(menu.x - btn.x)).toBeLessThan(2); // left-aligned with its trigger
      expect(menu.y).toBeGreaterThanOrEqual(btn.y + btn.height); // below it, not over it
      const viewport = page.viewportSize();
      expect(menu.x + menu.width).toBeLessThanOrEqual(viewport.width); // not clipped off-screen
    }

    // The reported bug: open Measure, pick nothing, then open Draw - both used
    // to stay open (four independent local useState flags), overlapping each
    // other and the floating measure/draw instruction banner underneath.
    await page.getByRole("button", { name: "Measure" }).click();
    await expectOnlyMenuUnder("Measure");
    await expect(page.getByRole("button", { name: "Distance" })).toBeVisible();

    await page.getByRole("button", { name: "Draw" }).click();
    await expectOnlyMenuUnder("Draw");
    await expect(page.getByRole("button", { name: "Distance" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Polygon" })).toBeVisible();

    // Opening More (Wave: toolbar overflow) closes Draw at the top level -
    // still just 1 menu, since Compare/Views inside it aren't expanded yet.
    await page.getByRole("button", { name: "More" }).click();
    await expectOnlyMenuUnder("More");
    await expect(page.getByRole("button", { name: "Polygon" })).toHaveCount(0);

    // Nested level: Views' own picker is a SECOND, independent
    // single-open-at-a-time state local to More (MoreMenu's `subOpen`) -
    // opening it doesn't close More itself, so now there are 2 (More's own
    // container + Views' nested one).
    await page.getByRole("button", { name: "Views" }).click();
    await expectOnlyMenuUnder("Views", { count: 2 });

    // The basemap picker is a native <select>, not a custom positioned menu, so
    // it can't overlap anything - but it must not leave a stale toolbar menu
    // behind it either.
    await page.getByLabel("Basemap").selectOption({ index: 1 });
    await expect(menus).toHaveCount(2); // More + Views still open; nothing new stacked on them

    // Clicking Views again closes just its own nested picker - back to 1
    // (More itself stays open).
    await page.getByRole("button", { name: "Views" }).click();
    await expect(menus).toHaveCount(1);

    // Clicking More's own button closes the whole thing - back to zero menus.
    await page.getByRole("button", { name: "More" }).click();
    await expect(menus).toHaveCount(0);
  });

  test("whole-panel collapse hides and reshows the floating Layers panel", async ({ page }) => {
    await gotoQaProject(page);
    const toggle = page.getByRole("button", { name: /Hide the Layers panel|Show the Layers panel/ });
    await expect(toggle).toHaveAttribute("aria-label", "Hide the Layers panel");
    await expect(page.locator(".layers-panel")).toBeVisible();
    await toggle.click();
    await expect(page.locator(".layers-panel")).toHaveCount(0);
    await expect(toggle).toHaveAttribute("aria-label", "Show the Layers panel");
    await toggle.click();
    await expect(page.locator(".layers-panel")).toBeVisible();
  });
});

test.describe("Per-project Dashboard tab", () => {
  async function gotoDashboardTab(page) {
    await gotoQaProject(page);
    await page.getByRole("button", { name: "Dashboard" }).click();
  }

  test("Maps/Dashboard toggle switches content", async ({ page }) => {
    await gotoQaProject(page);
    await expect(page.locator(".leaflet-container")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Land cover composition" })).toHaveCount(0);
    await page.getByRole("button", { name: "Dashboard" }).click();
    await expect(page.getByRole("heading", { name: "Land cover composition" })).toBeVisible();
    await expect(page.locator(".leaflet-container")).toHaveCount(0);
    await page.getByRole("button", { name: "Maps" }).click();
    await expect(page.locator(".leaflet-container")).toBeVisible();
  });

  test("dataset selector isolates land-cover composition to ONE selected layer - no cross-layer mixing", async ({ page }) => {
    await gotoDashboardTab(page);
    const select = page.getByLabel("Dataset");
    await expect(select).toBeVisible();

    // humanizeMetricName (lib/format.js) round-trips a class label through
    // the backend's metric_key slugification (raster.py) and back - any "/"
    // or "-" in the original label (e.g. "Urban/Built-up") is lost in that
    // round-trip and comes back space-separated ("Urban built up"). Matching
    // on the ACTUAL rendered text, not the original seed label.
    await select.selectOption({ label: "LULC · 2023-06-01" });
    const table = page.locator(".composition-table");
    await expect(table.locator("tbody tr")).toHaveCount(3);
    await expect(table).toContainText("Forest");
    await expect(table).toContainText("Water");
    await expect(table).toContainText("Urban built up");
    await expect(table).not.toContainText("Cropland");
    await expect(table).not.toContainText("Grassland");

    await select.selectOption({ label: "LULC · 2024-06-01" });
    await expect(table.locator("tbody tr")).toHaveCount(9);
    await expect(table).toContainText("Cropland agriculture");
    await expect(table).toContainText("Grassland");
    await expect(table).toContainText("Snow ice");
  });

  test("Forest cover trend renders a real chart across the 2 dated layers", async ({ page }) => {
    await gotoDashboardTab(page);
    const panel = page.locator("section.panel", { hasText: "Forest cover trend" });
    await expect(panel.locator("svg.recharts-surface")).toBeVisible();
    await expect(panel.getByText("No forest-class data yet")).toHaveCount(0);
  });

  test("Data quality summary is plain text - no badge/pill markup", async ({ page }) => {
    await gotoDashboardTab(page);
    const panel = page.locator("section.panel", { hasText: "Data quality" });
    const lines = panel.locator(".data-quality-line");
    await expect(lines).toHaveCount(2);
    await expect(lines.nth(0)).toContainText(/layers need re-ingestion/);
    await expect(lines.nth(1)).toContainText(/layers verified/);
    // No leftover pill/badge class on this card.
    await expect(panel.locator(".status-badge, .badge, .pill")).toHaveCount(0);
  });

  test("Monitoring periods lists the dated layers", async ({ page }) => {
    await gotoDashboardTab(page);
    const panel = page.locator("section.panel", { hasText: "Monitoring periods" });
    await expect(panel.locator(".monitoring-list li")).toHaveCount(2);
  });

  test("Project completeness checklist renders", async ({ page }) => {
    await gotoDashboardTab(page);
    const panel = page.locator("section.panel", { hasText: "Project completeness" });
    await expect(panel.locator(".checklist-list li")).toHaveCount(5);
    await expect(panel.getByText("Has at least one classified layer")).toBeVisible();
  });

  test("Recent activity feed shows a rename action after renaming a layer", async ({ page }) => {
    await gotoQaProject(page);
    const uniqueName = `QA activity check ${Date.now()}`;
    const popover = await openLayerInfoPopover(page, /Satellite|Raw/);
    await popover.getByLabel("Layer name").fill(uniqueName);
    await popover.getByRole("button", { name: "Save" }).click();
    await expect(popover.getByText("Saving…")).toHaveCount(0);
    await popover.getByRole("button", { name: "Close" }).click();

    await page.getByRole("button", { name: "Dashboard" }).click();
    const feedPanel = page.locator("section.panel", { hasText: "Recent activity" });
    await expect(feedPanel.locator(".activity-item").first()).toContainText(/renamed/i);
  });

  test("Export to Excel downloads a real .xlsx file", async ({ page }) => {
    await gotoDashboardTab(page);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Export to Excel" }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
  });
});

test.describe("Header / sidebar / login branding", () => {
  test("VNV logo is visible on the login screen and in the app header", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("img", { name: /VNV/i })).toBeVisible();
    await login(page, ADMIN);
    await expect(page.getByRole("img", { name: /VNV/i })).toBeVisible();
  });

  test("sidebar collapse persists (icon-only) across navigation", async ({ page }) => {
    await login(page, ADMIN);
    const toggle = page.getByRole("button", { name: /Collapse sidebar|Expand sidebar/ });
    await expect(toggle).toHaveAttribute("aria-label", "Collapse sidebar");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-label", "Expand sidebar");
    await expect(page.locator(".shell-sidebar-collapsed")).toBeVisible();

    await page.goto("/upload");
    await expect(page.locator(".shell-sidebar-collapsed")).toBeVisible();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();

    // Reset back open so it doesn't leak into a later test via sessionStorage
    // (fresh per Playwright test context, but keep this test self-contained).
    await page.getByRole("button", { name: "Expand sidebar" }).click();
  });
});

test.describe("Press-feedback (:active scale/opacity) doesn't break click handlers", () => {
  test("primary, ghost, danger, icon, and map-toolbar buttons each fire their action exactly once per click", async ({ page }) => {
    await gotoQaProject(page);

    // map-toolbar-btn: Zoom in changes the readout exactly once per click.
    const readout = page.locator(".map-coord-badge");
    const zoomBefore = await readout.textContent();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(async () => expect(await readout.textContent()).not.toEqual(zoomBefore)).toPass();

    // icon-button: the whole-Layers-panel collapse toggles exactly once, not
    // twice back to its starting state (which a swallowed/duplicated click
    // would look identical to if untested).
    const panelToggle = page.getByRole("button", { name: "Hide the Layers panel" });
    await panelToggle.click();
    await expect(page.locator(".layers-panel")).toHaveCount(0);
    await page.getByRole("button", { name: "Show the Layers panel" }).click();
    await expect(page.locator(".layers-panel")).toBeVisible();

    // ghost-button: layer visibility checkbox (native, but styled through the
    // same press-feedback rule) - one click, one state flip.
    const checkbox = page.locator(".layer-row-checkbox").first();
    const wasChecked = await checkbox.isChecked();
    await checkbox.click();
    await expect(checkbox).toBeChecked({ checked: !wasChecked });

    // danger-button: opens the delete-project confirmation exactly once
    // (not twice, which would be harmless here but would indicate the same
    // double-fire risk elsewhere on an action that ISN'T idempotent).
    await page.getByRole("button", { name: "Delete project" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await page.getByRole("dialog").getByRole("button", { name: /cancel/i }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();

    // primary-button: Apply on the symbology popover closes it in one click.
    await page.locator(".layer-row-gear").first().click();
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.locator(".symbology-popover")).toHaveCount(0);
  });
});

test.describe("Status display: pill/badge markup removed app-wide", () => {
  test("StatusBadge is plain colored text + dot, no pill background/border", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/projects");
    const badge = page.locator(".status-badge").first();
    await expect(badge).toBeVisible();
    await expect(badge.locator(".status-dot")).toBeVisible();
    const style = await badge.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { bg: cs.backgroundColor, radius: cs.borderRadius, padding: cs.padding, border: cs.borderStyle };
    });
    expect(style.bg).toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
    expect(style.padding).toBe("0px");
    expect(style.border).toBe("none");
  });
});

test.describe("Delete-a-dataset (Administrator-only)", () => {
  /** Uploads ONE fresh, disposable raster into the shared QA_PROJECT_NAME
   * project via the real upload API + real worker - not the seeded layers
   * every other spec in this file depends on (deleting one of those would
   * break tests that run later in the same session). Mirrors global-setup's
   * own uploadAndWait exactly, scoped to just this describe block since
   * nothing else needs a throwaway dataset. Returns the new layer_id. */
  async function uploadThrowawayDataset(accessToken) {
    const file = "qa-raw-imagery.tif";
    const bytes = readFileSync(resolve(__dirname, "fixtures", file));
    const form = new FormData();
    form.set("file", new Blob([bytes]), file);
    const fields = {
      project_name: QA_PROJECT_NAME, region: "Karnataka",
      dataset_type: "Satellite / Raw Imagery", source: "delete-test-throwaway",
      classification_method: "", date_processed: "2022-01-01", pixel_size_m: "10",
    };
    for (const [k, v] of Object.entries(fields)) form.set(k, v);

    const uploadRes = await fetch(`${API_BASE}/datasets/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
      body: form,
    });
    if (!uploadRes.ok) throw new Error(`throwaway upload failed: ${uploadRes.status} ${await uploadRes.text()}`);
    const { job_id } = await uploadRes.json();

    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      const jobRes = await fetch(`${API_BASE}/jobs/${job_id}`, { headers: { Authorization: `Bearer ${accessToken}` } });
      const job = await jobRes.json();
      if (job.status === "succeeded") {
        const layersRes = await fetch(`${API_BASE}/projects/${job.result.project_id}/layers`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        const layers = (await layersRes.json()).layers;
        const layer = layers.find((l) => l.date_processed === "2022-01-01");
        return { projectId: job.result.project_id, layerId: layer.layer_id, previewUrl: layer.preview_url };
      }
      if (["failed", "dead_letter"].includes(job.status)) throw new Error(`throwaway ingest failed: ${JSON.stringify(job.error)}`);
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("throwaway ingest did not reach a terminal status within 30s");
  }

  test("Administrator sees the delete option, confirms with the dataset's name shown, and the file is actually gone", async ({ page }) => {
    const { access_token } = readTokens(ADMIN.username);
    const { previewUrl } = await uploadThrowawayDataset(access_token);

    // The file genuinely exists before delete - otherwise "gone after" is a
    // tautology, not a real assertion.
    const before = await fetch(`${API_BASE.replace("/api/v1", "")}${previewUrl}`);
    expect(before.status).toBe(200);

    await login(page, ADMIN);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    await expect(page).toHaveURL(/\/projects\/[\w-]+/);
    await openMapPanels(page);

    const row = page.locator(".layer-row", { hasText: "2022-01-01" });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Delete this dataset" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // The dataset's name, not a raw id - the whole point of the confirmation.
    await expect(dialog).toContainText("Satellite / Raw Imagery");
    await expect(dialog).toContainText("2022-01-01");
    await dialog.getByRole("button", { name: "Delete" }).click();

    await expect(row).toHaveCount(0);

    const after = await fetch(`${API_BASE.replace("/api/v1", "")}${previewUrl}`);
    expect(after.status).toBe(404);
  });

  test("Non-administrator never sees the delete option on a formal dataset", async ({ page }) => {
    await login(page, GIS_ASSOCIATE);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    await expect(page).toHaveURL(/\/projects\/[\w-]+/);
    await openMapPanels(page);
    await expect(page.locator(".layer-row").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete this dataset" })).toHaveCount(0);
  });
});
