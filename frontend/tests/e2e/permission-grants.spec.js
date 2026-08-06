import { test, expect } from "@playwright/test";
import { login, readTokens, ADMIN, ANALYST, API_BASE } from "./helpers.js";

// Wave: permission grants. Runs against qa_analyst (fixture role account -
// see helpers.js), which no other spec grants/revokes individual
// permissions on. Preconditions are set up via direct API calls (not by
// chaining state through the UI across tests) so each test's starting point
// is deterministic regardless of test order or a prior test failing
// mid-way - only the ONE interaction actually under test goes through the
// UI. afterEach always revokes, so a failure never leaks the grant into
// whatever spec happens to run next.

let analystId;

async function adminFetch(path, opts = {}) {
  const token = readTokens(ADMIN.username).access_token;
  return fetch(`${API_BASE}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(opts.headers ?? {}) },
  });
}

async function setGrant(granted) {
  await adminFetch(`/users/${analystId}/permissions/edit_forest_definition`, {
    method: granted ? "PUT" : "DELETE",
  });
}

test.beforeAll(async () => {
  const res = await adminFetch("/users?limit=200");
  const { items } = await res.json();
  analystId = items.find((u) => u.username === ANALYST.username).user_id;
});

test.afterEach(async () => {
  await setGrant(false);
});

async function openManagePanel(page, username) {
  await page.goto("/users");
  const row = page.locator("tbody tr", { hasText: username });
  await row.getByRole("button", { name: "Manage" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

test.describe("Permission grants: Users page", () => {
  test("Administrator can grant and revoke edit_forest_definition via the Manage panel", async ({ page }) => {
    await login(page, ADMIN);
    const dialog = await openManagePanel(page, ANALYST.username);
    const toggle = dialog.getByLabel("Edit forest-definition threshold");
    await expect(toggle).not.toBeChecked();

    await toggle.check();
    await expect(toggle).toBeChecked();
    await expect(dialog.getByText(/Granted by qa_admin/)).toBeVisible();
    await expect(page.locator("tbody tr", { hasText: ANALYST.username })).toContainText("1 granted");

    await toggle.uncheck();
    await expect(toggle).not.toBeChecked();
    await expect(dialog.getByText(/Granted by/)).toHaveCount(0);
    await expect(page.locator("tbody tr", { hasText: ANALYST.username })).toContainText("None");
  });

  test('Administrator row shows "All (implicit)" with no Manage action', async ({ page }) => {
    await login(page, ADMIN);
    await page.goto("/users");
    const row = page.locator("tbody tr", { hasText: ADMIN.username });
    await expect(row).toContainText("All (implicit)");
    await expect(row.getByRole("button", { name: "Manage" })).toHaveCount(0);
  });
});

test.describe("Forest-definition threshold: permission-gated editing", () => {
  test("non-Administrator without the grant is read-only, and a direct PUT is rejected with 403", async ({
    page,
  }) => {
    await setGrant(false);
    await login(page, ANALYST);
    await page.goto("/forest-definition");
    await expect(page.getByRole("heading", { name: "Forest definition" })).toBeVisible();
    await expect(page.getByLabel(/Canopy cover/)).toHaveCount(0); // no editable input rendered
    await expect(page.getByRole("button", { name: /Review & save/ })).toHaveCount(0);

    // The UI hides the form entirely, but the server-side gate is the real
    // enforcement point - prove it directly, same request shape apiFetch
    // itself would send, from inside the authenticated browser session.
    const status = await page.evaluate(async (apiBase) => {
      const token = sessionStorage.getItem("dmrv.access_token");
      const res = await fetch(`${apiBase}/forest-definition`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ canopy_cover_pct: 99, min_height_m: 9, min_area_ha: 9 }),
      });
      return res.status;
    }, API_BASE);
    expect(status).toBe(403);
  });

  test("a grantee (not an Administrator) can edit and save the threshold from the real form", async ({
    page,
  }) => {
    await setGrant(true);
    await login(page, ANALYST);
    await page.goto("/forest-definition");
    const canopyInput = page.getByLabel(/Canopy cover/);
    await expect(canopyInput).toBeVisible();
    await canopyInput.fill("18");
    await page.getByLabel(/Minimum height/).fill("2.5");
    await page.getByLabel(/Minimum area/).fill("0.08");
    await page.getByRole("button", { name: /Review & save/ }).click();

    const confirmDialog = page.getByRole("dialog");
    await expect(confirmDialog).toBeVisible();
    await expect(confirmDialog).toContainText("15%"); // old value
    await expect(confirmDialog).toContainText("18%"); // new value
    await confirmDialog.getByRole("button", { name: "Save" }).click();
    await expect(confirmDialog).not.toBeVisible();

    await expect(page.getByText(/Last updated .* by qa_analyst/)).toBeVisible();
    await expect(canopyInput).toHaveValue("18");

    // Persisted for real, not just echoed - a fresh load shows the same
    // value, and restore the seeded defaults so no later run sees this edit.
    await page.reload();
    await expect(page.getByLabel(/Canopy cover/)).toHaveValue("18");
    await page.getByLabel(/Canopy cover/).fill("15");
    await page.getByLabel(/Minimum height/).fill("2");
    await page.getByLabel(/Minimum area/).fill("0.05");
    await page.getByRole("button", { name: /Review & save/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
  });

  test("revoking the grant makes the threshold read-only again for that same user", async ({ page }) => {
    await setGrant(true);
    await login(page, ANALYST);
    await page.goto("/forest-definition");
    await expect(page.getByLabel(/Canopy cover/)).toBeVisible(); // has access before revoke

    await setGrant(false);
    await page.reload();
    await expect(page.getByLabel(/Canopy cover/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Review & save/ })).toHaveCount(0);
    // Revoking edit access never hides the value itself - still readable.
    // exact:true - a substring match would also hit the (unopened, but
    // always-mounted) confirm dialog's own "15% -> 15%" detail text.
    await expect(page.getByText("15%", { exact: true })).toBeVisible();
  });
});
