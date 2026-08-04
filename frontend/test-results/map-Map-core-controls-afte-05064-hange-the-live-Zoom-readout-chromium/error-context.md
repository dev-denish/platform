# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: map.spec.js >> Map: core controls after memoization/throttling changes >> zoom in/out buttons change the live Zoom readout
- Location: tests/e2e/map.spec.js:18:3

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
  2   | import { login, ADMIN, collectConsoleErrors } from "./helpers.js";
  3   | 
  4   | async function gotoProjectWithMap(page) {
  5   |   await login(page, ADMIN);
  6   |   await page.goto("/projects");
> 7   |   await page.locator(".table-link").first().click();
      |                                             ^ Error: locator.click: Test timeout of 30000ms exceeded.
  8   |   await expect(page).toHaveURL(/\/projects\/[\w-]+/);
  9   |   const map = page.locator(".leaflet-container");
  10  |   await expect(map).toBeVisible();
  11  |   // Click once first - scroll-zoom and most interaction is gated on the map
  12  |   // having been clicked/focused at least once (ScrollZoomOnActivate).
  13  |   await map.click({ position: { x: 400, y: 300 } });
  14  |   return map;
  15  | }
  16  | 
  17  | test.describe("Map: core controls after memoization/throttling changes", () => {
  18  |   test("zoom in/out buttons change the live Zoom readout", async ({ page }) => {
  19  |     await gotoProjectWithMap(page);
  20  |     const readout = page.locator(".map-toolbar-readout");
  21  |     const zoomText = () => readout.textContent();
  22  |     const before = await zoomText();
  23  |     await page.getByRole("button", { name: "Zoom in" }).click();
  24  |     await expect(async () => {
  25  |       expect(await zoomText()).not.toEqual(before);
  26  |     }).toPass();
  27  |   });
  28  | 
  29  |   test("Extent button re-fits the map (no crash, map stays visible)", async ({ page }) => {
  30  |     const map = await gotoProjectWithMap(page);
  31  |     await page.getByRole("button", { name: "Extent" }).click();
  32  |     await expect(map).toBeVisible();
  33  |   });
  34  | 
  35  |   test("pan (drag) moves the map center", async ({ page }) => {
  36  |     await gotoProjectWithMap(page);
  37  |     const readout = page.locator(".map-toolbar-readout");
  38  |     const map = page.locator(".leaflet-container");
  39  |     const box = await map.boundingBox();
  40  |     await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  41  |     await page.mouse.down();
  42  |     await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 60, { steps: 10 });
  43  |     await page.mouse.up();
  44  |     // moveend fires -> readout's Lat/Lon (center) updates. Just assert no
  45  |     // crash and the toolbar readout is still rendering coordinates.
  46  |     await expect(readout).toContainText(/Lat: /);
  47  |   });
  48  | 
  49  |   test("layer visibility checkbox toggle hides/shows a tile layer", async ({ page }) => {
  50  |     await gotoProjectWithMap(page);
  51  |     const checkbox = page.locator(".layer-row-checkbox").first();
  52  |     await expect(checkbox).toBeChecked();
  53  |     await checkbox.uncheck();
  54  |     await expect(checkbox).not.toBeChecked();
  55  |     await checkbox.check();
  56  |     await expect(checkbox).toBeChecked();
  57  |   });
  58  | 
  59  |   test("opacity slider (gear popover) changes a layer's opacity", async ({ page }) => {
  60  |     await gotoProjectWithMap(page);
  61  |     await page.locator(".layer-row-gear").first().click();
  62  |     const slider = page.locator(".symbology-popover-opacity input[type=range]");
  63  |     await expect(slider).toBeVisible();
  64  |     await slider.fill("0.5");
  65  |     await expect(page.locator(".symbology-popover-opacity-value")).toHaveText("0.50");
  66  |   });
  67  | 
  68  |   test("measure: distance tool computes a result in meters", async ({ page }) => {
  69  |     const map = await gotoProjectWithMap(page);
  70  |     await page.getByRole("button", { name: /Measure/i }).click();
  71  |     await page.getByRole("button", { name: "Distance" }).click();
  72  |     const box = await map.boundingBox();
  73  |     await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
  74  |     await map.click({ position: { x: box.width * 0.6, y: box.height * 0.5 } });
  75  |     const result = page.locator(".measure-result");
  76  |     await expect(result).toContainText(/\d+(\.\d+)?\s*m/);
  77  |     await page.getByRole("button", { name: "Clear" }).click();
  78  |   });
  79  | 
  80  |   test("measure: area tool computes a result in hectares", async ({ page }) => {
  81  |     const map = await gotoProjectWithMap(page);
  82  |     await page.getByRole("button", { name: /Measure/i }).click();
  83  |     await page.getByRole("button", { name: "Area" }).click();
  84  |     const box = await map.boundingBox();
  85  |     await map.click({ position: { x: box.width * 0.3, y: box.height * 0.3 } });
  86  |     await map.click({ position: { x: box.width * 0.6, y: box.height * 0.3 } });
  87  |     await map.click({ position: { x: box.width * 0.45, y: box.height * 0.55 } });
  88  |     const result = page.locator(".measure-result");
  89  |     await expect(result).toContainText(/ha/);
  90  |   });
  91  | 
  92  |   test("pixel inspect (Identify): clicking the map opens a popup with a layer row", async ({ page }) => {
  93  |     const errors = collectConsoleErrors(page);
  94  |     const map = await gotoProjectWithMap(page);
  95  |     // "Identify" (inspect mode) is the default; explicitly select it since
  96  |     // the earlier measure tests in this file may leave a different mode
  97  |     // selected if run in the same worker.
  98  |     await page.getByRole("button", { name: "Identify" }).click();
  99  |     const box = await map.boundingBox();
  100 |     await map.click({ position: { x: box.width / 2, y: box.height / 2 } });
  101 |     const popup = page.locator(".pixel-popup");
  102 |     await expect(popup).toBeVisible();
  103 |     expect(errors).toEqual([]);
  104 |   });
  105 | 
  106 |   test("fullscreen toggle enters and exits fullscreen", async ({ page }) => {
  107 |     await gotoProjectWithMap(page);
```