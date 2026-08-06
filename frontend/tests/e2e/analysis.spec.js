import { test, expect } from "@playwright/test";
import { login, ADMIN, QA_PROJECT_NAME } from "./helpers.js";

/**
 * Maps tab -> Analysis view (Wave: GEE analysis registry).
 *
 * The "Run analysis" test hits REAL Google Earth Engine from the ephemeral
 * stack (see docker-compose.test.yml's DMRV_GEE_PROJECT_ID/credentials mount) -
 * there is no meaningful mock for a real dataset query, and a fake one would
 * only prove the mock works. Hence the long per-test timeout: Hansen takes
 * ~7s server-side, plus tile loading.
 */
async function gotoAnalysisView(page, creds = ADMIN) {
  // Wide enough for the three-across layout (see .analysis-layout's 1440px
  // breakpoint) - the default 1280 test viewport stacks it into one column.
  await page.setViewportSize({ width: 1800, height: 1000 });
  await login(page, creds);
  await page.goto("/projects");
  // Filtered, not "click the link on page 1": the backend's own DB-backed
  // integration tests can be pointed at this same ephemeral stack and leave
  // hundreds of their own projects behind, which pushes this one off the
  // first (client-side-limited) page of the list. Observed, not theoretical.
  await page.locator(".projects-search").fill(QA_PROJECT_NAME);
  await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
  await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  await page.getByRole("group", { name: "Maps view" }).getByRole("button", { name: "Analysis" }).click();
  await expect(page.locator(".analysis-layout")).toBeVisible();
}

test.describe("Analysis view", () => {
  test("lists the whole registry, in-development entries included but de-emphasized", async ({ page }) => {
    await gotoAnalysisView(page);
    // 16 catalog entries, grouped by category.
    await expect(page.locator(".analysis-row")).toHaveCount(16);
    await expect(page.locator(".analysis-row-muted")).toHaveCount(11);
    await expect(page.getByRole("button", { name: /^Forest Change$/ })).toBeVisible();

    // An in-development entry: honest empty state, no numbers, no run button.
    await page.getByRole("button", { name: /^NDVI/ }).click();
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
});

// Not covered here: the refresh button's role gating (a plain canUpload check
// in AnalysisPanel.jsx). The seeded QA project only has qa_gis as a member,
// and a non-member Viewer can't open the project at all (project-level RBAC),
// so asserting it would mean seeding a Viewer membership for a one-line check.
