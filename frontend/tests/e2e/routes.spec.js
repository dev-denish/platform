import { test, expect } from "@playwright/test";
import { login, ADMIN, QA_PROJECT_NAME, collectConsoleErrors } from "./helpers.js";

// Every top-level route, lazy-loaded via React.lazy/Suspense (App.jsx) as of
// the code-splitting perf wave. Checks: no console error, not stuck on the
// Suspense "Loading…" fallback, a real heading renders.
//
// Redesign: index route ("/") is ProjectsPage now, not the deleted
// DashboardPage - "/" and "/projects" both render the same component (see
// App.jsx's comment on why "/projects" is kept as a working path too).
const ROUTES = [
  { path: "/", heading: "Projects" },
  { path: "/projects", heading: "Projects" },
  { path: "/upload", heading: "Upload dataset" },
  { path: "/users", heading: "Users" },
  { path: "/wms-domains", heading: "WMS/WFS domains" },
];

test.describe("Route loads (code-split)", () => {
  for (const { path, heading } of ROUTES) {
    test(`${path} loads without console error and is not stuck on Suspense fallback`, async ({ page }) => {
      const errors = collectConsoleErrors(page);
      if (path !== "/") await login(page, ADMIN);
      else await login(page, ADMIN);
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, exact: false })).toBeVisible();
      // Fallback spinner text is "Loading…" - must not still be showing.
      await expect(page.getByText("Loading…", { exact: true })).toHaveCount(0);
      expect(errors, `console errors on ${path}: ${errors.join(" | ")}`).toEqual([]);
    });
  }

  test("Project Detail page loads for a real project", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await login(page, ADMIN);
    await page.goto("/projects");
    const link = page.getByRole("link", { name: QA_PROJECT_NAME });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/projects\/[\w-]+/);
    await expect(page.getByRole("heading", { level: 1, name: QA_PROJECT_NAME })).toBeVisible();
    expect(errors, `console errors on project detail: ${errors.join(" | ")}`).toEqual([]);
  });
});

test.describe("Code-split chunks", () => {
  test("each page route requests its own chunk, not one shared bundle", async ({ page }) => {
    await login(page, ADMIN);
    const jsRequests = [];
    page.on("request", (req) => {
      // Vite's DEV server (what webServer runs here - see playwright.config.js)
      // serves each lazy-imported module at its real source extension
      // (.jsx), not a bundled "assets/xyz-hash.js" chunk the way a production
      // build would - matching only ".js" here always found zero new
      // requests and passed for the wrong reason (no code was actually
      // exercised). ".jsx" is what a route's own lazy-loaded page module
      // (App.jsx's lazy(() => import(...))) actually requests in this mode.
      if (/\.(js|jsx)(\?.*)?$/.test(req.url())) jsRequests.push(req.url());
    });
    await page.goto("/projects");
    await page.waitForLoadState("networkidle");
    const afterProjects = [...jsRequests];
    await page.goto("/users");
    await page.waitForLoadState("networkidle");
    const newChunks = jsRequests.filter((u) => !afterProjects.includes(u));
    expect(newChunks.length, "expected a new chunk to load for /users").toBeGreaterThan(0);
  });
});
