# Phase 1 — Changelog & Rationale

This documents what changed versus the original MVP and *why*, following the
review structure: existing implementation → why insufficient → production risk →
enterprise solution. Each item maps to a finding from the engineering review.

Every change preserves existing functionality: all MVP endpoints (login, projects,
project detail, KPIs, layers, portfolio summary, upload) remain, with identical
meaning, now typed, versioned (`/api/v1`), and hardened.

---

## P1 — Critical (correctness / security / availability)

### 1. Frontend shipped the Vite dev server as production
- **Was:** the frontend image's `CMD` was `npm run dev`.
- **Risk:** the dev server is single-connection, unoptimised, ships source maps and
  the full module graph, and is a live filesystem watcher — a performance and
  security liability in front of users.
- **Now:** multi-stage build compiling optimised, hashed, minified assets served by
  a **non-root nginx** image with gzip, immutable asset caching, SPA history
  fallback, security headers, and an API reverse-proxy (`frontend/Dockerfile`,
  `frontend/nginx.conf`).

### 2. Working default secrets
- **Was:** `JWT_SECRET` defaulted to `"dev-only-secret-change-in-production"`; the DB
  password `dmrv_dev_pw` was hardcoded in code and compose.
- **Risk:** one missed environment variable = full auth bypass with a publicly known
  signing key.
- **Now:** typed `Settings` (`app/core/config.py`) with **fail-fast validation** —
  production-like environments refuse to start with a placeholder/short JWT secret,
  a placeholder DB password, wildcard CORS, local storage, or debug on. Dev
  generates a strong ephemeral per-process secret so nothing hardcoded ever ships.
  Verified by `tests/unit/test_config.py`.

### 3. Blocking ingestion on the async event loop
- **Was:** `async def upload_dataset` called the synchronous, CPU/IO-heavy
  `ingest_raster` inline.
- **Risk:** one upload froze the event loop for *every* concurrent user for the whole
  ingest.
- **Now:** a `TaskRunner` seam (`app/workers/queue.py`); the upload endpoint offloads
  ingestion to a bounded worker pool via `run_in_executor`, keeping the loop
  responsive and capping concurrency. The same interface swaps to a distributed
  queue (arq/Celery) later without touching call sites.

### 4. Whole-raster reads into memory
- **Was:** `src.read(1)` pulled the entire band into a NumPy array, several times per
  ingest.
- **Risk:** a full-resolution Sentinel-2 tile is hundreds of MB to GBs in memory;
  reading it whole OOM-kills the worker. Memory was unbounded in raster size.
- **Now:** **windowed reads** (fixed-size tiles) so peak memory is O(window²),
  independent of total size (`app/services/ingestion/raster.py`). Previews are
  decimated on read; reprojection streams tile-by-tile. Verified numerically by
  `tests/unit/test_raster_stats.py`.

### 5. Area measured from EPSG:4326 pixel counts  *(domain-critical)*
- **Was:** hectares computed from pixel counts on the reprojected 4326 (degrees) grid.
- **Risk:** a 4326 "pixel" does not cover constant ground area — it shrinks toward the
  poles — so area (which drives carbon-credit volume) was systematically wrong, with
  error growing by latitude.
- **Now:** area is measured on a **projected, equal-area grid in metres** — the native
  grid when the source is already projected in metres (exact, the Sentinel-2/UTM
  case), otherwise a lazy WarpedVRT reprojection to EPSG:6933. Pixel ground area comes
  from the raster's affine transform, never a user-typed field. The measurement CRS
  is recorded in the audit trail for provenance.

### 6. JWT in `localStorage`; wildcard CORS
- **Was:** token in `localStorage` (readable by any XSS); `allow_origins=["*"]`.
- **Now:** CORS restricted to a configured allow-list; the frontend fetch wrapper is
  centralised (`frontend/src/config.js`) and moved to `sessionStorage` as an interim
  step, with an httpOnly-cookie end state documented and localised to one module. JWT
  now uses **short-lived access + long-lived refresh** tokens with typed claims,
  issuer/audience validation, and a revocation table.

### 7. Zero tests
- **Was:** no tests.
- **Now:** 31 passing tests — unit (raster, security, config, pagination),
  API-contract (routing, auth, RBAC, error mapping, pagination — no DB), and
  DB-backed repository integration tests that run against PostGIS in CI.

### 8. No migrations
- **Was:** a raw `schema.sql` with `CREATE TABLE IF NOT EXISTS`, no history, no
  rollback.
- **Now:** Alembic baseline migration (`migrations/versions/0001_initial.py`) with
  full up/down, sourced from the same typed settings as the app.

### 9. README described a project that didn't run
- **Was:** referenced a nonexistent `seed.py`, the wrong port (8000 vs 8001), "auth:
  None" when auth existed, and wrong class counts.
- **Now:** accurate `README.md` + `docs/MIGRATION.md`; the demo-data path is named
  correctly and the compose/quickstart match the actual ports.

---

## P2 — Scale & correctness

- **No indexes** → added indexes on every FK/join/order column, a partial unique index
  on `lower(name)`, a GIST index on the layer extent (migration).
- **New DB connection per request, leaked on error paths** → a process-wide psycopg3
  `ConnectionPool` with transaction context managers that always return connections
  (`app/core/db.py`).
- **KPI duplication on re-ingest → `/summary` double-counted** → `UNIQUE(dataset_id,
  metric_name)` + UPSERT semantics (`app/repositories/datasets.py`).
- **find-or-create project race** → atomic `ON CONFLICT (lower(name))`.
- **No pagination** → every list endpoint returns a `Page[T]` envelope with bounded
  limit/offset.
- **Non-attributable audit log** ("api_upload"/"seed_script") → every entry records the
  real actor id + name + target + request id (`app/repositories/audit.py`).
- **Naive TIMESTAMP** → `TIMESTAMPTZ` throughout.
- **No rate limiting** → per-route limits on login (5/min) and upload (20/hr); health
  probes are never throttled (`app/core/ratelimit.py`).
- **Leaking internals in errors** → a domain-exception hierarchy mapped by global
  handlers to clean `{error:{code,message}}` responses; full detail goes to structured
  logs only.
- **Local-disk storage breaks multi-replica** → a `Storage` abstraction with local and
  S3 backends behind one interface (`app/services/ingestion/storage.py`).

---

## Cross-cutting additions

Structured JSON logging with request/correlation IDs (`app/core/logging.py`,
`app/core/middleware.py`); graceful startup/shutdown via lifespan (drains cleanly on
SIGTERM); `/livez` `/readyz` `/healthz` probes; OpenAPI docs; multi-stage non-root
Dockerfiles; hardened docker-compose (secrets via `.env`, healthchecks, one-shot
migrations); CI with lint, type-check, tests against PostGIS, image builds, Trivy
image scanning, and pip-audit.
