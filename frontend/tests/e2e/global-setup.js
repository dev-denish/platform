import { request } from "@playwright/test";
import { execFileSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";
import { writeFileSync } from "fs";
import { ADMIN, VIEWER, TOKEN_FILE, API_BASE, ROOT_BASE } from "./helpers.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const COMPOSE_FILE = resolve(__dirname, "../../../deploy/docker-compose.test.yml");
const COMPOSE_PROJECT = "dmrv-qa";

// Ports deploy/docker-compose.yml's live stack actually exposes (frontend's
// nginx on 8080). Checked in addition to the /livez environment check below
// as a second, independent signal - belt and suspenders, not a substitute
// for it. If this suite is ever repointed, both checks have to agree it's
// safe before anything destructive runs.
const LIVE_STACK_PORTS = new Set([8080]);

function composeUp() {
  execFileSync(
    "docker",
    ["compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE, "up", "-d", "--build", "--wait", "--wait-timeout", "180"],
    { stdio: "inherit" }
  );
}

/**
 * The hard guard. A prior QA run pointed this exact suite's bulk-delete test
 * at the live deploy stack and permanently deleted the real admin account -
 * this function is what makes that impossible going forward. It refuses to
 * proceed (which refuses the WHOLE run, including every destructive test -
 * global-setup gates all of them) unless the target backend both (a) isn't
 * on a port the live stack uses, and (b) self-reports DMRV_ENVIRONMENT=test
 * via its own real, validated config (see backend app/core/config.py's
 * `Environment` literal and GET /livez) - not a guess from this side, an
 * assertion from the target itself.
 */
async function assertTargetIsEphemeralTestStack(ctx) {
  const port = Number(new URL(ROOT_BASE).port);
  if (LIVE_STACK_PORTS.has(port)) {
    throw new Error(
      `Refusing to run: ROOT_BASE (${ROOT_BASE}) uses port ${port}, which the live deploy stack ` +
        `exposes. This suite must only ever target the ephemeral dmrv-qa stack (deploy/docker-compose.test.yml).`
    );
  }
  const res = await ctx.get(`${ROOT_BASE}/livez`);
  if (!res.ok()) {
    throw new Error(
      `Refusing to run: GET ${ROOT_BASE}/livez returned ${res.status()}. Expected the ephemeral ` +
        `dmrv-qa test stack to be reachable and healthy before running any test.`
    );
  }
  const body = await res.json();
  if (body.environment !== "test") {
    throw new Error(
      `Refusing to run destructive E2E tests: ${ROOT_BASE} reports environment="${body.environment}", ` +
        `expected "test". A prior run of this suite pointed at the live stack and permanently deleted ` +
        `the real admin account - this check exists so that can never happen again. Only run this suite ` +
        `against deploy/docker-compose.test.yml's stack, never deploy/docker-compose.yml's.`
    );
  }
}

function createAccount({ username, password, role }) {
  execFileSync(
    "docker",
    [
      "compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE, "exec", "-T", "test-backend",
      "python", "-m", "scripts.create_admin",
      "--username", username, "--password", password, "--role", role,
    ],
    { stdio: "inherit" }
  );
}

export default async function globalSetup() {
  composeUp();

  const ctx = await request.newContext();
  await assertTargetIsEphemeralTestStack(ctx);

  createAccount({ ...ADMIN, role: "Administrator" });
  createAccount({ ...VIEWER, role: "Viewer" });

  const tokens = {};
  for (const creds of [ADMIN, VIEWER]) {
    const res = await ctx.post(`${API_BASE}/auth/login`, { data: creds });
    if (!res.ok()) {
      throw new Error(`global-setup login as ${creds.username} failed: ${res.status()} ${await res.text()}`);
    }
    tokens[creds.username] = await res.json();
  }
  await ctx.dispose();
  writeFileSync(TOKEN_FILE, JSON.stringify(tokens));
}
