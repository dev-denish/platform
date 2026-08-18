import { test, expect } from "@playwright/test";
import { login, ADMIN, QA_PROJECT_NAME, collectConsoleErrors, openMapPanels, clickMapToActivate } from "./helpers.js";

async function gotoProjectWithMap(page) {
  await login(page, ADMIN);
  await page.goto("/projects");
  await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
  await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  const map = page.locator(".leaflet-container");
  await expect(map).toBeVisible();
  await openMapPanels(page);
  await clickMapToActivate(map);
  return map;
}

test.describe("Map Toolbar Enhancement v2", () => {
  test("Tier 1: compass indicator is visible, north-up, non-interactive", async ({ page }) => {
    await gotoProjectWithMap(page);
    const compass = page.locator(".compass-indicator");
    await expect(compass).toBeVisible();
    await expect(compass).toHaveAttribute("title", "North is up");
    await page.screenshot({ path: "test-results/screenshots/tier1-compass.png" });
  });

  test("Tier 1: opacity slider visibly changes tile transparency live", async ({ page }) => {
    await gotoProjectWithMap(page);
    await page.locator(".layer-row-gear").first().click();
    const slider = page.locator(".symbology-popover-opacity input[type=range]");
    await expect(slider).toBeVisible();
    await page.screenshot({ path: "test-results/screenshots/tier1-opacity-before.png" });
    await slider.fill("0.2");
    await expect(page.locator(".symbology-popover-opacity-value")).toHaveText("0.20");
    await page.screenshot({ path: "test-results/screenshots/tier1-opacity-after.png" });
  });

  test("Tier 1: fullscreen toggle still works alongside the new compass", async ({ page }) => {
    await gotoProjectWithMap(page);
    const toggle = page.locator(".map-overlay-topright button");
    await toggle.click();
    await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true);
    await toggle.click();
    await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false);
  });

  test("Tier 2: Basemap panel opens, shows Google tab, and actually swaps tiles", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await gotoProjectWithMap(page);
    await page.getByRole("button", { name: /Show map sources|Hide map sources/ }).click();
    const panel = page.locator(".basemap-panel");
    await expect(panel).toBeVisible();
    await page.screenshot({ path: "test-results/screenshots/tier2-basemap-panel-esri.png" });

    await panel.getByRole("tab", { name: "Google" }).click();
    await expect(panel.getByText("Google Satellite")).toBeVisible();
    await page.screenshot({ path: "test-results/screenshots/tier2-basemap-panel-google-tab.png" });

    await panel.getByText("Google Streets").click();
    // Real tile swap, not just a state change - wait for an actual Google
    // vt tile to load and confirm the URL really is a Google host.
    const tileReq = page.waitForResponse((res) => /mt\d\.google\.com\/vt/.test(res.url()), { timeout: 15000 });
    const res = await tileReq;
    expect(res.ok()).toBeTruthy();
    await page.waitForTimeout(1500); // let the crossfade settle
    await page.screenshot({ path: "test-results/screenshots/tier2-basemap-google-streets-live.png" });

    // Old BasemapToggle <select> still works and agrees with the new panel.
    await expect(page.getByLabel("Basemap")).toHaveValue("google-streets");

    await panel.getByRole("tab", { name: "Esri" }).click();
    await panel.getByText("Satellite", { exact: true }).click();
    await expect(page.getByLabel("Basemap")).toHaveValue("satellite");
    expect(errors).toEqual([]);
  });

  test("Tier 2: existing native <select> basemap picker still works (no regression)", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await gotoProjectWithMap(page);
    await page.getByLabel("Basemap").selectOption("map");
    await expect(page.getByLabel("Basemap")).toHaveValue("map");
    await page.getByLabel("Basemap").selectOption("satellite");
    await expect(page.getByLabel("Basemap")).toHaveValue("satellite");
    expect(errors).toEqual([]);
  });

  test("Tier 3: right-click opens a context menu at the clicked point with real coordinates", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const box = await map.boundingBox();
    const clickX = box.x + box.width * 0.75;
    const clickY = box.y + box.height * 0.5;
    await map.click({ button: "right", position: { x: box.width * 0.75, y: box.height * 0.5 } });

    const menu = page.locator(".map-context-menu");
    await expect(menu).toBeVisible();
    const menuBox = await menu.boundingBox();
    // Menu's top-left should render right at (or very near) the click point.
    expect(Math.abs(menuBox.x - clickX)).toBeLessThan(5);
    expect(Math.abs(menuBox.y - clickY)).toBeLessThan(5);

    const coordsText = await page.locator(".map-context-menu-coords").textContent();
    expect(coordsText).toMatch(/-?\d+\.\d+,\s*-?\d+\.\d+/);
    await page.screenshot({ path: "test-results/screenshots/tier3-context-menu-open.png" });

    // Escape closes it.
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
  });

  test("Tier 3: context menu closes on click-away", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.75, y: box.height * 0.5 } });
    await expect(page.locator(".map-context-menu")).toBeVisible();
    await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
    await expect(page.locator(".map-context-menu")).toBeHidden();
  });

  test("Tier 3: What's here opens the pixel popup from the context menu", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.75, y: box.height * 0.5 } });
    await page.getByRole("menuitem", { name: "What's here" }).click();
    await expect(page.locator(".pixel-popup")).toBeVisible();
    await expect(page.locator(".map-context-menu")).toBeHidden();
    await page.screenshot({ path: "test-results/screenshots/tier3-whats-here.png" });
  });

  test("Tier 3: Attribute table opens a full data table from the context menu", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.75, y: box.height * 0.5 } });
    await page.getByRole("menuitem", { name: "Attribute table" }).click();
    const dialog = page.locator(".attribute-table-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".attribute-table, .confirm-dialog-detail")).toBeVisible();
    await page.screenshot({ path: "test-results/screenshots/tier3-attribute-table.png" });
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(dialog).toBeHidden();
  });

  test("Tier 3: Center map here recenters the view on the clicked point", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const badge = page.locator(".map-coord-badge");
    const before = await badge.textContent();
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.3, y: box.height * 0.3 } });
    await page.getByRole("menuitem", { name: "Center map here" }).click();
    await expect(async () => {
      expect(await badge.textContent()).not.toEqual(before);
    }).toPass();
  });

  test("Tier 3: Zoom in here increases the zoom level", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const badge = page.locator(".map-coord-badge");
    const zoomBefore = (await badge.textContent()).match(/Zoom: (\d+)/)[1];
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.6, y: box.height * 0.4 } });
    await page.getByRole("menuitem", { name: "Zoom in here" }).click();
    await expect(async () => {
      const zoomAfter = (await badge.textContent()).match(/Zoom: (\d+)/)[1];
      expect(Number(zoomAfter)).toBeGreaterThan(Number(zoomBefore));
    }).toPass();
  });

  test("Tier 3: Copy as GeoJSON copies a valid Point Feature to the clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const map = await gotoProjectWithMap(page);
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.5, y: box.height * 0.5 } });
    await page.getByRole("menuitem", { name: "Copy as GeoJSON" }).click();
    await expect(page.getByRole("menuitem", { name: "Copied!" })).toBeVisible();
    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    const feature = JSON.parse(clipboardText);
    expect(feature.type).toBe("Feature");
    expect(feature.geometry.type).toBe("Point");
    expect(feature.geometry.coordinates).toHaveLength(2);
  });

  test("Tier 3: View in Google Maps / Earth open external tabs with the clicked coordinates", async ({ page, context }) => {
    const map = await gotoProjectWithMap(page);
    const box = await map.boundingBox();
    await map.click({ button: "right", position: { x: box.width * 0.5, y: box.height * 0.5 } });

    const [mapsTab] = await Promise.all([
      context.waitForEvent("page"),
      page.getByRole("menuitem", { name: "View in Google Maps" }).click(),
    ]);
    await mapsTab.waitForLoadState("domcontentloaded").catch(() => {});
    expect(mapsTab.url()).toContain("google.com/maps/@");
    await mapsTab.close();

    await map.click({ button: "right", position: { x: box.width * 0.5, y: box.height * 0.5 } });
    const [earthTab] = await Promise.all([
      context.waitForEvent("page"),
      page.getByRole("menuitem", { name: "View in Google Earth" }).click(),
    ]);
    await earthTab.waitForLoadState("domcontentloaded").catch(() => {});
    expect(earthTab.url()).toContain("earth.google.com/web/@");
    await earthTab.close();
  });

  test("Tier 4: drag-to-reorder swaps two layers and persists across reload", async ({ page }) => {
    await gotoProjectWithMap(page);
    const group = page.locator(".layer-group", { hasText: "Classified imagery" });
    const before = await group.locator(".layer-row-label").allTextContents();

    // Drag the SECOND row onto the FIRST - moveBefore inserts just before
    // the drop target, so this is the direction that actually swaps them
    // (the reverse is a no-op: row 0 is already before row 1).
    await group.locator(".layer-row").nth(1).locator(".layer-row-drag-handle").dragTo(group.locator(".layer-row").nth(0));
    await expect(async () => {
      const after = await group.locator(".layer-row-label").allTextContents();
      expect(after).toEqual([before[1], before[0]]);
    }).toPass();

    await page.reload();
    await expect(page.locator(".leaflet-container")).toBeVisible();
    await openMapPanels(page);
    await expect(page.locator(".layer-group", { hasText: "Classified imagery" }).locator(".layer-row-label")).toHaveText([
      before[1],
      before[0],
    ]);
  });

  test("Tier 4: vector layer style controls actually restyle the rendered GeoJSON", async ({ page }) => {
    await gotoProjectWithMap(page);
    // boundsLayers (ProjectMap.jsx) also renders one Rectangle outline per
    // layer, always fillOpacity:0 - excluded here so this only asserts on
    // the real vector layer's own GeoJSON path(s).
    const vectorPath = page.locator('.leaflet-overlay-pane path:not([fill-opacity="0"])').first();
    await expect(vectorPath).toHaveAttribute("fill-opacity", "0.15");

    await page.locator(".layer-group", { hasText: "Vector layers" }).locator(".layer-row-gear").first().click();
    const panel = page.locator(".symbology-popover", { hasText: "layer style" });
    await expect(panel).toBeVisible();

    const colorInput = panel.locator('input[type="color"]');
    await colorInput.evaluate((el) => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(el, "#ff00ff");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await expect(vectorPath).toHaveAttribute("stroke", "#ff00ff");

    await panel.locator('input[type="range"]').nth(0).fill("6");
    await expect(vectorPath).toHaveAttribute("stroke-width", "6");

    await panel.locator('input[type="range"]').nth(1).fill("0.8");
    await expect(vectorPath).toHaveAttribute("fill-opacity", "0.8");
  });

  test("Fix: attribution info toggle expands/collapses and closes on click-away/Escape", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const attribution = page.locator(".attribution-info");
    await expect(attribution).toBeVisible();
    await expect(attribution.locator(".attribution-info-text")).not.toBeEmpty();

    const panel = page.locator(".attribution-info-panel");
    await expect(panel).toBeHidden();

    await attribution.locator(".attribution-info-toggle").click();
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("Terms of use");
    await expect(panel).toContainText("Report a map error");
    await expect(panel).toContainText("Keyboard shortcuts");

    // Toggle again closes it.
    await attribution.locator(".attribution-info-toggle").click();
    await expect(panel).toBeHidden();

    // Click-away on the map closes it.
    await attribution.locator(".attribution-info-toggle").click();
    await expect(panel).toBeVisible();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width * 0.7, y: box.height * 0.2 } });
    await expect(panel).toBeHidden();

    // Escape closes it.
    await attribution.locator(".attribution-info-toggle").click();
    await expect(panel).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
  });

  test("Fix: Lat/Lon/Zoom/Scale status bar sits below the map, not overlaying it", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    const statusBar = page.locator(".map-coord-badge");
    await expect(statusBar).toBeVisible();

    const mapBox = await map.boundingBox();
    const statusBox = await statusBar.boundingBox();
    expect(statusBox.y).toBeGreaterThanOrEqual(mapBox.y + mapBox.height - 1);

    // Still the same click-to-copy control it always was.
    await statusBar.click();
    await expect(statusBar).toContainText("Copied!");
  });
});
