# Migration Guide — MVP → Phase-1 Platform

This is a **new, hardened backend** that preserves the MVP's behaviour and data model
(extended, not replaced). Because the MVP created its schema with `CREATE TABLE IF NOT
EXISTS` and had no migration history, moving existing data forward is an explicit,
one-time step.

## 1. Configuration (breaking: env var names)

All configuration is now prefixed `DMRV_` and validated on boot. Map old → new:

| MVP                | Phase-1                                   |
|--------------------|-------------------------------------------|
| `JWT_SECRET`       | `DMRV_JWT_SECRET` (must be ≥32 random chars in staging/prod) |
| DB creds in code   | `DMRV_DB_HOST/PORT/USER/PASSWORD/NAME`     |
| (implicit `*`)     | `DMRV_CORS_ALLOW_ORIGINS` (explicit allow-list) |
| local disk         | `DMRV_STORAGE_BACKEND=local\|s3` (+ `DMRV_S3_BUCKET`) |

Generate secrets:
```bash
openssl rand -base64 48   # DMRV_JWT_SECRET
openssl rand -base64 24   # DMRV_DB_PASSWORD
```
In `dev`/`test` a strong ephemeral JWT secret is generated automatically if unset.

## 2. Ports & endpoints

- The API listens on **8001** (the MVP README incorrectly said 8000).
- All API routes are now under **`/api/v1`** (e.g. `POST /api/v1/auth/login`,
  `GET /api/v1/projects`). Health probes are at the root: `/livez`, `/readyz`,
  `/healthz`.
- Login now accepts **JSON** (`{"username","password"}`) and returns
  `{access_token, refresh_token, expires_in}`. Upload remains `multipart/form-data`.

## 3. Database schema

Apply the baseline migration (it creates PostGIS + uuid-ossp extensions and all
tables/indexes/constraints):

```bash
cd platform/backend
alembic upgrade head        # env reads DMRV_DB_* from the environment
```

If you have **existing MVP data** to preserve, run the migration against a fresh
database and copy rows over, mapping to the new columns:
- `project`: now has `status`, `version`, `created_at/updated_at`, `deleted_at`.
- `dataset`: `loaded_at` is `TIMESTAMPTZ`; `batch_id` is required; soft-delete via
  `deleted_at`.
- `kpi`: **`UNIQUE(dataset_id, metric_name)`** is now enforced — de-duplicate any
  historical duplicate KPI rows before import (keep the latest `computed_at`), or the
  copy will conflict. This is deliberate: those duplicates were the cause of the
  `/summary` double-count.
- `spatial_layer`: file references are now storage **keys** (`file_key`,
  `preview_key`), not absolute local paths — re-point them at your storage backend.
- `audit_log`: now requires `actor_name`; historical rows can be backfilled with a
  sentinel like `"legacy-import"` (new rows capture the real user).

For a greenfield deployment, no data migration is needed — `alembic upgrade head`
plus demo data is enough.

## 4. Demo / seed data

The MVP README referenced a `seed.py` that did not exist; the real demo path was
`load_demo_data.py`. In Phase-1, seeding is a thin script that calls the **same**
`IngestionService` the API uses (single code path — no divergent duplicate of the
raster logic, which the MVP had). Create your admin user and ingest a sample raster
through the service or the `POST /api/v1/datasets/upload` endpoint.

## 5. Storage

- **Local (dev):** rasters/previews live under `DMRV_LOCAL_DATA_DIR`; previews served
  via the dev-only `/previews` mount.
- **Cloud:** set `DMRV_STORAGE_BACKEND=s3` and `DMRV_S3_BUCKET`; previews are served via
  short-lived presigned URLs and rasters are processed in place via GDAL `/vsis3/`.
  No code change — it is a config switch.

## 6. Frontend

- Replace the four hardcoded `API_BASE = "http://localhost:8001"` constants with
  imports from `src/config.js` (`import { API_BASE, apiFetch } from "./config"`).
- Set `VITE_API_BASE` at build time (defaults to same-origin `/api/v1`).
- Build the production image (`frontend/Dockerfile`) instead of running `npm run dev`.

## 7. Rollback

`alembic downgrade -1` drops the Phase-1 schema. Because this is an additive baseline,
rollback is clean on a greenfield database; on a migrated database, back up first.
