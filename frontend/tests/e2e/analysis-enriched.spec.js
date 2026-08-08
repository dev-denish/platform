import { test, expect } from "@playwright/test";
import { gotoAnalysisView } from "./helpers.js";

/**
 * Wave: enriched index results (backend 369942f/5aaf55f, frontend c70cc77).
 *
 * Same "no meaningful mock for a real GEE dataset query" reasoning as
 * analysis.spec.js's own NDVI test - this hits the real backend/worker
 * against real Google Earth Engine (see docker-compose.test.yml's
 * DMRV_GEE_PROJECT_ID/credentials mount), computing a fresh EVI result and
 * asserting the NEW parts of the results panel (IndexSummaryCallout,
 * IndexDistribution, IndexHistogramChart, the reference-region placeholder -
 * see AnalysisPanel.jsx) render genuinely computed numbers, not stubs.
 *
 * EVI, not NDVI: this shared seeded QA project's NDVI analysis already has
 * its own dedicated fresh-state test in analysis.spec.js ("computing NDVI
 * polls the async job to completion..."), which asserts "Not computed yet"
 * as its starting condition. Reusing NDVI here would leave a cached result
 * behind (this file sorts before analysis.spec.js and workers:1/
 * fullyParallel:false in playwright.config.js means file order is exec
 * order), breaking that test's fresh-state assumption - exactly the class
 * of bug fixed once already for the Identify+NDVI test (commit 3ba1fdf).
 * EVI exercises the same _annual_index_series/index_summary pipeline as
 * NDVI (identical code path, only the band formula differs) without
 * touching NDVI's cached state, and as a bonus exercises the EVI
 * out-of-range range-masking fix (commit 88c0900) against live data.
 *
 * A separate file rather than another test in analysis.spec.js: that file
 * already carries the (long, deliberately real-GEE) trend-chart and Identify
 * tests: this wave's own assertions are a distinct concern (the enrichment
 * layer on top of an already-tested trend chart) and long enough on their
 * own to earn a file, not a fourth 100+-line test bolted onto an already
 * sizeable one.
 */

// Kept in sync with app/services/index_summary.py's DESCRIPTIVE_ONLY_TRAILER
// (a frontend test can't import that Python module directly) - every real
// summary this composer produces ends with this exact string, verbatim.
const DESCRIPTIVE_ONLY_TRAILER =
  "Descriptive only - not a forest-definition, eligibility or carbon determination.";

test.describe("Enriched vegetation-index results", () => {
  test("computing EVI renders a real composed summary, real distribution stats consistent with the trend chart, a real histogram, and a correctly-parked reference-region placeholder", async ({
    page,
  }) => {
    // Same timing budget as analysis.spec.js's NDVI test: async job-queue
    // path, measured 5-101s end-to-end for a fresh multi-year composite.
    test.setTimeout(180_000);
    await gotoAnalysisView(page);
    const results = page.locator(".analysis-results-body");

    await page.getByRole("button", { name: /^EVI/ }).click();
    // EVI may already have a cached result on this shared seeded QA project
    // if this spec has run before - .primary-button reads "Run analysis" OR
    // "Refresh" either way, and either one (re)computes a FRESH real result,
    // which is what every assertion below needs.
    await results.locator(".primary-button").click();
    const yearInput = page.locator(".analysis-year-select input[type=range]");
    await expect(yearInput).toBeVisible({ timeout: 120_000 });

    // ---------------------------------------------------------------------
    // 1. Summary callout (IndexSummaryCallout): real composed prose.
    // ---------------------------------------------------------------------
    const summary = page.locator(".analysis-callout-text");
    await expect(summary).toBeVisible();
    const summaryText = (await summary.textContent()).trim();
    // eslint-disable-next-line no-console
    console.log("EVI summary callout:", summaryText);

    // A stub or static placeholder string could never match this: only a
    // REAL computed boundary mean, interpolated by describe_level() in
    // app/services/index_summary.py, produces "averages <N.NN> across the
    // boundary".
    expect(summaryText).toMatch(/averages -?\d+\.\d{2} across the boundary/);
    // Proves the real clause composer (compose_summary()) ran end to end,
    // rather than a truncated/broken string - every real summary ends with
    // this fixed trailer, verbatim, regardless of which clauses fired.
    expect(
      summaryText.endsWith(DESCRIPTIVE_ONLY_TRAILER),
      `summary callout did not end with the fixed trailer - got: ${summaryText}`
    ).toBe(true);

    // ---------------------------------------------------------------------
    // 2. Distribution stat grid (IndexDistribution): 4 distinct, sane,
    //    real numbers for one specific year.
    // ---------------------------------------------------------------------
    const statCards = page.locator(".stat-grid .stat-card");
    await expect(statCards).toHaveCount(4);

    const statsByKind = {};
    let statYear = null;
    for (let i = 0; i < 4; i++) {
      const card = statCards.nth(i);
      const label = (await card.locator(".stat-label").textContent()).trim();
      const valueText = (await card.locator(".stat-value").textContent()).trim();
      const m = label.match(/^(Mean|Std dev|Min|Max) \((\d{4})\)$/);
      expect(m, `unexpected stat card label: "${label}"`).not.toBeNull();
      const [, kind, year] = m;
      statYear = year;
      statsByKind[kind] = Number(valueText);
    }
    // eslint-disable-next-line no-console
    console.log(`EVI distribution stats (${statYear}):`, statsByKind);

    const values = Object.values(statsByKind);
    // Real numbers, not "—" (formatNumber's null placeholder) or NaN text.
    expect(values.every((v) => Number.isFinite(v)), `parsed stat values: ${JSON.stringify(statsByKind)}`).toBe(
      true
    );
    // Not all zero (a degenerate/empty result would render 0.000 across
    // the board).
    expect(values.some((v) => Math.abs(v) > 1e-9)).toBe(true);
    // Not all identical (a copy-paste bug binding every card to the same
    // field would still pass the two checks above).
    expect(new Set(values.map((v) => v.toFixed(6))).size, `all four stat cards read the same value: ${values}`).toBeGreaterThan(1);
    // Real sanity invariants on genuine pixel statistics.
    expect(statsByKind["Std dev"]).toBeGreaterThanOrEqual(0);
    expect(statsByKind["Min"]).toBeLessThanOrEqual(statsByKind["Mean"]);
    expect(statsByKind["Mean"]).toBeLessThanOrEqual(statsByKind["Max"]);

    // ---------------------------------------------------------------------
    // 3. Histogram (IndexHistogramChart): real bars, not an empty chart.
    // ---------------------------------------------------------------------
    // _VEG_INDEX_HISTOGRAM_BINS in gee_analysis_service.py is a FIXED 20
    // bins for every year that produced real pixel data - a genuinely
    // computed year renders exactly that many bars, not zero and not a
    // placeholder chart.
    const bars = page.locator(".recharts-bar-rectangle");
    await expect(bars).toHaveCount(20);
    // Not bars.first(): the leftmost (lowest-value) bin(s) legitimately hold
    // zero pixels on real data whenever the boundary's min is well above -1 -
    // Recharts degenerates a zero-count bar's rounded-rect path to a
    // zero-size shape, which is correct rendering of a real empty bin, not a
    // broken chart. Checking the MAX across all 20 instead proves at least
    // the bin holding the bulk of the boundary's pixels rendered
    // real, non-degenerate geometry - a genuinely empty/broken chart would
    // leave every one of the 20 rectangles at zero height AND zero width.
    const barBoxes = [];
    for (let i = 0; i < 20; i++) barBoxes.push(await bars.nth(i).boundingBox());
    const maxHeight = Math.max(...barBoxes.map((b) => b?.height ?? 0));
    const maxWidth = Math.max(...barBoxes.map((b) => b?.width ?? 0));
    expect(maxHeight, `bar heights: ${JSON.stringify(barBoxes.map((b) => b?.height))}`).toBeGreaterThan(0);
    expect(maxWidth, `bar widths: ${JSON.stringify(barBoxes.map((b) => b?.width))}`).toBeGreaterThan(0);

    // ---------------------------------------------------------------------
    // 4. Reference-region comparison placeholder: present, but visually
    //    de-emphasized - correctly parked, not wired to fake data.
    // ---------------------------------------------------------------------
    const placeholderRow = page.locator(".analysis-placeholder-row");
    await expect(placeholderRow).toBeVisible();
    await expect(placeholderRow).toContainText("Reference-region comparison");
    const mutedValue = placeholderRow.locator(".stat-value-muted");
    await expect(mutedValue).toHaveText("Not yet available");
    // It uses the muted class, not the real-number .stat-value class every
    // card in the stat grid above uses - confirms it's visually distinct,
    // not just textually labeled "not available" while still looking live.
    await expect(placeholderRow.locator(".stat-value")).toHaveCount(0);

    // ---------------------------------------------------------------------
    // 5. Cross-check: the stat grid's mean must be the SAME number the
    //    pre-existing trend chart already shows for that year, not a
    //    separately (and possibly differently) computed one.
    // ---------------------------------------------------------------------
    // Structural, not coincidental: gee_analysis_service.py's
    // _annual_index_series() builds `series[year]` and
    // `distribution[year]["mean"]` from the exact same `mean` variable, in
    // the same per-year loop - so IndexTrendChart's default (latest) year
    // and IndexDistribution's default (latest) year are drawn from sibling
    // dicts with an identical key set, and must land on the same year.
    const trendCell = page.locator(".analysis-year-select .mono-cell");
    const trendText = (await trendCell.textContent()).trim();
    const trendMatch = trendText.match(/^(\d{4}):\s*(-?[\d.]+|no data)$/);
    expect(trendMatch, `trend chart caption text was: "${trendText}"`).not.toBeNull();
    const [, trendYear, trendValueRaw] = trendMatch;
    expect(trendYear, "trend chart and distribution grid disagree on which year is 'latest'").toBe(statYear);
    expect(trendValueRaw, "trend chart shows 'no data' for the very year the distribution grid has real stats for").not.toBe(
      "no data"
    );
    const trendValue = Number(trendValueRaw);
    // Rounding-tolerant, not exact-string, equality: the trend chart prints
    // toFixed(3), the stat grid prints Intl's variable-precision
    // formatNumber(...,3) - different presentation of the SAME underlying
    // 4dp-rounded backend value (see _round_or_none() in
    // gee_analysis_service.py). A real mismatch beyond rounding would mean
    // the enrichment pipeline computed a DIFFERENT number than the
    // pre-existing series, which is exactly the bug class this guards
    // against.
    expect(
      Math.abs(trendValue - statsByKind["Mean"]),
      `trend chart mean ${trendValue} vs distribution grid mean ${statsByKind["Mean"]} for ${statYear}`
    ).toBeLessThan(0.002);

    if (process.env.ANALYSIS_SHOT) await page.screenshot({ path: process.env.ANALYSIS_SHOT });
  });
});
