import { test, expect } from "@playwright/test";
import { login, uiLogin, ADMIN, VIEWER, collectConsoleErrors } from "./helpers.js";

test.describe("Login", () => {
  test("success lands on dashboard", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await uiLogin(page, ADMIN);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("invalid credentials show an error and stay on /login", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Username").fill("qa_admin");
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("Role-gated routes", () => {
  test("Administrator can reach Upload, Users, WMS domains", async ({ page }) => {
    await login(page, ADMIN);
    for (const path of ["/upload", "/users", "/wms-domains"]) {
      await page.goto(path);
      await expect(page).toHaveURL(new RegExp(path.replace("/", "\\/")));
    }
  });

  test("Viewer is redirected away from Upload, Users, WMS domains", async ({ page }) => {
    await login(page, VIEWER);
    for (const path of ["/upload", "/users", "/wms-domains"]) {
      await page.goto(path);
      // RoleRoute redirects to "/" (Dashboard) for a disallowed role.
      await expect(page).toHaveURL(/\/$|\/(?!upload|users|wms-domains)/);
      await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    }
  });

  test("Viewer's sidebar does not even show the gated nav links", async ({ page }) => {
    await login(page, VIEWER);
    await expect(page.getByRole("link", { name: /upload dataset/i })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /^users$/i })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /wms\/wfs domains/i })).toHaveCount(0);
  });
});
