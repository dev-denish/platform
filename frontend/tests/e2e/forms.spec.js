import { test, expect } from "@playwright/test";
import { login, ADMIN, API_BASE, QA_PROJECT_NAME } from "./helpers.js";

test.describe("Users: create form + pagination + bulk delete", () => {
  test("create user form validates and submits", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/users");
    const uniqueName = `qa_form_${Date.now()}`;
    // Scoped to the Create user panel itself: its Password field's own
    // accessible name is "PasswordAt least 8 characters." (the field-hint
    // span is inside the same <label>, so it's concatenated in) - not worth
    // matching exactly, and a plain substring match would also hit the
    // sidebar's Change-password dialog fields (mounted, just not open).
    const createPanel = page.locator("section.panel", { hasText: "Create user" }).first();
    await createPanel.getByLabel("Username").fill(uniqueName);
    await createPanel.getByLabel("Password").fill("short"); // below MIN_PASSWORD_LENGTH
    // The collapsible header button above this form is ALSO named "Create
    // user" - scope to the submit button specifically (type=submit).
    const createBtn = createPanel.getByRole("button", { name: "Create user" }).and(page.locator('[type="submit"]'));
    await expect(createBtn).toBeDisabled(); // inline validation before submit
    await createPanel.getByLabel("Password").fill("LongEnoughPass1");
    await expect(createBtn).toBeEnabled();
    await createBtn.click();
    await expect(page.getByText(`Account created for`)).toBeVisible();
    await expect(page.getByText(uniqueName).first()).toBeVisible();
  });

  test("pagination advances and goes back", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/users");
    const status = page.locator(".pagination-status");
    await expect(status).toContainText("Page 1 of");
    await page.getByRole("button", { name: /next/i }).click();
    await expect(status).toContainText("Page 2 of");
    await page.getByRole("button", { name: /previous/i }).click();
    await expect(status).toContainText("Page 1 of");
  });

  test("bulk-select + bulk permanent delete removes selected users", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/users");
    const rowCheckbox = page.getByRole("checkbox", { name: /^Select "/ }).first();
    const username = await page
      .locator("tbody tr")
      .first()
      .locator("td")
      .nth(1)
      .textContent();
    await rowCheckbox.check();
    await expect(page.locator(".bulk-action-bar-count")).toContainText("1 selected");
    // The bulk-action-bar's own "Permanently delete" button renders before
    // the table in the DOM, so .first() here (not the per-row link of the
    // same name) opens BulkPermanentDeleteDialog.
    await page.getByRole("button", { name: /permanently delete/i }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(username.trim()); // restates what's being deleted
    const confirmBtn = dialog.getByRole("button", { name: /permanently delete/i });
    await expect(confirmBtn).toBeDisabled(); // requires typing DELETE first - not a single click
    await dialog.getByLabel(/Type.*DELETE.*to confirm/i).fill("DELETE");
    await expect(confirmBtn).toBeEnabled();
    await confirmBtn.click();
    await expect(dialog).not.toBeVisible();
  });
});

test.describe("Projects: bulk delete confirmation restates names", () => {
  test("selecting projects and opening bulk-delete dialog lists their names", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/projects");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible(); // wait out the loading spinner
    const firstCheckbox = rows.first().locator('input[type="checkbox"]');
    await firstCheckbox.check();
    await page.getByRole("button", { name: "Delete" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: /cancel/i })).toBeVisible();
    await dialog.getByRole("button", { name: /cancel/i }).click();
    await expect(dialog).not.toBeVisible();
  });
});

test.describe("Confused-user behaviors", () => {
  test("garbage in a numeric field (accuracy_score) is rejected before submit", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/upload");
    // Step 1 requires a file to continue - can't reach step 2's numeric
    // field without one, so this exercises the browser-native number input's
    // own validation directly on step 2's Accuracy field via a file pick.
    await page.setInputFiles('input[type="file"]', {
      name: "test.geojson",
      mimeType: "application/geo+json",
      buffer: Buffer.from(
        JSON.stringify({ type: "FeatureCollection", features: [] })
      ),
    });
    await page.getByLabel("Project name").fill(`qa_upload_${Date.now()}`);
    await page.getByRole("button", { name: /continue/i }).click();
    const accuracy = page.getByLabel(/Accuracy score/i);
    if (await accuracy.count()) {
      await accuracy.focus();
      // A real browser's <input type=number> refuses non-numeric keystrokes
      // outright - Playwright's own .fill() enforces the same rule and
      // throws rather than typing it, which is itself evidence the
      // protection is real. pressSequentially drives actual keydown events
      // instead, letting the browser filter each character as a user's
      // keystrokes would be.
      await accuracy.pressSequentially("abc");
      await expect(accuracy).toHaveValue("");
    }
  });

  test("double-clicking Create user does not create two accounts", async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/users");
    const uniqueName = `qa_dbl_${Date.now()}`;
    const createPanel = page.locator("section.panel", { hasText: "Create user" }).first();
    await createPanel.getByLabel("Username").fill(uniqueName);
    await createPanel.getByLabel("Password").fill("LongEnoughPass1");
    const createBtn = createPanel.getByRole("button", { name: "Create user" }).and(page.locator('[type="submit"]'));
    // Two raw DOM clicks in the same task, bypassing Playwright's normal
    // actionability wait (which would just block on the button going
    // `disabled` after the first click) - this is what a real impatient
    // double-click looks like: both mousedowns land before React has had a
    // chance to re-render the disabled attribute.
    await createBtn.evaluate((el) => {
      el.click();
      el.click();
    });
    await expect(page.getByText("Account created for")).toBeVisible();
    // handleCreate reloads the list (setOffset(0) + load(0)) right after
    // succeeding, so the new row is already visible - a duplicate account
    // from a second POST would show up as a second matching row right here.
    await expect(page.locator("tbody").getByText(uniqueName, { exact: true })).toHaveCount(1);
  });
});

test.describe("Upload: project-name matching (Wave: upload project-name footgun fix)", () => {
  async function projectCount(page) {
    const token = await page.evaluate(() => sessionStorage.getItem("dmrv.access_token"));
    const res = await page.request.get(`${API_BASE}/projects?limit=200`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return (await res.json()).total;
  }

  test("a typo'd project name is rejected, not silently forked into a duplicate project", async ({ page }) => {
    // The exact reported bug: QA_PROJECT_NAME ("QA Regression Project")
    // already exists (seeded by global-setup) - typing a mismatched variant
    // (space -> underscore) used to silently create a second, empty-looking
    // duplicate project instead of erroring.
    await login(page, ADMIN);
    const before = await projectCount(page);

    await page.goto("/upload");
    await page.setInputFiles('input[type="file"]', {
      name: "typo-repro.geojson",
      mimeType: "application/geo+json",
      buffer: Buffer.from(
        JSON.stringify({
          type: "FeatureCollection",
          features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [76.3, 13.05] } }],
        })
      ),
    });
    await page.getByLabel("Project name").fill(QA_PROJECT_NAME.replace(/ /g, "_"));
    // "This is a new project" left UNCHECKED (the default) - the caller
    // never confirmed this should be a new project.
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByLabel("Source").fill("Typo repro (must be rejected)");
    await page.getByLabel("Date processed").fill("2026-01-01");
    // Step 1's own Continue button stays in the (inert) DOM after advancing,
    // so scope to the last one - step 2's - to avoid a strict-mode ambiguity.
    await page.getByRole("button", { name: /continue/i }).last().click();
    await page.getByRole("button", { name: /submit for ingestion/i }).click();

    await expect(page.getByRole("heading", { name: "Ingest failed" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("alert")).toContainText(/no project named/i);

    expect(await projectCount(page)).toBe(before);
  });

  test("uploading with the exact existing project name still succeeds, no confirmation needed", async ({ page }) => {
    // The regression risk: the common, valid case of re-uploading to the
    // SAME correctly-named project must keep working unchanged.
    await login(page, ADMIN);
    const before = await projectCount(page);

    await page.goto("/upload");
    await page.setInputFiles('input[type="file"]', {
      name: "matching-name-reupload.geojson",
      mimeType: "application/geo+json",
      buffer: Buffer.from(
        JSON.stringify({
          type: "FeatureCollection",
          features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [76.3, 13.05] } }],
        })
      ),
    });
    await page.getByLabel("Project name").fill(QA_PROJECT_NAME);
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByLabel("Source").fill("Legit re-upload");
    // Reuses one of global-setup's own seeded dates (2024-06-01, the
    // 9-class LULC layer) rather than a fresh one - datedLayerGroups
    // (lib/timeline.js) groups the Dashboard's "Monitoring periods" by exact
    // date_processed, so a fresh date here would add a THIRD distinct
    // period and break redesign.spec.js's "lists the dated layers" test,
    // which depends on this shared project having exactly the 2 seeded ones.
    await page.getByLabel("Date processed").fill("2024-06-01");
    await page.getByRole("button", { name: /continue/i }).last().click();
    await page.getByRole("button", { name: /submit for ingestion/i }).click();

    await expect(page.getByRole("heading", { name: "Ingest complete" })).toBeVisible({ timeout: 20_000 });
    // Reused the existing project, not a new one.
    expect(await projectCount(page)).toBe(before);
  });
});
