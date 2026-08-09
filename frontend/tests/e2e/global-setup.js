import { request } from "@playwright/test";
import { execFileSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";
import { readFileSync, writeFileSync } from "fs";
import {
  ADMIN, VIEWER, GIS_ASSOCIATE, ANALYST, VERIFIER, QA_PROJECT_NAME, TOKEN_FILE, API_BASE, ROOT_BASE,
} from "./helpers.js";

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

// forms.spec.js's Users-pagination test needs total > UsersPage.jsx's own
// client-side LIMIT (50) - Pagination.jsx renders nothing at all otherwise
// (`if (total <= limit) return null`). A fresh ephemeral DB starts with only
// the 5 role accounts above, nowhere near enough. One process, one hashed
// password reused for all N (bcrypt is deliberately slow - hashing it once
// instead of N times is what keeps this fast), same UserRepository.upsert
// scripts/create_admin.py itself uses - not a second definition of "how a
// user is stored."
//
// Username prefix "zz_bulk_" (not "qa_bulk_") is deliberate: GET /users
// sorts ORDER BY username ascending (app/repositories/users.py), and other
// specs (forms.spec.js's create/double-click-create tests) assume a
// freshly-created "qa_..." user shows up on the unpaginated FIRST page right
// after creation. "zz_" sorts after every "qa_*" username, so these 55
// filler accounts land on page 2+ and never bump a real test's own new user
// off page 1 - confirmed this actually happens with a "qa_bulk_" prefix
// instead (alphabetically interleaves with "qa_dbl_"/"qa_form_", pushing them
// to page 2 and failing forms.spec.js's double-click-create assertion).
function seedManyUsers(count) {
  const script = `
import sys
sys.path.insert(0, ".")
from app.core.config import get_settings
from app.core.db import Database
from app.core.security import hash_password
from app.repositories.users import UserRepository

settings = get_settings()
db = Database(settings)
db.connect()
pw_hash = hash_password("QaTest12345!")
try:
    with db.transaction() as cur:
        repo = UserRepository(cur)
        for i in range(${count}):
            repo.upsert(username=f"zz_bulk_{i:03d}", password_hash=pw_hash, role="Viewer")
finally:
    db.close()
print("OK: seeded ${count} bulk users")
`;
  execFileSync(
    "docker",
    ["compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE, "exec", "-T", "test-backend", "python", "-c", script],
    { stdio: "inherit" }
  );
}

const FIXTURES_DIR = resolve(__dirname, "fixtures");

/** One multipart POST /datasets/upload + poll GET /jobs/{id} to a terminal
 * status - the REAL ingest pipeline (test-worker consuming the REAL arq
 * queue), not a stub. Throws if the job doesn't succeed, so a broken seed
 * fails loudly in global-setup rather than quietly starving every later
 * spec of the project/layers it expects. */
async function uploadAndWait(accessToken, { file, fields }) {
  const bytes = readFileSync(resolve(FIXTURES_DIR, file));
  const form = new FormData();
  form.set("file", new Blob([bytes]), file);
  for (const [k, v] of Object.entries(fields)) form.set(k, String(v));

  const uploadRes = await fetch(`${API_BASE}/datasets/upload`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: form,
  });
  if (!uploadRes.ok) {
    throw new Error(`seed upload of ${file} failed: ${uploadRes.status} ${await uploadRes.text()}`);
  }
  const { job_id } = await uploadRes.json();

  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const jobRes = await fetch(`${API_BASE}/jobs/${job_id}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    const job = await jobRes.json();
    if (job.status === "succeeded") return job.result;
    if (["failed", "dead_letter"].includes(job.status)) {
      throw new Error(`seed ingest of ${file} ended in ${job.status}: ${JSON.stringify(job.error)}`);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`seed ingest of ${file} did not reach a terminal status within 60s (job ${job_id})`);
}

/**
 * Seeds exactly one real project (QA_PROJECT_NAME) through the REAL upload
 * API and REAL ingest worker (test-worker, added to docker-compose.test.yml
 * for this regression pass - see its own comment) - not a DB fixture, not a
 * mock. Every map/collapsible/redesign spec that needs an actual project
 * with real layers reads THIS one project, so it only has to be built once
 * per run instead of every spec improvising its own upload.
 *
 * Four layers, deliberately shaped for what the redesign regression pass
 * needs:
 *   - a 3-class LULC raster dated 2023-06-01 (older)
 *   - a 9-class LULC raster dated 2024-06-01 (newer) - the 9-class legend
 *     specifically for the symbology popover's scroll-behavior test, and a
 *     SECOND classified layer (differently classified from the first) so
 *     the Dashboard's dataset selector can be checked for cross-contamination
 *     between the two (both share a "Forest" class, so Forest cover trend
 *     has 2 real dated points too)
 *   - an unclassified raw-imagery raster (Raw imagery group, no legend)
 *   - a small 2-feature GeoJSON boundary (Vector layers group, feature count)
 */
async function seedProject(adminAccessToken) {
  const common = {
    project_name: QA_PROJECT_NAME,
    region: "Karnataka",
    pixel_size_m: "10",
  };

  const layerAResult = await uploadAndWait(adminAccessToken, {
    file: "qa-lulc-3class-2023.tif",
    fields: {
      ...common,
      dataset_type: "LULC",
      source: "QA seed A (3-class)",
      accuracy_score: "92.5",
      date_processed: "2023-06-01",
      // Wave: upload project-name footgun fix. QA_PROJECT_NAME doesn't exist
      // yet on a fresh stack - POST /datasets/upload now REJECTS a
      // non-matching project_name unless the caller explicitly confirms this
      // is a new project. The 3 uploads below deliberately do NOT set this -
      // they attach to the SAME already-created project by exact name match,
      // proving the common "re-upload to an existing project" case still
      // works with no confirmation needed.
      create_new_project: "true",
      class_legend: JSON.stringify({
        1: { label: "Forest", color: "#228b22" },
        2: { label: "Water", color: "#4682b4" },
        3: { label: "Urban/Built-up", color: "#b22222" },
      }),
    },
  });

  await uploadAndWait(adminAccessToken, {
    file: "qa-lulc-9class-2024.tif",
    fields: {
      ...common,
      dataset_type: "LULC",
      source: "QA seed B (9-class)",
      accuracy_score: "88.0",
      date_processed: "2024-06-01",
      class_legend: JSON.stringify({
        1: { label: "Forest", color: "#228b22" },
        2: { label: "Water", color: "#4682b4" },
        3: { label: "Cropland/Agriculture", color: "#50aa2a" },
        4: { label: "Urban/Built-up", color: "#b22222" },
        5: { label: "Grassland", color: "#7fff00" },
        6: { label: "Barren/Bare soil", color: "#d2b48c" },
        7: { label: "Wetland", color: "#4682b4" },
        8: { label: "Snow/Ice", color: "#ffffff" },
        9: { label: "Shrubland", color: "#ee82ee" },
      }),
    },
  });

  await uploadAndWait(adminAccessToken, {
    file: "qa-raw-imagery.tif",
    fields: {
      ...common,
      dataset_type: "Satellite / Raw Imagery",
      source: "QA seed C (raw)",
      date_processed: "2024-06-01",
    },
  });

  await uploadAndWait(adminAccessToken, {
    file: "qa-boundary.geojson",
    fields: {
      ...common,
      dataset_type: "Boundary",
      source: "QA seed vector",
      date_processed: "2024-06-01",
    },
  });

  // Project-level RBAC (Wave: project-level RBAC): GET /projects only ever
  // returns projects a non-Administrator has a live membership row on
  // (ProjectService.list_projects) - qa_gis needs to actually be a member
  // here, or every redesign.spec.js test exercising a non-Administrator
  // role against this project (e.g. the layer-rename field's role gating)
  // can't even find it in the list, let alone open it.
  const memberRes = await fetch(`${API_BASE}/projects/${layerAResult.project_id}/members`, {
    method: "POST",
    headers: { Authorization: `Bearer ${adminAccessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ username: GIS_ASSOCIATE.username, role: "GIS Associate" }),
  });
  if (!memberRes.ok) {
    throw new Error(`seed: adding ${GIS_ASSOCIATE.username} to the QA project failed: ${memberRes.status} ${await memberRes.text()}`);
  }
}

export default async function globalSetup() {
  composeUp();

  const ctx = await request.newContext();
  await assertTargetIsEphemeralTestStack(ctx);

  createAccount({ ...ADMIN, role: "Administrator" });
  createAccount({ ...VIEWER, role: "Viewer" });
  createAccount({ ...GIS_ASSOCIATE, role: "GIS Associate" });
  createAccount({ ...ANALYST, role: "Analyst" });
  createAccount({ ...VERIFIER, role: "Verifier" });
  seedManyUsers(55);

  const tokens = {};
  for (const creds of [ADMIN, VIEWER, GIS_ASSOCIATE, ANALYST, VERIFIER]) {
    const res = await ctx.post(`${API_BASE}/auth/login`, { data: creds });
    if (!res.ok()) {
      throw new Error(`global-setup login as ${creds.username} failed: ${res.status()} ${await res.text()}`);
    }
    tokens[creds.username] = await res.json();
  }
  await ctx.dispose();
  writeFileSync(TOKEN_FILE, JSON.stringify(tokens));

  await seedProject(tokens[ADMIN.username].access_token);

  // The 5 real /auth/login calls just above (one per seeded role) share
  // /auth/login's own 5/minute-per-IP limiter (app/api/v1/auth.py) with every
  // spec in this run - auth.spec.js's real-login tests use the SAME IP and
  // can land in the SAME fixed one-minute window as these, 429ing through no
  // fault of their own (confirmed: adding the 3 new role logins above pushed
  // a clean run straight into "Too many requests." on auth.spec.js's first
  // real login). All the ingest polling above the arq queue is fully drained
  // by now, so it's safe to clear the limiter's own counters (Redis, per
  // core/ratelimit.py's storage_uri) without dropping anything a later test
  // still needs - this only resets request COUNTS, not any real data.
  execFileSync(
    "docker",
    ["compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE, "exec", "-T", "test-redis", "redis-cli", "FLUSHALL"],
    { stdio: "inherit" }
  );
}
