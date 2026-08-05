import { test, expect } from "@playwright/test";
import { login, uiLogin, ADMIN, VIEWER, GIS_ASSOCIATE, ANALYST, VERIFIER, collectConsoleErrors } from "./helpers.js";

test.describe("Login", () => {
  // Redesign: index route is now Projects (DashboardPage was deleted, its
  // content moved into ProjectDetailPage's Dashboard tab - see App.jsx).
  test("success lands on the Projects list", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await uiLogin(page, ADMIN);
    await expect(page).toHaveURL(/\/$|\/projects$/);
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
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

  test("GIS Associate can reach Upload but is redirected away from Users and WMS domains", async ({ page }) => {
    await login(page, GIS_ASSOCIATE);
    await page.goto("/upload");
    await expect(page).toHaveURL(/\/upload/);
    for (const path of ["/users", "/wms-domains"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/$|\/projects$/);
      await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    }
  });

  test("Analyst is redirected away from Upload, Users, WMS domains", async ({ page }) => {
    await login(page, ANALYST);
    for (const path of ["/upload", "/users", "/wms-domains"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/$|\/projects$/);
      await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    }
  });

  test("Verifier is redirected away from Upload, Users, WMS domains", async ({ page }) => {
    await login(page, VERIFIER);
    for (const path of ["/upload", "/users", "/wms-domains"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/$|\/projects$/);
      await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    }
  });

  test("Viewer is redirected away from Upload, Users, WMS domains", async ({ page }) => {
    await login(page, VIEWER);
    for (const path of ["/upload", "/users", "/wms-domains"]) {
      await page.goto(path);
      // RoleRoute redirects to "/" (Projects, post-redesign) for a disallowed role.
      await expect(page).toHaveURL(/\/$|\/(?!upload|users|wms-domains)/);
      await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    }
  });

  test("Viewer's sidebar does not even show the gated nav links", async ({ page }) => {
    await login(page, VIEWER);
    await expect(page.getByRole("link", { name: /upload dataset/i })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /^users$/i })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /wms\/wfs domains/i })).toHaveCount(0);
  });

  test("Analyst and Verifier sidebars do not show the gated nav links either", async ({ page }) => {
    for (const creds of [ANALYST, VERIFIER]) {
      await login(page, creds);
      await expect(page.getByRole("link", { name: /upload dataset/i })).toHaveCount(0);
      await expect(page.getByRole("link", { name: /^users$/i })).toHaveCount(0);
      await expect(page.getByRole("link", { name: /wms\/wfs domains/i })).toHaveCount(0);
    }
  });
});
