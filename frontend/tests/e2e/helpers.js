import { readFileSync } from "fs";

// Shared helpers for the collapsible-panel-redesign regression pass.
//
// These accounts live ONLY on the ephemeral dmrv-qa compose stack (see
// ../../../deploy/docker-compose.test.yml and global-setup.js, which creates
// them fresh every run and tears the whole stack down afterward). This
// suite must never be pointed at the live deploy/docker-compose.yml stack -
// global-setup.js's hard guard refuses to run if it is.
export const ADMIN = { username: "qa_admin", password: "QaTest12345!" };
export const VIEWER = { username: "qa_viewer", password: "QaTest12345!" };

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
export const TOKEN_FILE =
  "/tmp/claude-1000/-home-denish-dmrv-1-platform/8338ce9e-6b69-4e08-ae35-3eab3ccbc29e/scratchpad/dmrv-qa-tokens.json";

function readTokens(username) {
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
