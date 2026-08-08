import { readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// Shared helpers for the collapsible-panel-redesign regression pass.
//
// These accounts live ONLY on the ephemeral dmrv-qa compose stack (see
// ../../../deploy/docker-compose.test.yml and global-setup.js, which creates
// them fresh every run and tears the whole stack down afterward). This
// suite must never be pointed at the live deploy/docker-compose.yml stack -
// global-setup.js's hard guard refuses to run if it is.
export const ADMIN = { username: "qa_admin", password: "QaTest12345!" };
export const VIEWER = { username: "qa_viewer", password: "QaTest12345!" };
export const GIS_ASSOCIATE = { username: "qa_gis", password: "QaTest12345!" };
export const ANALYST = { username: "qa_analyst", password: "QaTest12345!" };
export const VERIFIER = { username: "qa_verifier", password: "QaTest12345!" };

// The one real project global-setup.js seeds (see seedProject there) via the
// REAL upload API + REAL ingest worker - not a mock. Named/exported so specs
// that need actual layer data (map, collapsible, redesign) can navigate to it
// by name instead of assuming row order/position in the Projects table.
export const QA_PROJECT_NAME = "QA Regression Project";

// The dmrv-qa stack's backend, exposed on a host port distinct from
// deploy/docker-compose.yml's (8080) specifically so the two can never be
// confused and both checked by global-setup.js's guard.
export const ROOT_BASE = "http://localhost:8091";
export const API_BASE = `${ROOT_BASE}/api/v1`;

// POST /auth/login is rate-limited to 5 req/min/IP on the real backend (a
// genuine brute-force protection, confirmed via backend logs - not a bug).
// global-setup.js logs in exactly once per role for the whole run and drops
// the token pairs here; every test just seeds sessionStorage from this file
// instead of hitting the real endpoint again. Only auth.spec.js's own
// login-flow tests use uiLogin() below, which goes through the real form.
// os.tmpdir(), not a Claude-session scratchpad path: this file is committed
// test infra run by any dev/CI/agent session, so it must not depend on a
// path that only exists inside one particular agent's ephemeral scratchpad
// (a prior run hardcoded one of those here - broke the very next session).
export const TOKEN_FILE = join(tmpdir(), "dmrv-qa-tokens.json");

export function readTokens(username) {
  const all = JSON.parse(readFileSync(TOKEN_FILE, "utf-8"));
  const tokens = all[username];
  if (!tokens) throw new Error(`no cached tokens for ${username} - did global-setup run?`);
  return tokens;
}

/** Authenticated navigation for tests that don't care about the login FORM
 * itself - seeds sessionStorage with the token pair global-setup fetched
 * once, then loads the app already signed in. */
export async function login(page, creds) {
  const tokens = readTokens(creds.username);
  await page.addInitScript((t) => {
    sessionStorage.setItem("dmrv.access_token", t.access_token);
    sessionStorage.setItem("dmrv.refresh_token", t.refresh_token);
  }, tokens);
  await page.goto("/");
  await page.waitForURL(/\/(?!login)/);
}

/** The real UI login form flow - only for auth.spec.js's own login tests,
 * which are specifically testing that flow and must go through it for real. */
export async function uiLogin(page, { username, password }) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/(?!login)/);
}

/**
 * Clicks the map canvas at a point guaranteed clear of every corner overlay
 * (Wave: floating map controls) - the toolbar/Layers floating panels
 * (top-left, together up to ~550px wide and, with several layer groups
 * open, taller than the 460px map frame itself), the fullscreen toggle
 * (top-right), and the coordinate badge/scale bar (bottom-right). A plain
 * hardcoded (400, 300) - what every "click once to activate scroll-zoom"
 * call in this suite used before the floating redesign - now lands ON the
 * toolbar/Layers panel whenever openMapPanels() has opened them (confirmed:
 * real Playwright run, "<div class=\"map-overlay-topleft\"> intercepts
 * pointer events"), the same way a real user's click would if they aimed at
 * that same pixel with both panels open - this isn't a bug in the app, the
 * test just needs to click somewhere those real, visible panels don't cover.
 * Right-of-center, vertically centered clears all four corners regardless of
 * which panels are open.
 */
export async function clickMapToActivate(map) {
  const box = await map.boundingBox();
  await map.click({ position: { x: box.width - 80, y: box.height / 2 } });
}

/**
 * Opens the map's two floating panels (Wave: floating map controls) - the
 * toolbar (wrench toggle) and the Layers panel (orange toggle), both
 * collapsed by default so the map gets full width/space on first load. Most
 * existing map specs were written against the old always-visible docked
 * toolbar/Layers column and just want their contents reachable; call this
 * right after navigating to a project's map view instead of updating every
 * individual test to click both toggles itself. A no-op (idempotent) if a
 * panel is already open - checks aria-expanded rather than blindly clicking,
 * so it's safe to call more than once in the same test.
 */
export async function openMapPanels(page) {
  const toolbarToggle = page.getByRole("button", { name: /Show map tools|Hide map tools/ });
  if ((await toolbarToggle.getAttribute("aria-expanded")) !== "true") await toolbarToggle.click();

  const layersToggle = page.getByRole("button", { name: /Show the Layers panel|Hide the Layers panel/ });
  if (await layersToggle.isVisible()) {
    if ((await layersToggle.getAttribute("aria-expanded")) !== "true") await layersToggle.click();
  }
}

/** Collects console "error"-level messages + uncaught page errors for the
 * duration of the test. Call once per test, inspect the returned array.
 *
 * Chrome's own synthetic "Failed to load resource: ... 404" console entries
 * (which carry no URL in msg.text(), so they can't be told apart from each
 * other here) are excluded - this app ships no favicon.ico (a pre-existing,
 * unrelated-to-this-wave gap - confirmed via curl, not a regression) which
 * triggers exactly this on every single page load, and would otherwise mask
 * a real console error underneath. Use collectUnexpectedResponseErrors
 * alongside this where a precise per-URL 404 check matters (e.g. map tiles,
 * which legitimately 404 at a layer's bounding-box edge - see
 * ProjectMap.jsx's handleBatchSettled docstring). */
export function collectConsoleErrors(page) {
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    if (/^Failed to load resource:/.test(msg.text())) return;
    errors.push(msg.text());
  });
  page.on("pageerror", (err) => errors.push(String(err)));
  return errors;
}

/** Precise, URL-aware 404/5xx tracking - allowlists the known-benign
 * favicon.ico 404 and raster-tile-at-bbox-edge 404s (both explained above),
 * flags everything else. */
export function collectUnexpectedResponseErrors(page) {
  const errors = [];
  page.on("response", (res) => {
    if (res.status() < 400) return;
    const url = res.url();
    if (url.endsWith("/favicon.ico")) return;
    if (/\/tiles\/.*\.png/.test(url)) return;
    errors.push(`${res.status()} ${url}`);
  });
  return errors;
}
