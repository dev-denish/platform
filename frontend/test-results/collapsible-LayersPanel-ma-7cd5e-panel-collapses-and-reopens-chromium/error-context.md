# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: collapsible.spec.js >> LayersPanel (map) collapsible groups >> outer Layers panel collapses and reopens
- Location: tests/e2e/collapsible.spec.js:91:3

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.click: Test timeout of 30000ms exceeded.
Call log:
  - waiting for locator('.table-link').first()

```

# Page snapshot

```yaml
- generic [ref=f1e3]:
  - complementary [ref=f1e4]:
    - generic [ref=f1e7]:
      - generic [ref=f1e8]: dMRV
      - generic [ref=f1e9]: Analytical Platform
    - navigation [ref=f1e10]:
      - generic [ref=f1e11]:
        - generic [ref=f1e12]: Overview
        - link "Dashboard" [ref=f1e13] [cursor=pointer]:
          - /url: /
      - generic [ref=f1e16]:
        - generic [ref=f1e17]: Projects
        - link "Projects" [ref=f1e18] [cursor=pointer]:
          - /url: /projects
        - link "Upload dataset" [ref=f1e21] [cursor=pointer]:
          - /url: /upload
      - generic [ref=f1e24]:
        - generic [ref=f1e25]: Administration
        - link "Users" [ref=f1e26] [cursor=pointer]:
          - /url: /users
        - link "WMS/WFS domains" [ref=f1e29] [cursor=pointer]:
          - /url: /wms-domains
    - generic [ref=f1e32]:
      - generic [ref=f1e33]:
        - generic [ref=f1e34]: qa_admin
        - text: Administrator
      - generic [ref=f1e35]:
        - button "Change password" [ref=f1e36] [cursor=pointer]
        - button "Sign out" [ref=f1e37] [cursor=pointer]
  - main [ref=f1e38]:
    - generic [ref=f1e39]:
      - generic [ref=f1e41]:
        - paragraph [ref=f1e42]: Registry
        - heading "Projects" [level=1] [ref=f1e43]
      - searchbox "Search projects by name" [ref=f1e44]
      - generic [ref=f1e45]:
        - paragraph [ref=f1e50]: No projects yet
        - paragraph [ref=f1e51]: Ingest a dataset to create the first project.
```

# Test source

```ts
  1   | import { test, expect } from "@playwright/test";
  2   | import { login, ADMIN } from "./helpers.js";
  3   | 
  4   | /** Generic assertions for one collapsible-header button: starts expanded,
  5   |  * click collapses (aria-expanded=false), Enter/Space on focus re-expands,
  6   |  * and the collapsed state survives a full page reload (sessionStorage). */
  7   | async function checkCollapse(page, button) {
  8   |   await expect(button).toHaveAttribute("aria-expanded", "true");
  9   | 
  10  |   await button.click();
  11  |   await expect(button).toHaveAttribute("aria-expanded", "false");
  12  | 
  13  |   await button.click();
  14  |   await expect(button).toHaveAttribute("aria-expanded", "true");
  15  | 
  16  |   // Keyboard operability: focus + Enter, then + Space.
  17  |   await button.focus();
  18  |   await page.keyboard.press("Enter");
  19  |   await expect(button).toHaveAttribute("aria-expanded", "false");
  20  |   await page.keyboard.press("Space");
  21  |   await expect(button).toHaveAttribute("aria-expanded", "true");
  22  | 
  23  |   // Collapse, then reload - state must persist via sessionStorage.
  24  |   await button.click();
  25  |   await expect(button).toHaveAttribute("aria-expanded", "false");
  26  |   await page.reload();
  27  |   await expect(button).toHaveAttribute("aria-expanded", "false");
  28  | 
  29  |   // Reset back open so later assertions on the same page aren't affected.
  30  |   await button.click();
  31  |   await expect(button).toHaveAttribute("aria-expanded", "true");
  32  | }
  33  | 
  34  | test.describe("Dashboard's 6 collapsible panels", () => {
  35  |   const PANELS = [
  36  |     "Portfolio snapshot",
  37  |     "Land cover composition",
  38  |     "Project coverage",
  39  |     "Carbon removal trend",
  40  |     "Verification status",
  41  |     "Recently updated projects",
  42  |   ];
  43  | 
  44  |   for (const label of PANELS) {
  45  |     test(`"${label}" opens, closes, keyboard-operable, persists across reload`, async ({ page }) => {
  46  |       await login(page, ADMIN);
  47  |       const button = page.getByRole("button", { name: label });
  48  |       await expect(button).toBeVisible();
  49  |       await checkCollapse(page, button);
  50  |     });
  51  |   }
  52  | });
  53  | 
  54  | test.describe("Project Detail page collapsible sections", () => {
  55  |   async function gotoFirstProject(page) {
  56  |     await login(page, ADMIN);
  57  |     await page.goto("/projects");
  58  |     await page.locator(".table-link").first().click();
  59  |     await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  60  |   }
  61  | 
  62  |   for (const label of ["Members", "Key metrics", "Landscape evolution", "Datasets"]) {
  63  |     test(`"${label}" section opens, closes, keyboard-operable, persists across reload`, async ({ page }) => {
  64  |       await gotoFirstProject(page);
  65  |       const button = page.getByRole("button", { name: label });
  66  |       await expect(button).toBeVisible();
  67  |       await checkCollapse(page, button);
  68  |     });
  69  |   }
  70  | });
  71  | 
  72  | test.describe("Users / WMS domains create-form panels", () => {
  73  |   test('Users "Create user" panel collapses, is keyboard-operable, persists', async ({ page }) => {
  74  |     await login(page, ADMIN);
  75  |     await page.goto("/users");
  76  |     // "Create user" is also the form's own submit button's accessible name -
  77  |     // scope to the collapsible header specifically, not either match.
  78  |     const button = page.locator("button.collapsible-header", { hasText: "Create user" });
  79  |     await checkCollapse(page, button);
  80  |   });
  81  | 
  82  |   test('WMS domains "Approve a domain" panel collapses, is keyboard-operable, persists', async ({ page }) => {
  83  |     await login(page, ADMIN);
  84  |     await page.goto("/wms-domains");
  85  |     const button = page.getByRole("button", { name: "Approve a domain" });
  86  |     await checkCollapse(page, button);
  87  |   });
  88  | });
  89  | 
  90  | test.describe("LayersPanel (map) collapsible groups", () => {
  91  |   test("outer Layers panel collapses and reopens", async ({ page }) => {
  92  |     await login(page, ADMIN);
  93  |     await page.goto("/projects");
> 94  |     await page.locator(".table-link").first().click();
      |                                               ^ Error: locator.click: Test timeout of 30000ms exceeded.
  95  |     const outer = page.getByRole("button", { name: "Layers" });
  96  |     await expect(outer).toBeVisible();
  97  |     await expect(outer).toHaveAttribute("aria-expanded", "true");
  98  |     await outer.click();
  99  |     await expect(outer).toHaveAttribute("aria-expanded", "false");
  100 |     await outer.click();
  101 |     await expect(outer).toHaveAttribute("aria-expanded", "true");
  102 |   });
  103 | 
  104 |   test('per-kind group ("Classified imagery") collapses, keyboard-operable, persists', async ({ page }) => {
  105 |     await login(page, ADMIN);
  106 |     await page.goto("/projects");
  107 |     await page.locator(".table-link").first().click();
  108 |     const group = page.getByRole("button", { name: /Classified imagery/i });
  109 |     await expect(group).toBeVisible();
  110 |     await checkCollapse(page, group);
  111 |   });
  112 | });
  113 | 
  114 | test.describe("Per-layer Key Metrics section", () => {
  115 |   test("a layer's own metrics header has aria-expanded, like every other collapsible header", async ({ page }) => {
  116 |     await login(page, ADMIN);
  117 |     await page.goto("/projects");
  118 |     await page.locator(".table-link").first().click();
  119 |     const header = page.locator(".layer-metrics-header").first();
  120 |     await expect(header).toBeVisible();
  121 |     // Every other collapsible-header in the app sets aria-expanded (see
  122 |     // Dashboard/Members/Key metrics/Landscape evolution/Datasets/LayersPanel
  123 |     // groups). This asserts the same contract for the per-layer header.
  124 |     await expect(header).toHaveAttribute("aria-expanded", "true");
  125 |     await header.click();
  126 |     await expect(header).toHaveAttribute("aria-expanded", "false");
  127 |   });
  128 | });
  129 | 
  130 | test.describe("UploadPage step collapse (intentionally NOT sessionStorage-persisted)", () => {
  131 |   test("step 1 auto-collapses on Continue, step 2 opens", async ({ page }) => {
  132 |     await login(page, ADMIN);
  133 |     await page.goto("/upload");
  134 |     const step1 = page.getByRole("button", { name: "File & project" });
  135 |     await expect(step1).toHaveAttribute("aria-expanded", "true");
  136 |     // Continue is disabled with no file chosen - just verify step1 is
  137 |     // collapsible on its own click (step-driven, not stepping through the
  138 |     // full form here - covered by the forms spec).
  139 |     await step1.click();
  140 |     await expect(step1).toHaveAttribute("aria-expanded", "false");
  141 |   });
  142 | });
  143 | 
```