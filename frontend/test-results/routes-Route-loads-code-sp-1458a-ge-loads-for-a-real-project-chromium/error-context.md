# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: routes.spec.js >> Route loads (code-split) >> Project Detail page loads for a real project
- Location: tests/e2e/routes.spec.js:29:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('.table-link').first()
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('.table-link').first()

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
  - paragraph: Registry
  - heading "Projects" [level=1]
  - searchbox "Search projects by name"
  - paragraph: No projects yet
  - paragraph: Ingest a dataset to create the first project.
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | import { login, ADMIN, collectConsoleErrors } from "./helpers.js";
  3  | 
  4  | // Every top-level route, lazy-loaded via React.lazy/Suspense (App.jsx) as of
  5  | // the code-splitting perf wave. Checks: no console error, not stuck on the
  6  | // Suspense "Loading…" fallback, a real heading renders.
  7  | const ROUTES = [
  8  |   { path: "/", heading: "Dashboard" },
  9  |   { path: "/projects", heading: "Projects" },
  10 |   { path: "/upload", heading: "Upload dataset" },
  11 |   { path: "/users", heading: "Users" },
  12 |   { path: "/wms-domains", heading: "WMS/WFS domains" },
  13 | ];
  14 | 
  15 | test.describe("Route loads (code-split)", () => {
  16 |   for (const { path, heading } of ROUTES) {
  17 |     test(`${path} loads without console error and is not stuck on Suspense fallback`, async ({ page }) => {
  18 |       const errors = collectConsoleErrors(page);
  19 |       if (path !== "/") await login(page, ADMIN);
  20 |       else await login(page, ADMIN);
  21 |       await page.goto(path);
  22 |       await expect(page.getByRole("heading", { name: heading, exact: false })).toBeVisible();
  23 |       // Fallback spinner text is "Loading…" - must not still be showing.
  24 |       await expect(page.getByText("Loading…", { exact: true })).toHaveCount(0);
  25 |       expect(errors, `console errors on ${path}: ${errors.join(" | ")}`).toEqual([]);
  26 |     });
  27 |   }
  28 | 
  29 |   test("Project Detail page loads for a real project", async ({ page }) => {
  30 |     const errors = collectConsoleErrors(page);
  31 |     await login(page, ADMIN);
  32 |     await page.goto("/projects");
  33 |     const firstLink = page.locator(".table-link").first();
> 34 |     await expect(firstLink).toBeVisible();
     |                             ^ Error: expect(locator).toBeVisible() failed
  35 |     await firstLink.click();
  36 |     await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  37 |     await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  38 |     expect(errors, `console errors on project detail: ${errors.join(" | ")}`).toEqual([]);
  39 |   });
  40 | });
  41 | 
  42 | test.describe("Code-split chunks", () => {
  43 |   test("each page route requests its own chunk, not one shared bundle", async ({ page }) => {
  44 |     await login(page, ADMIN);
  45 |     const jsRequests = [];
  46 |     page.on("request", (req) => {
  47 |       if (req.url().endsWith(".js")) jsRequests.push(req.url());
  48 |     });
  49 |     await page.goto("/projects");
  50 |     await page.waitForLoadState("networkidle");
  51 |     const afterProjects = [...jsRequests];
  52 |     await page.goto("/users");
  53 |     await page.waitForLoadState("networkidle");
  54 |     const newChunks = jsRequests.filter((u) => !afterProjects.includes(u));
  55 |     expect(newChunks.length, "expected a new chunk to load for /users").toBeGreaterThan(0);
  56 |   });
  57 | });
  58 | 
```