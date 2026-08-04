# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: forms.spec.js >> Users: create form + pagination + bulk delete >> pagination advances and goes back
- Location: tests/e2e/forms.spec.js:28:3

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('.pagination-status')
Expected substring: "Page 1 of"
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('.pagination-status')

```

```yaml
- complementary:
  - text: dMRV Analytical Platform
  - navigation:
    - text: Overview
    - link "Dashboard":
      - /url: /
    - text: Projects
    - link "Projects":
      - /url: /projects
    - link "Upload dataset":
      - /url: /upload
    - text: Administration
    - link "Users":
      - /url: /users
    - link "WMS/WFS domains":
      - /url: /wms-domains
  - text: qa_admin Administrator
  - button "Change password"
  - button "Sign out"
- main:
  - paragraph: Administration
  - heading "Users" [level=1]
  - button "Create user" [expanded]
  - text: Username
  - textbox "Username"
  - text: Password
  - textbox "Password At least 8 characters."
  - text: At least 8 characters. Role
  - combobox "Role":
    - option "Administrator"
    - option "GIS Associate"
    - option "Analyst"
    - option "Verifier"
    - option "Viewer" [selected]
  - button "Create user" [disabled]
  - heading "All accounts" [level=2]
  - checkbox "Show hidden"
  - text: Show hidden
  - table:
    - rowgroup:
      - row "Select all users on this page Username Role Created Status":
        - columnheader "Select all users on this page":
          - checkbox "Select all users on this page"
        - columnheader "Username"
        - columnheader "Role"
        - columnheader "Created"
        - columnheader "Status"
        - columnheader
    - rowgroup:
      - row "Select \"qa_admin\" qa_admin Administrator Aug 4, 2026 Active Deactivate Hide Permanently delete":
        - cell "Select \"qa_admin\"":
          - checkbox "Select \"qa_admin\""
        - cell "qa_admin"
        - cell "Administrator"
        - cell "Aug 4, 2026"
        - cell "Active"
        - cell "Deactivate Hide Permanently delete":
          - button "Deactivate"
          - button "Hide"
          - button "Permanently delete"
      - row "Select \"qa_form_1785809617052\" qa_form_1785809617052 Viewer Aug 4, 2026 Active Deactivate Hide Reset password Permanently delete":
        - cell "Select \"qa_form_1785809617052\"":
          - checkbox "Select \"qa_form_1785809617052\""
        - cell "qa_form_1785809617052"
        - cell "Viewer"
        - cell "Aug 4, 2026"
        - cell "Active"
        - cell "Deactivate Hide Reset password Permanently delete":
          - button "Deactivate"
          - button "Hide"
          - button "Reset password"
          - button "Permanently delete"
      - row "Select \"qa_viewer\" qa_viewer Viewer Aug 4, 2026 Active Deactivate Hide Reset password Permanently delete":
        - cell "Select \"qa_viewer\"":
          - checkbox "Select \"qa_viewer\""
        - cell "qa_viewer"
        - cell "Viewer"
        - cell "Aug 4, 2026"
        - cell "Active"
        - cell "Deactivate Hide Reset password Permanently delete":
          - button "Deactivate"
          - button "Hide"
          - button "Reset password"
          - button "Permanently delete"
```

# Test source

```ts
  1   | import { test, expect } from "@playwright/test";
  2   | import { login, ADMIN } from "./helpers.js";
  3   | 
  4   | test.describe("Users: create form + pagination + bulk delete", () => {
  5   |   test("create user form validates and submits", async ({ page }) => {
  6   |     await login(page, ADMIN);
  7   |     await page.goto("/users");
  8   |     const uniqueName = `qa_form_${Date.now()}`;
  9   |     // Scoped to the Create user panel itself: its Password field's own
  10  |     // accessible name is "PasswordAt least 8 characters." (the field-hint
  11  |     // span is inside the same <label>, so it's concatenated in) - not worth
  12  |     // matching exactly, and a plain substring match would also hit the
  13  |     // sidebar's Change-password dialog fields (mounted, just not open).
  14  |     const createPanel = page.locator("section.panel", { hasText: "Create user" }).first();
  15  |     await createPanel.getByLabel("Username").fill(uniqueName);
  16  |     await createPanel.getByLabel("Password").fill("short"); // below MIN_PASSWORD_LENGTH
  17  |     // The collapsible header button above this form is ALSO named "Create
  18  |     // user" - scope to the submit button specifically (type=submit).
  19  |     const createBtn = createPanel.getByRole("button", { name: "Create user" }).and(page.locator('[type="submit"]'));
  20  |     await expect(createBtn).toBeDisabled(); // inline validation before submit
  21  |     await createPanel.getByLabel("Password").fill("LongEnoughPass1");
  22  |     await expect(createBtn).toBeEnabled();
  23  |     await createBtn.click();
  24  |     await expect(page.getByText(`Account created for`)).toBeVisible();
  25  |     await expect(page.getByText(uniqueName).first()).toBeVisible();
  26  |   });
  27  | 
  28  |   test("pagination advances and goes back", async ({ page }) => {
  29  |     await login(page, ADMIN);
  30  |     await page.goto("/users");
  31  |     const status = page.locator(".pagination-status");
> 32  |     await expect(status).toContainText("Page 1 of");
      |                          ^ Error: expect(locator).toContainText(expected) failed
  33  |     await page.getByRole("button", { name: /next/i }).click();
  34  |     await expect(status).toContainText("Page 2 of");
  35  |     await page.getByRole("button", { name: /previous/i }).click();
  36  |     await expect(status).toContainText("Page 1 of");
  37  |   });
  38  | 
  39  |   test("bulk-select + bulk permanent delete removes selected users", async ({ page }) => {
  40  |     await login(page, ADMIN);
  41  |     await page.goto("/users");
  42  |     const rowCheckbox = page.getByRole("checkbox", { name: /^Select "/ }).first();
  43  |     const username = await page
  44  |       .locator("tbody tr")
  45  |       .first()
  46  |       .locator("td")
  47  |       .nth(1)
  48  |       .textContent();
  49  |     await rowCheckbox.check();
  50  |     await expect(page.locator(".bulk-action-bar-count")).toContainText("1 selected");
  51  |     // The bulk-action-bar's own "Permanently delete" button renders before
  52  |     // the table in the DOM, so .first() here (not the per-row link of the
  53  |     // same name) opens BulkPermanentDeleteDialog.
  54  |     await page.getByRole("button", { name: /permanently delete/i }).first().click();
  55  |     const dialog = page.getByRole("dialog");
  56  |     await expect(dialog).toBeVisible();
  57  |     await expect(dialog).toContainText(username.trim()); // restates what's being deleted
  58  |     const confirmBtn = dialog.getByRole("button", { name: /permanently delete/i });
  59  |     await expect(confirmBtn).toBeDisabled(); // requires typing DELETE first - not a single click
  60  |     await dialog.getByLabel(/Type.*DELETE.*to confirm/i).fill("DELETE");
  61  |     await expect(confirmBtn).toBeEnabled();
  62  |     await confirmBtn.click();
  63  |     await expect(dialog).not.toBeVisible();
  64  |   });
  65  | });
  66  | 
  67  | test.describe("Projects: bulk delete confirmation restates names", () => {
  68  |   test("selecting projects and opening bulk-delete dialog lists their names", async ({ page }) => {
  69  |     await login(page, ADMIN);
  70  |     await page.goto("/projects");
  71  |     const rows = page.locator("tbody tr");
  72  |     await expect(rows.first()).toBeVisible(); // wait out the loading spinner
  73  |     const firstCheckbox = rows.first().locator('input[type="checkbox"]');
  74  |     await firstCheckbox.check();
  75  |     await page.getByRole("button", { name: "Delete" }).first().click();
  76  |     const dialog = page.getByRole("dialog");
  77  |     await expect(dialog).toBeVisible();
  78  |     await expect(dialog.getByRole("button", { name: /cancel/i })).toBeVisible();
  79  |     await dialog.getByRole("button", { name: /cancel/i }).click();
  80  |     await expect(dialog).not.toBeVisible();
  81  |   });
  82  | });
  83  | 
  84  | test.describe("Confused-user behaviors", () => {
  85  |   test("garbage in a numeric field (accuracy_score) is rejected before submit", async ({ page }) => {
  86  |     await login(page, ADMIN);
  87  |     await page.goto("/upload");
  88  |     // Step 1 requires a file to continue - can't reach step 2's numeric
  89  |     // field without one, so this exercises the browser-native number input's
  90  |     // own validation directly on step 2's Accuracy field via a file pick.
  91  |     await page.setInputFiles('input[type="file"]', {
  92  |       name: "test.geojson",
  93  |       mimeType: "application/geo+json",
  94  |       buffer: Buffer.from(
  95  |         JSON.stringify({ type: "FeatureCollection", features: [] })
  96  |       ),
  97  |     });
  98  |     await page.getByLabel("Project name").fill(`qa_upload_${Date.now()}`);
  99  |     await page.getByRole("button", { name: /continue/i }).click();
  100 |     const accuracy = page.getByLabel(/Accuracy score/i);
  101 |     if (await accuracy.count()) {
  102 |       await accuracy.focus();
  103 |       // A real browser's <input type=number> refuses non-numeric keystrokes
  104 |       // outright - Playwright's own .fill() enforces the same rule and
  105 |       // throws rather than typing it, which is itself evidence the
  106 |       // protection is real. pressSequentially drives actual keydown events
  107 |       // instead, letting the browser filter each character as a user's
  108 |       // keystrokes would be.
  109 |       await accuracy.pressSequentially("abc");
  110 |       await expect(accuracy).toHaveValue("");
  111 |     }
  112 |   });
  113 | 
  114 |   test("double-clicking Create user does not create two accounts", async ({ page }) => {
  115 |     await login(page, ADMIN);
  116 |     await page.goto("/users");
  117 |     const uniqueName = `qa_dbl_${Date.now()}`;
  118 |     const createPanel = page.locator("section.panel", { hasText: "Create user" }).first();
  119 |     await createPanel.getByLabel("Username").fill(uniqueName);
  120 |     await createPanel.getByLabel("Password").fill("LongEnoughPass1");
  121 |     const createBtn = createPanel.getByRole("button", { name: "Create user" }).and(page.locator('[type="submit"]'));
  122 |     // Two raw DOM clicks in the same task, bypassing Playwright's normal
  123 |     // actionability wait (which would just block on the button going
  124 |     // `disabled` after the first click) - this is what a real impatient
  125 |     // double-click looks like: both mousedowns land before React has had a
  126 |     // chance to re-render the disabled attribute.
  127 |     await createBtn.evaluate((el) => {
  128 |       el.click();
  129 |       el.click();
  130 |     });
  131 |     await expect(page.getByText("Account created for")).toBeVisible();
  132 |     // handleCreate reloads the list (setOffset(0) + load(0)) right after
```