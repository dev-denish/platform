import { execFileSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const COMPOSE_FILE = resolve(__dirname, "../../../deploy/docker-compose.test.yml");
const COMPOSE_PROJECT = "dmrv-qa";

// Tears the ephemeral stack all the way down, including its tmpfs-backed
// containers - nothing from a test run is meant to survive to the next one.
// If this throws, don't let it mask the actual test results (Playwright
// treats a globalTeardown throw as a run failure) - log and move on.
export default async function globalTeardown() {
  try {
    execFileSync("docker", ["compose", "-p", COMPOSE_PROJECT, "-f", COMPOSE_FILE, "down", "-v"], {
      stdio: "inherit",
    });
  } catch (err) {
    console.error(`global-teardown: failed to tear down the ${COMPOSE_PROJECT} stack - remove it manually:`, err);
  }
}
