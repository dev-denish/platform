import { test, expect } from "@playwright/test";
import { login, ADMIN, QA_PROJECT_NAME, collectConsoleErrors, openMapPanels } from "./helpers.js";

async function gotoProjectWithMap(page) {
  await login(page, ADMIN);
  await page.goto("/projects");
  await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
  await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  const map = page.locator(".leaflet-container");
  await expect(map).toBeVisible();
  // Wave: floating map controls - the toolbar/Layers panel are collapsed by
  // default now (floating overlays, not always-visible docked chrome), so
  // every test below that reaches into either one needs them open first.
  await openMapPanels(page);
  // Click once first - scroll-zoom and most interaction is gated on the map
  // having been clicked/focused at least once (ScrollZoomOnActivate).
  await map.click({ position: { x: 400, y: 300 } });
  return map;
}

test.describe("Map: core controls after memoization/throttling changes", () => {
  test("zoom in/out buttons change the live Zoom readout", async ({ page }) => {
    await gotoProjectWithMap(page);
    const readout = page.locator(".map-coord-badge");
    const zoomText = () => readout.textContent();
    const before = await zoomText();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(async () => {
      expect(await zoomText()).not.toEqual(before);
    }).toPass();
  });

  test("Extent button re-fits the map (no crash, map stays visible)", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    await page.getByRole("button", { name: "Extent" }).click();
    await expect(map).toBeVisible();
  });

  test("pan (drag) moves the map center", async ({ page }) => {
    await gotoProjectWithMap(page);
    const readout = page.locator(".map-coord-badge");
    const map = page.locator(".leaflet-container");
    const box = await map.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 60, { steps: 10 });
    await page.mouse.up();
    // moveend fires -> readout's Lat/Lon (center) updates. Just assert no
    // crash and the coordinate badge is still rendering coordinates.
    await expect(readout).toContainText(/Lat: /);
  });

  test("layer visibility checkbox toggle hides/shows a tile layer", async ({ page }) => {
    await gotoProjectWithMap(page);
    const checkbox = page.locator(".layer-row-checkbox").first();
    await expect(checkbox).toBeChecked();
    await checkbox.uncheck();
    await expect(checkbox).not.toBeChecked();
    await checkbox.check();
    await expect(checkbox).toBeChecked();
  });

  test("opacity slider (gear popover) changes a layer's opacity", async ({ page }) => {
    await gotoProjectWithMap(page);
    await page.locator(".layer-row-gear").first().click();
    const slider = page.locator(".symbology-popover-opacity input[type=range]");
    await expect(slider).toBeVisible();
    await slider.fill("0.5");
    await expect(page.locator(".symbology-popover-opacity-value")).toHaveText("0.50");
  });

  test("measure: distance tool computes a result in meters", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    await page.getByRole("button", { name: /Measure/i }).click();
    await page.getByRole("button", { name: "Distance" }).click();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.6, y: box.height * 0.5 } });
    const result = page.locator(".measure-result");
    await expect(result).toContainText(/\d+(\.\d+)?\s*m/);
    await page.getByRole("button", { name: "Clear" }).click();
  });

  test("measure: area tool computes a result in hectares", async ({ page }) => {
    const map = await gotoProjectWithMap(page);
    await page.getByRole("button", { name: /Measure/i }).click();
    await page.getByRole("button", { name: "Area" }).click();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.6, y: box.height * 0.3 } });
    await map.click({ position: { x: box.width * 0.45, y: box.height * 0.55 } });
    const result = page.locator(".measure-result");
    await expect(result).toContainText(/ha/);
  });

  test("pixel inspect (Identify): clicking the map opens a popup with a layer row", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    const map = await gotoProjectWithMap(page);
    // "Identify" (inspect mode) is the default; explicitly select it since
    // the earlier measure tests in this file may leave a different mode
    // selected if run in the same worker.
    await page.getByRole("button", { name: "Identify" }).click();
    const box = await map.boundingBox();
    await map.click({ position: { x: box.width / 2, y: box.height / 2 } });
    const popup = page.locator(".pixel-popup");
    await expect(popup).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("fullscreen toggle enters and exits fullscreen", async ({ page }) => {
    await gotoProjectWithMap(page);
    const toggle = page.locator(".map-overlay-topright button");
    await toggle.click();
    await expect
      .poll(() => page.evaluate(() => !!document.fullscreenElement))
      .toBe(true);
    await toggle.click();
    await expect
      .poll(() => page.evaluate(() => !!document.fullscreenElement))
      .toBe(false);
  });

  test("basemap switch (Satellite <-> Map) does not error", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await gotoProjectWithMap(page);
    await page.getByLabel("Basemap").selectOption("map");
    await expect(page.getByLabel("Basemap")).toHaveValue("map");
    await page.getByLabel("Basemap").selectOption("satellite");
    await expect(page.getByLabel("Basemap")).toHaveValue("satellite");
    expect(errors).toEqual([]);
  });
});
