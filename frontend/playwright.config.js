import { defineConfig, devices } from "@playwright/test";

// E2E regression suite. Targets ONLY the ephemeral dmrv-qa stack (see
// ../deploy/docker-compose.test.yml) - global-setup.js spins it up and
// hard-guards that it's actually that stack (not the live deploy one)
// before anything runs, and global-teardown.js tears it down after. The
// dev server below gets its API target from VITE_API_BASE, an absolute
// URL to the ephemeral backend - no vite.config.js changes needed, and
// none should ever be made for this again (see incident notes in
// global-setup.js's guard).
export default defineConfig({
  testDir: "./tests/e2e",
  globalSetup: "./tests/e2e/global-setup.js",
  globalTeardown: "./tests/e2e/global-teardown.js",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  webServer: {
    command: "npm run dev -- --port 5173",
    url: "http://localhost:5173",
    reuseExistingServer: false,
    env: {
      VITE_API_BASE: "http://localhost:8091/api/v1",
    },
  },
  use: {
    baseURL: "http://localhost:5173",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // The default headless-shell build in this sandbox is missing
        // libnspr4.so; the full chromium build (already downloaded, no
        // missing deps) works fine headless too.
        launchOptions: {
          executablePath: `${process.env.HOME}/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`,
        },
      },
    },
  ],
});
