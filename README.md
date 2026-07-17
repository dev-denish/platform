# dMRV Analytical Platform

Enterprise digital Measurement, Reporting & Verification (dMRV) platform for
land-use / land-cover, forestry, and carbon-project monitoring from satellite
rasters.

This repository is the **Phase-1 foundation**: a well-factored *modular monolith*
backend with hard internal seams (repository → service → API), a verified
memory-bounded raster pipeline, a hardened database schema with migrations, and the
CI/containerisation needed to build on it safely. See `docs/ROADMAP.md` for how the
remaining platform (additional services, the enterprise UI, carbon-accounting
engine, Kubernetes/Terraform, and the broader dMRV feature set) sequences on top of
this base, and `docs/CHANGELOG_PHASE1.md` for exactly what changed versus the
original MVP and why.

## Repository layout

```
platform/
├── backend/                 FastAPI modular monolith
│   ├── app/
│   │   ├── core/            config, db pool, security, logging, errors, middleware, rate limit
│   │   ├── domain/          enums + DTOs (typed request/response contracts)
│   │   ├── repositories/    parameterised SQL, isolated from business logic
│   │   ├── services/        orchestration (auth, projects, ingestion/*)
│   │   ├── api/v1/          versioned routers + DI
│   │   ├── workers/         background-task abstraction (threadpool now, queue later)
│   │   ├── main.py          app factory (lifespan, middleware, handlers)
│   │   └── asgi.py          production server entrypoint
│   ├── migrations/          Alembic (versioned schema)
│   ├── tests/               unit + API-contract (+ DB integration, CI)
│   ├── Dockerfile           multi-stage, non-root
│   └── pyproject.toml
├── frontend/                production nginx build + central API config (UI rebuild is a later phase)
├── deploy/                  hardened docker-compose + .env.example
├── .github/workflows/       CI (lint, type, test, image build, Trivy, pip-audit)
└── docs/                    changelog, migration, verification, roadmap
```

## Quickstart (local, docker-compose)

```bash
cd platform/deploy
cp .env.example .env
# generate real secrets (the stack refuses to start without them):
#   openssl rand -base64 48   -> DMRV_JWT_SECRET
#   openssl rand -base64 24   -> DMRV_DB_PASSWORD
docker compose up --build
# frontend  -> http://localhost:8080
# API docs  -> http://localhost:8080/api/v1/openapi.json  (or backend :8001/docs)
```

The `migrate` service applies Alembic migrations before the API serves traffic; the
backend waits on the database healthcheck.

## Backend development (without Docker)

```bash
cd platform/backend
pip install -e ".[dev]"
# unit + API-contract tests (no database needed):
DMRV_ENVIRONMENT=test pytest -q
# lint + types:
ruff check . && mypy app
```

DB-backed integration tests run when `DMRV_TEST_DATABASE=1` and a PostGIS instance is
configured (this is what CI does).

## Verified in this phase

Reproduce with the commands in `docs/VERIFICATION.md`:
- windowed raster stats are numerically identical to a whole-array computation;
- areas are measured on a projected/equal-area grid (not EPSG:4326 degrees);
- the app builds with all routes wired;
- the Alembic migration renders valid SQL;
- 31 tests pass; lint is clean.
