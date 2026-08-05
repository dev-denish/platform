import { test, expect } from "@playwright/test";
import { login, ADMIN, GIS_ASSOCIATE, QA_PROJECT_NAME, collectConsoleErrors } from "./helpers.js";

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
  // The map's initial fitBounds (fitting the QA project's seeded layers) pans
  // and zooms on load - under load (the full suite, not this file run in
  // isolation) that animation can still be settling when a test starts
  // interacting, so "save current view" captures a mid-transition center/zoom
  // that never matches the readout's later, fully-settled value (confirmed:
  // reproduces 2/2 in the full suite, 0/3 isolated). Wait for two consecutive
  // identical reads before treating the view as ready to interact with.
  const readout = page.locator(".map-toolbar-readout");
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
    await map.click({ position: { x: 400, y: 300 } });
    await page.getByRole("button", { name: /Measure/i }).click();
    await page.getByRole("button", { name: "Distance" }).click();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.6, y: box.height * 0.5 } });
    const result = page.locator(".measure-result");
    await expect(result).toContainText(/\bm\b/);

    const unitSelect = page.getByLabel("Distance units");
    await unitSelect.selectOption("km");
    await expect(result).toContainText(/km/);

    // Preference persists (lib/measure.js's storeUnit -> localStorage), so a
    // reload defaults straight to km instead of resetting to meters.
    await page.reload();
    await map.click({ position: { x: 400, y: 300 } });
    await page.getByRole("button", { name: /Measure/i }).click();
    await page.getByRole("button", { name: "Distance" }).click();
    await expect(page.getByLabel("Distance units")).toHaveValue("km");
  });
});

test.describe("Draw tools", () => {
  test("drawing a polygon and downloading it produces a shapefile zip", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await map.click({ position: { x: 400, y: 300 } });
    await page.getByRole("button", { name: "Draw" }).click();
    await page.getByRole("button", { name: "Polygon" }).click();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.6, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.45, y: box.height * 0.55 } });
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
    await map.click({ position: { x: 400, y: 300 } });
    await page.getByRole("button", { name: "Draw" }).click();
    await page.getByRole("button", { name: "Point" }).click();
    const box = await map.boundingBox();
    // Single click IS the whole shape for point mode (ProjectMap.jsx's
    // addDrawPoint) - "finished" flips true with no separate Finish click.
    await map.click({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
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

  test("popover paints above the map's panel-collapse toggle, expanded or after a collapse/re-expand cycle", async ({ page }) => {
    await gotoQaProject(page);
    const panelToggle = page.getByRole("button", { name: "Hide the Layers panel" });

    async function openPopoverAndAssertOnTop() {
      const row = page.locator(".layer-row", { hasText: "LULC · 2024-06-01" }).first();
      await row.getByRole("button", { name: "Visualization parameters" }).click();
      const popover = page.locator(".symbology-popover", { hasText: "visualization parameters" });
      await expect(popover).toBeVisible();
      // Their bounding boxes overlapping is EXPECTED and fine (the popover
      // opens right where .map-overlay-topleft sits) - a plain bounding-box
      // check would flag that as "overlap" even with paint order already
      // correct. What actually broke before the fix was the toggle button
      // painting on top and clipping the popover's own title text; the
      // real regression check is which element the browser hit-tests at
      // the overlap point, not whether the boxes touch.
      const title = popover.locator(".symbology-popover-header");
      await expect(title).toContainText("LULC");
      const titleBox = await title.boundingBox();
      const toggleBox = await panelToggle.boundingBox();
      const overlapX = Math.max(titleBox.x, toggleBox.x) + Math.min(titleBox.x + titleBox.width, toggleBox.x + toggleBox.width);
      const overlapY = Math.max(titleBox.y, toggleBox.y) + Math.min(titleBox.y + titleBox.height, toggleBox.y + toggleBox.height);
      const topElementIsPopover = await page.evaluate(
        ([x, y]) => {
          const el = document.elementFromPoint(x / 2, y / 2);
          return !!el?.closest(".symbology-popover");
        },
        [overlapX, overlapY]
      );
      expect(topElementIsPopover).toBe(true);
      await popover.getByRole("button", { name: "Close" }).click();
    }

    // Panel expanded (the reported case).
    await openPopoverAndAssertOnTop();

    // Collapse, then re-expand - the toggle button's own screen position
    // shifts (it's anchored to the map canvas, which widens/narrows as the
    // docked Layers column disappears/reappears), so this isn't redundant
    // with the check above.
    await panelToggle.click();
    await page.getByRole("button", { name: "Show the Layers panel" }).click();
    await openPopoverAndAssertOnTop();
  });
});

test.describe("Map toolbar: new capabilities", () => {
  test("Compare shows a before/after swipe divider once 2+ dated layers exist", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await map.click({ position: { x: 400, y: 300 } });
    await page.getByRole("button", { name: "Compare" }).click();
    await expect(page.locator(".map-swipe-divider")).toBeVisible();
    await expect(page.getByRole("button", { name: "Before" })).toHaveCount(0); // labels are plain text, not buttons
    await expect(page.getByText("Before")).toBeVisible();
    await expect(page.getByText("After")).toBeVisible();
  });

  test("Jump to coordinates moves the map and rejects garbage input", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await map.click({ position: { x: 400, y: 300 } });
    const input = page.getByLabel("Go to coordinates (latitude, longitude)");
    const readout = page.locator(".map-toolbar-readout");
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
    await map.click({ position: { x: 400, y: 300 } });
    await page.locator(".map-toolbar-copy").click();
    await expect(page.getByText("Copied!")).toBeVisible();
  });

  test("Save image downloads a PNG of the current view", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await map.click({ position: { x: 400, y: 300 } });
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 15000 }),
      page.getByRole("button", { name: /Save image/ }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^map-\d{4}-\d{2}-\d{2}\.png$/);
  });

  test("Saved views: save the current view, jump to it, then remove it", async ({ page }) => {
    await gotoQaProject(page);
    const map = page.locator(".leaflet-container");
    await map.click({ position: { x: 400, y: 300 } });

    // window.prompt() has no Playwright-native handler (unlike dialog()) for
    // a plain synchronous prompt - stub it directly on the already-loaded
    // page instead of addInitScript (which only applies to FUTURE
    // navigations, not this already-open one).
    await page.evaluate(() => {
      window.prompt = () => "QA saved view";
    });
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
    const readout = page.locator(".map-toolbar-readout");
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
    // fully settle (two consecutive identical reads) before recalling.
    await expect(async () => {
      const a = await readout.textContent();
      await page.waitForTimeout(150);
      const b = await readout.textContent();
      expect(a).toEqual(b);
    }).toPass({ timeout: 5000 });
    await savedRow.click();
    await expect(readout).toHaveText(zoomBefore);

    await page.getByRole("button", { name: "Views" }).click();
    await page.getByRole("button", { name: "Remove QA saved view" }).click();
    await expect(page.getByText("No saved views yet")).toBeVisible();
  });

  test("only one toolbar dropdown is open at a time, each anchored under its own button", async ({ page }) => {
    await gotoQaProject(page);
    const menus = page.locator(".map-toolbar-menu");

    // Every open menu must sit directly under ITS OWN trigger button. The CSS
    // does this on its own (.map-toolbar-menu is absolute inside the button's
    // own position:relative .map-toolbar-dropdown wrapper), including after the
    // toolbar flex-wraps on a narrow window - this asserts it stays true rather
    // than assuming it.
    async function expectOnlyMenuUnder(buttonName) {
      await expect(menus).toHaveCount(1);
      const trigger = page.getByRole("button", { name: buttonName, exact: true });
      const btn = await trigger.boundingBox();
      const menu = await menus.first().boundingBox();
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

    await page.getByRole("button", { name: "Views" }).click();
    await expectOnlyMenuUnder("Views");
    await expect(page.getByRole("button", { name: "Polygon" })).toHaveCount(0);

    // The basemap picker is a native <select>, not a custom positioned menu, so
    // it can't overlap anything - but it must not leave a stale toolbar menu
    // behind it either.
    await page.getByLabel("Basemap").selectOption({ index: 1 });
    await expect(menus).toHaveCount(1); // Views still open; nothing new stacked on it

    // Clicking the open menu's own button closes it - back to zero menus.
    await page.getByRole("button", { name: "Views" }).click();
    await expect(menus).toHaveCount(0);
  });

  test("whole-panel collapse hides and reshows the docked Layers column", async ({ page }) => {
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
    const readout = page.locator(".map-toolbar-readout");
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
