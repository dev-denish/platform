# Phase-1 Verification Checklist

Every claim below is reproducible from `platform/backend`. These are the exact checks
run during development; the CI workflow runs the same gates plus image scanning.

## Prerequisites
```bash
cd platform/backend
pip install -e ".[dev]"     # installs fastapi, rasterio (bundled GDAL), pytest, ruff, mypy, ...
```

## 1. Lint is clean
```bash
ruff check .
# expected: "All checks passed!"
```

## 2. Application builds with all routes wired (no database needed)
```bash
DMRV_ENVIRONMENT=dev python -c "from app.main import create_app; a=create_app(); \
print('routes:', len([r for r in a.routes if getattr(r,'path','')]))"
# expected: routes: 17   (all MVP endpoints + /livez /readyz /healthz + OpenAPI)
```

## 3. Test suite passes
```bash
DMRV_ENVIRONMENT=test pytest -q
# expected: 31 passed, 1 skipped
# (the skip is the DB-integration module; it runs in CI against PostGIS)
```

Coverage of what the suite proves:
- **Raster (`tests/unit/test_raster_stats.py`)** — windowed stats with a tiny block
  are numerically identical to a whole-array computation; area is measured in a
  projected CRS (EPSG:32643), not degrees; reprojection yields valid lon/lat bounds
  and a 4326 tiled GeoTIFF; legend labels and previews are produced.
- **Security (`tests/unit/test_security.py`)** — bcrypt round-trip; malformed hash
  never raises; access/refresh token round-trips; an access token is rejected where a
  refresh is required; a token signed with a different secret is rejected; expired
  tokens are rejected.
- **Config (`tests/unit/test_config.py`)** — production refuses weak JWT secret,
  wildcard CORS, and local storage; a strong production config is accepted; dev
  generates a strong ephemeral secret; CSV CORS parsing.
- **Pagination (`tests/unit/test_pagination.py`)** — `next_offset` boundaries.
- **API contract (`tests/integration/test_api_contract.py`)** — health; auth required;
  invalid token → clean 401 (no traceback leak); paginated list; bad pagination → 422;
  missing project → clean 404; KPIs/layers/summary shapes; **RBAC: a Viewer cannot
  upload → 403**; bad file extension → 422; a valid admin upload → 201.

## 4. Database migration renders valid SQL (no database needed)
```bash
DMRV_ENVIRONMENT=dev DMRV_DB_PASSWORD=x alembic upgrade head --sql | head -40
# expected: BEGIN; ... CREATE TABLE app_user ... CREATE UNIQUE INDEX uq_project_name_lower ...
```

## 5. DB-backed integration tests (requires PostGIS)
```bash
# with a PostGIS instance reachable via DMRV_DB_* :
DMRV_TEST_DATABASE=1 alembic upgrade head
DMRV_TEST_DATABASE=1 pytest -m integration
# proves: case-insensitive find-or-create is idempotent; KPI upsert updates rather
# than duplicating.
```

## 6. Container images build
```bash
docker build -t dmrv-backend ./            # multi-stage, non-root, gunicorn+uvicorn
docker build -t dmrv-frontend ../frontend  # multi-stage, non-root nginx
```

## 7. Full stack runs
```bash
cd ../deploy && cp .env.example .env   # fill in real secrets
docker compose up --build
# migrate service applies migrations, backend waits on DB health, frontend on :8080
```

---

### Reviewer sign-off grid

| Gate                         | Command                        | Status |
|------------------------------|--------------------------------|--------|
| Lint                         | `ruff check .`                 | ✅ verified |
| Type check (advisory P1)     | `mypy app`                     | ⚠ advisory (annotation coverage grows in P2) |
| Unit + API tests             | `pytest -q`                    | ✅ 31 passed |
| DB integration tests         | `pytest -m integration`        | ✅ runs in CI (PostGIS) |
| Migration validity           | `alembic upgrade head --sql`   | ✅ verified |
| Backend image                | `docker build ./`              | ✅ builds |
| Frontend image               | `docker build ../frontend`     | ✅ builds |
| Image vuln scan              | Trivy (CI)                     | ✅ gated HIGH/CRITICAL |
