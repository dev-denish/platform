import { test, expect } from "@playwright/test";
import { gotoAnalysisView } from "./helpers.js";

/**
 * Wave: analysis config and methodology.
 *
 * Real Google Earth Engine, same "no meaningful mock for a real dataset
 * query" reasoning as analysis.spec.js's own NDVI test - a fake compute
 * would only prove the mock works, not that the new year/range/season/
 * source/masking config actually reaches the real compute path.
 *
 * File-ordering note (playwright.config.js: fullyParallel:false, workers:1,
 * so spec FILES run in a fixed order - alphabetical): "analysis-config"
 * sorts BEFORE both "analysis-enriched" and "analysis" (plain), so this
 * file runs first. analysis.spec.js's own NDVI test asserts "Not computed
 * yet" as its starting condition (a real, load-bearing assumption - see
 * analysis-enriched.spec.js's own docstring on why IT avoids NDVI for
 * exactly this reason). This file must not leave a cached NDVI result
 * behind either: the rejected-combo test below never sends a request at
 * all (that's the whole point), and the season-window test uses SAVI - not
 * used by name anywhere else in this suite - so nothing here disturbs
 * either downstream file's assumptions. io_lulc IS reused across files, but
 * safely: nothing here or in analysis.spec.js asserts io_lulc starts
 * uncomputed, and different configs are different stored variants
 * (params_key), not overwrites of each other - the exact non-destructive
 * property this wave built.
 */

test.describe("Analysis config panel", () => {
  test("io_lulc: single year is the default; range mode computes every requested year", async ({ page }) => {
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    const results = page.locator(".analysis-results-body");
    const configPanel = page.locator(".analysis-config-panel");

    await page.getByRole("button", { name: /Annual Land Cover/ }).click();
    await expect(configPanel).toBeVisible();
    await expect(configPanel.getByRole("combobox", { name: "Years" })).toHaveValue("single");
    const liveMaxYear = Number(
      await configPanel.getByRole("combobox", { name: "Year", exact: true }).inputValue()
    );

    await configPanel.getByRole("combobox", { name: "Years" }).selectOption("range");
    const fromSelect = configPanel.getByRole("combobox", { name: "From" });
    const toSelect = configPanel.getByRole("combobox", { name: "To" });
    await fromSelect.selectOption(String(liveMaxYear - 2));
    await toSelect.selectOption(String(liveMaxYear));

    await results.locator(".primary-button").click();
    const breakdownYearSelect = results.locator(".analysis-year-select select").first();
    await expect(breakdownYearSelect).toBeVisible({ timeout: 120_000 });
    // 3 requested years (liveMax-2..liveMax inclusive) - not the single year
    // the default would have produced, not the full 2017-2023 domain either.
    // Generous timeout: a successful run resets configParams back to its
    // defaults (selected's object identity changes via the computed_at
    // update, re-triggering the same reset effect selecting an analysis
    // does), which re-fetches the result WITHOUT config params (most
    // recent variant) - a real extra round trip after every run, not an
    // instant re-render, so the final 3-year state takes a moment to settle.
    await expect(breakdownYearSelect.locator("option")).toHaveCount(3, { timeout: 30_000 });

    // The methodology panel reflects the SAME 3 years, in words, with no
    // internal implementation detail leaking into it.
    await page.getByRole("button", { name: "Methodology" }).click();
    const methodology = results.locator("table.data-table");
    await expect(methodology).toContainText(String(liveMaxYear - 2));
    await expect(methodology).toContainText(String(liveMaxYear));
    await expect(methodology).toContainText("10m Annual Land Cover");
    const methodologyText = (await methodology.textContent()) ?? "";
    for (const forbidden of [".py", "gee_analysis_service", "_esri_lulc", "_compute"]) {
      expect(methodologyText).not.toContain(forbidden);
    }
  });

  test("a custom season window for SAVI is reflected in the note and the methodology panel", async ({ page }) => {
    // SAVI, not NDVI/EVI: NDVI's fresh "Not computed yet" state is a real
    // assumption analysis.spec.js's own test relies on (this file runs
    // first - see module docstring); EVI is analysis-enriched.spec.js's.
    // SAVI exercises the identical _annual_index_series/season-window code
    // path without touching either.
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    const results = page.locator(".analysis-results-body");
    const configPanel = page.locator(".analysis-config-panel");

    await page.getByRole("button", { name: /^SAVI/ }).click();
    await expect(configPanel).toBeVisible();
    await configPanel.getByRole("textbox", { name: "Season start" }).fill("03-01");
    await configPanel.getByRole("textbox", { name: "Season end" }).fill("06-30");

    await results.locator(".primary-button").click();
    await expect(results.locator(".analysis-note").filter({ hasText: "03-01 to 06-30" })).toBeVisible({
      timeout: 120_000,
    });

    await page.getByRole("button", { name: "Methodology" }).click();
    const methodology = results.locator("table.data-table");
    await expect(methodology).toContainText("03-01 to 06-30");
  });

  test("an unsupported imagery source/cloud masking combination is rejected with no request ever sent", async ({
    page,
  }) => {
    // NDVI is safe here specifically BECAUSE this test never successfully
    // computes anything - the whole assertion is that no request fires at
    // all, so it can't leave a cached result behind to break
    // analysis.spec.js's later "Not computed yet" assumption.
    await gotoAnalysisView(page);
    const configPanel = page.locator(".analysis-config-panel");

    const refreshRequests = [];
    page.on("request", (req) => {
      if (req.url().includes("/refresh")) refreshRequests.push(req.url());
    });

    await page.getByRole("button", { name: /^NDVI/ }).click();
    await expect(configPanel).toBeVisible();
    await configPanel.getByRole("combobox", { name: "Imagery source" }).selectOption("landsat8");
    await configPanel.getByRole("combobox", { name: "Cloud masking" }).selectOption("cloud_score_plus");

    await expect(
      page.locator(".field-hint-error").filter({ hasText: "isn't supported yet" })
    ).toContainText("Sentinel-2 + Cloud Score+ is implemented.");
    const runButton = page.locator(".analysis-results-body .primary-button");
    // A real `disabled` attribute on a native <button> - not just a visual
    // treatment - so no click handler could fire even if something tried.
    await expect(runButton).toBeDisabled();
    await page.waitForTimeout(500);
    expect(refreshRequests).toHaveLength(0);

    // Switching to the one supported combination clears the rejection and
    // re-enables Run - proves this is real, live client-side validation,
    // not a one-way dead end.
    await configPanel.getByRole("combobox", { name: "Imagery source" }).selectOption("sentinel2");
    await expect(page.locator(".field-hint-error")).toHaveCount(0);
    await expect(runButton).toBeEnabled();
  });
});
