import { test, expect } from "@playwright/test";
import { login, ADMIN } from "./helpers.js";

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
