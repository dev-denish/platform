import { readFileSync } from "fs";
import { test, expect } from "@playwright/test";
import { login, ADMIN, API_BASE, QA_PROJECT_NAME } from "./helpers.js";

test.describe("PDF report generation", () => {
  test("select a computed analysis, generate, download, and get a valid PDF", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    await expect(page).toHaveURL(/\/projects\/[\w-]+/);
    const projectId = page.url().match(/\/projects\/([\w-]+)/)[1];
    const token = await page.evaluate(() => sessionStorage.getItem("dmrv.access_token"));

    // Guarantee at least one real computed analysis exists for the
    // checklist, regardless of what other specs in this run have or haven't
    // already computed on this shared project - hansen_gfc is "sync"
    // execution (a few seconds), the fastest way to get real data. Safe to
    // call even if another spec already computed it (idempotent refresh).
    const refreshRes = await page.request.post(
      `${API_BASE}/projects/${projectId}/analyses/hansen_gfc/refresh`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    expect(refreshRes.ok()).toBe(true);

    await page.getByRole("button", { name: "Dashboard" }).click();
    const panel = page.locator("section.panel", { hasText: "PDF report" });
    const hansenRow = panel.locator("label", { hasText: "Global Forest Change (Hansen)" });
    await expect(hansenRow).toBeVisible({ timeout: 15_000 });
    await hansenRow.locator('input[type="checkbox"]').check();

    await panel.getByRole("button", { name: /generate report/i }).click();

    // Real GEE map-tile fetch + PDF assembly - measured ~1 minute for a
    // 2-analysis report including a vegetation index (see report_service.py's
    // own docstring); a single sync-execution analysis is faster, but this
    // stays generous rather than assuming.
    const downloadButton = panel.getByRole("button", { name: /download report/i });
    await expect(downloadButton).toBeVisible({ timeout: 90_000 });

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      downloadButton.click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.pdf$/);

    const path = await download.path();
    const bytes = readFileSync(path);
    expect(bytes.subarray(0, 4).toString("latin1")).toBe("%PDF");
    // A real report with a map image + methodology text is comfortably
    // larger than a trivial/empty PDF stub.
    expect(bytes.length).toBeGreaterThan(5_000);
  });

  test("a project with no computed analyses shows an honest empty state, never a fake option", async ({ page }) => {
    // Uses a throwaway project with a boundary but zero computed analyses -
    // the checklist must never offer an analysis with no real data (same
    // rule the catalog itself already follows for in-development entries).
    await login(page, ADMIN);
    const token = await page.evaluate(() => sessionStorage.getItem("dmrv.access_token"));
    const boundary = {
      type: "FeatureCollection",
      features: [{
        type: "Feature", properties: {},
        geometry: { type: "Polygon", coordinates: [[[76.29, 13.02], [76.30, 13.02], [76.30, 13.03], [76.29, 13.03], [76.29, 13.02]]] },
      }],
    };
    const form = new FormData();
    form.set("file", new Blob([JSON.stringify(boundary)], { type: "application/geo+json" }), "boundary.geojson");
    const projectName = `Report Empty State ${Date.now()}`;
    for (const [k, v] of Object.entries({
      project_name: projectName, region: "Karnataka", dataset_type: "Boundary",
      source: "report empty-state test", date_processed: "2026-01-01", pixel_size_m: "10",
      create_new_project: "true",
    })) {
      form.set(k, v);
    }
    const uploadRes = await fetch(`${API_BASE}/datasets/upload`, {
      method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form,
    });
    expect(uploadRes.ok).toBe(true);
    const { job_id: jobId } = await uploadRes.json();
    const deadline = Date.now() + 30_000;
    let projectId;
    while (Date.now() < deadline) {
      const jobRes = await fetch(`${API_BASE}/jobs/${jobId}`, { headers: { Authorization: `Bearer ${token}` } });
      const job = await jobRes.json();
      if (job.status === "succeeded") {
        projectId = job.result.project_id;
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    expect(projectId).toBeTruthy();

    await page.goto(`/projects/${projectId}`);
    await page.getByRole("button", { name: "Dashboard" }).click();
    const panel = page.locator("section.panel", { hasText: "PDF report" });
    await expect(panel.getByText("No computed analyses yet")).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByRole("button", { name: /generate report/i })).toHaveCount(0);
  });
});
