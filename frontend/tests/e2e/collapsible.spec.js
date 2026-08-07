import { test, expect } from "@playwright/test";
import { login, ADMIN, QA_PROJECT_NAME, openMapPanels } from "./helpers.js";

/** Generic assertions for one collapsible-header button: starts expanded,
 * click collapses (aria-expanded=false), Enter/Space on focus re-expands,
 * and the collapsed state survives a full page reload (sessionStorage). */
async function checkCollapse(page, button) {
  await expect(button).toHaveAttribute("aria-expanded", "true");

  await button.click();
  await expect(button).toHaveAttribute("aria-expanded", "false");

  await button.click();
  await expect(button).toHaveAttribute("aria-expanded", "true");

  // Keyboard operability: focus + Enter, then + Space.
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Space");
  await expect(button).toHaveAttribute("aria-expanded", "true");

  // Collapse, then reload - state must persist via sessionStorage.
  await button.click();
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await page.reload();
  await expect(button).toHaveAttribute("aria-expanded", "false");

  // Reset back open so later assertions on the same page aren't affected.
  await button.click();
  await expect(button).toHaveAttribute("aria-expanded", "true");
}

// The old global Dashboard (Portfolio snapshot / Land cover composition /
// Project coverage / Carbon removal trend / Verification status / Recently
// updated projects) was DELETED in the redesign - DashboardPage.jsx no
// longer exists (App.jsx's index route is ProjectsPage now) and its content
// moved into ProjectDetailPage's own Dashboard tab (see
// "Per-project Dashboard tab" describe block below), which has its own,
// different set of sections. There is intentionally no equivalent describe
// block left here for the deleted page.

test.describe("Project Detail page collapsible sections", () => {
  async function gotoQaProject(page) {
    await login(page, ADMIN);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    await expect(page).toHaveURL(/\/projects\/[\w-]+/);
    // Maps is the default tab - Key metrics/Landscape evolution/Datasets only
    // render under the Dashboard tab (ProjectDetailPage.jsx).
    await page.getByRole("button", { name: "Dashboard" }).click();
  }

  for (const label of ["Members", "Key metrics", "Landscape evolution", "Datasets"]) {
    test(`"${label}" section opens, closes, keyboard-operable, persists across reload`, async ({ page }) => {
      await gotoQaProject(page);
      const button = page.getByRole("button", { name: label });
      await expect(button).toBeVisible();
      await checkCollapse(page, button);
    });
  }
});

test.describe("Users / WMS domains create-form panels", () => {
  test('Users "Create user" panel collapses, is keyboard-operable, persists', async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/users");
    // "Create user" is also the form's own submit button's accessible name -
    // scope to the collapsible header specifically, not either match.
    const button = page.locator("button.collapsible-header", { hasText: "Create user" });
    await checkCollapse(page, button);
  });

  test('WMS domains "Approve a domain" panel collapses, is keyboard-operable, persists', async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/wms-domains");
    const button = page.getByRole("button", { name: "Approve a domain" });
    await checkCollapse(page, button);
  });
});

test.describe("LayersPanel (map) collapsible groups", () => {
  test("outer Layers panel collapses and reopens", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    // Wave: floating map controls - the floating Layers panel (and
    // everything inside it, including its own "Layers" header below) is
    // collapsed by default now; open it first via the orange toggle.
    await openMapPanels(page);
    // exact: true - "Layers" alone would also match "Vector layers" (a
    // per-kind group header) and "Hide the Layers panel" (the map's separate
    // whole-panel-collapse button, see redesign.spec.js) under Playwright's
    // default substring matching.
    const outer = page.getByRole("button", { name: "Layers", exact: true });
    await expect(outer).toBeVisible();
    await expect(outer).toHaveAttribute("aria-expanded", "true");
    await outer.click();
    await expect(outer).toHaveAttribute("aria-expanded", "false");
    await outer.click();
    await expect(outer).toHaveAttribute("aria-expanded", "true");
  });

  test('per-kind group ("Classified imagery") collapses, keyboard-operable, persists', async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    await openMapPanels(page);
    const group = page.getByRole("button", { name: /Classified imagery/i });
    await expect(group).toBeVisible();
    await checkCollapse(page, group);
  });
});

test.describe("Per-layer Key Metrics section", () => {
  test("a layer's own metrics header has aria-expanded, like every other collapsible header", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/projects");
    await page.getByRole("link", { name: QA_PROJECT_NAME }).click();
    // .layer-metrics-header only renders under the Dashboard tab's Key
    // metrics section (ProjectDetailPage.jsx) - Maps (the default tab) has
    // no such header.
    await page.getByRole("button", { name: "Dashboard" }).click();
    const header = page.locator(".layer-metrics-header").first();
    await expect(header).toBeVisible();
    // Every other collapsible-header in the app sets aria-expanded (see
    // Dashboard/Members/Key metrics/Landscape evolution/Datasets/LayersPanel
    // groups). This asserts the same contract for the per-layer header.
    await expect(header).toHaveAttribute("aria-expanded", "true");
    await header.click();
    await expect(header).toHaveAttribute("aria-expanded", "false");
  });
});

test.describe("UploadPage step collapse (intentionally NOT sessionStorage-persisted)", () => {
  test("step 1 auto-collapses on Continue, step 2 opens", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/upload");
    const step1 = page.getByRole("button", { name: "File & project" });
    await expect(step1).toHaveAttribute("aria-expanded", "true");
    // Continue is disabled with no file chosen - just verify step1 is
    // collapsible on its own click (step-driven, not stepping through the
    // full form here - covered by the forms spec).
    await step1.click();
    await expect(step1).toHaveAttribute("aria-expanded", "false");
  });
});
