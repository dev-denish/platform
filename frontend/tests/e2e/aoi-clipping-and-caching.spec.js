import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";
import { test, expect } from "@playwright/test";
import { login, gotoAnalysisView, readTokens, ADMIN, API_BASE } from "./helpers.js";

/**
 * Wave: AOI clip / raw-imagery browsing / partial coverage.
 *
 * Hits REAL Google Earth Engine from the ephemeral dmrv-qa stack, same
 * reasoning as analysis.spec.js - there is no meaningful mock for a real
 * dataset query, and this suite exists specifically to prove the real
 * production code path (tile clipping, year-param plumbing, coverage %)
 * against real GEE responses, not a stand-in for them.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = resolve(__dirname, "fixtures");

test.describe("AOI clip and raw-imagery browsing", () => {
  test("running an analysis fits the map to the AOI, not the prior viewport", async ({ page }) => {
    test.setTimeout(180_000);
    await gotoAnalysisView(page);

    const zoomReadout = page.locator(".map-toolbar-copy");
    const zoomBefore = await zoomReadout.textContent();

    await page.getByRole("button", { name: /Global Forest Change/ }).click();
    await page.getByRole("button", { name: "Run analysis" }).click();
    await expect(page.locator(".analysis-callout")).toBeVisible({ timeout: 120_000 });
    // Wave: AOI clip's fit-to-AOI-bounds effect (ProjectMap.jsx) fires on
    // overlayTileUrl going non-null - give Leaflet a moment to settle the
    // resulting fitBounds animation before reading the readout again.
    await page.waitForTimeout(500);
    const zoomAfter = await zoomReadout.textContent();

    expect(zoomAfter).not.toBe(zoomBefore);
  });

  test("s2_browse with an explicit year renders a real clipped tile and a coverage line", async ({ page }) => {
    test.setTimeout(120_000);
    await gotoAnalysisView(page);

    await page.getByRole("button", { name: /^Sentinel-2 True Color/ }).click();
    const yearSelect = page.locator(".analysis-year-select select");
    await expect(yearSelect).toBeVisible();
    await yearSelect.selectOption("2023");

    await page.getByRole("button", { name: "Run analysis" }).click();
    await expect(page.locator('.leaflet-container img[src*="earthengine"]').first()).toBeVisible({
      timeout: 90_000,
    });
    // Wave: partial coverage - the banner is ALWAYS rendered once
    // coverage_pct is present, not just when it's below the warning threshold.
    await expect(page.locator(".coverage-banner")).toBeVisible();
    await expect(page.locator(".coverage-banner")).toContainText(/% of the project boundary/);
  });

  test("an AOI a single scene can't fully cover shows the partial-coverage warning banner", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    // Ad-hoc project + boundary (not the shared QA project) - a real ~165km-
    // wide AOI, verified live against real GEE to span more than one
    // Sentinel-2 tile, so a single-scene browse layer genuinely cannot cover
    // it (confirmed ~1.6% coverage against real data before writing this
    // test - not a guess). This is what Part 3 exists for: cloud gaps/scene
    // edges/revisit timing genuinely under-cover an AOI sometimes, and that
    // must render honestly, not silently as if fully covered.
    const tokens = readTokens(ADMIN.username);
    const projectName = `QA Multi-tile AOI ${Date.now()}`;
    const boundaryBytes = readFileSync(resolve(FIXTURES_DIR, "qa-boundary-multitile.geojson"));

    // Playwright's APIRequestContext multipart shape: file fields are
    // {name, mimeType, buffer}, not a browser FormData/Blob (this `request`
    // fixture runs in Node, not the page) - global-setup.js's own
    // uploadAndWait uses browser FormData/Blob because IT runs via plain
    // `fetch` inside the Playwright-launched Node process pre-browser, a
    // different context from this in-test `request` fixture.
    const uploadRes = await request.post(`${API_BASE}/datasets/upload`, {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
      multipart: {
        file: {
          name: "qa-boundary-multitile.geojson",
          mimeType: "application/geo+json",
          buffer: boundaryBytes,
        },
        project_name: projectName,
        region: "Karnataka",
        pixel_size_m: "10",
        dataset_type: "Boundary",
        source: "E2E partial-coverage fixture",
        date_processed: "2024-06-01",
        // Wave: upload project-name footgun fix (merged after this test was
        // originally written) - POST /datasets/upload now rejects a
        // project_name that doesn't already exist unless the caller
        // explicitly confirms it's new. `projectName` above is always a
        // fresh, timestamped, never-before-seen name, so this must be true.
        create_new_project: "true",
      },
    });
    expect(uploadRes.ok()).toBe(true);
    const { job_id } = await uploadRes.json();

    const deadline = Date.now() + 60_000;
    let jobStatus = null;
    while (Date.now() < deadline) {
      const jobRes = await request.get(`${API_BASE}/jobs/${job_id}`, {
        headers: { Authorization: `Bearer ${tokens.access_token}` },
      });
      const job = await jobRes.json();
      jobStatus = job.status;
      if (jobStatus === "succeeded") break;
      if (["failed", "dead_letter"].includes(jobStatus)) {
        throw new Error(`boundary ingest ended in ${jobStatus}: ${JSON.stringify(job.error)}`);
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    expect(jobStatus).toBe("succeeded");

    await page.setViewportSize({ width: 1800, height: 1000 });
    await login(page, ADMIN);
    await page.goto("/projects");
    await page.locator(".projects-search").fill(projectName);
    await page.getByRole("link", { name: projectName }).click();
    await page.getByRole("group", { name: "Maps view" }).getByRole("button", { name: "Analysis" }).click();
    await expect(page.locator(".analysis-layout")).toBeVisible();

    await page.getByRole("button", { name: /^Sentinel-2 True Color/ }).click();
    await page.locator(".analysis-year-select select").selectOption("2023");
    await page.getByRole("button", { name: "Run analysis" }).click();
    await expect(page.locator('.leaflet-container img[src*="earthengine"]').first()).toBeVisible({
      timeout: 90_000,
    });

    await expect(page.locator(".coverage-banner-warning")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".coverage-banner-warning")).toContainText("Partial coverage");
    if (process.env.ANALYSIS_SHOT) {
      await page.screenshot({ path: process.env.ANALYSIS_SHOT.replace(/\.png$/, "-coverage-banner.png") });
    }
  });
});
