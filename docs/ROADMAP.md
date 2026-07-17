# dMRV Platform — Delivery Roadmap

This maps the full target platform (the services and ~150 dMRV features in the brief)
onto a sequence of phases that build on the Phase-1 foundation. It is written to be
read by an architecture review board: every service extraction has a **trigger** (the
condition that justifies splitting it out), and every phase has a clear goal and exit
criteria.

## Architecture principle: modular monolith → extract along seams

Phase-1 is a modular monolith with hard internal boundaries (repository → service →
API, domain isolated from transport, a `TaskRunner` seam for background work, a
`Storage` seam for objects). We do **not** start with 15 microservices, because at
pre-scale that multiplies operational surface (15 pipelines, 15 dashboards,
distributed tracing everywhere, network failure modes) before there is load or team
to justify it — the classic distributed-monolith trap.

Instead, each component is extracted into its own service **only when a concrete
trigger fires**:
- **Independent scaling** — one component's resource profile diverges sharply (raster
  processing is CPU/RAM-bound and bursty; the API is IO-bound).
- **Independent failure isolation** — a component must not take the API down (heavy
  batch analytics).
- **Independent deploy cadence / ownership** — a separate team ships it on its own
  clock.
- **Specialised runtime** — GPU, GDAL-heavy workers, tile servers.

Because the seams already exist, extraction is mechanical, not a rewrite.

---

## Phase 1 — Foundation ✅ (delivered & verified)

Backend modular monolith; verified memory-bounded raster pipeline with correct
equal-area measurement; config/security/DB/logging/error layers; repository + service
+ DTO layers; versioned API; pagination; RBAC; rate limiting; attributable audit;
Alembic migrations; structured logging + request IDs; health/readiness/liveness;
multi-stage non-root images; production frontend serving; hardened compose; CI with
lint/type/test/scan. **31 tests pass.** See `CHANGELOG_PHASE1.md`.

**Exit criteria (met):** green tests, clean lint, app builds, migration renders,
images build.

---

## Phase 2 — Asynchronous ingestion, jobs, and observability

**Goal:** make ingestion fully asynchronous and observable end-to-end; this is the
prerequisite for "millions of observations."

- Replace the in-process `ThreadPoolTaskRunner` with a **distributed queue** (arq or
  Celery on Redis/SQS) behind the *same* `TaskRunner` interface. Upload returns
  **202 + a job id**; add `GET /api/v1/jobs/{id}` for status/polling and a webhook on
  completion.
- **Processing Queue / Job Monitoring / Processing History** feature epics.
- **OpenTelemetry** tracing (the request-id middleware is the hook point), **Prometheus**
  metrics endpoint, **Grafana** dashboards, alerting rules.
- Idempotency keys on upload; dead-letter handling; retry with backoff + circuit
  breaker around external calls.
- Pin a dependency lockfile; make `mypy` and `pip-audit` gating (not advisory).

**Service extraction:** none yet — the worker runs as a *separate deployment of the
same image* (`gunicorn` for API, `arq worker` for jobs). First real split.

**Exit:** an upload of a multi-GB raster runs as a tracked background job with metrics,
never blocking API latency; p99 read latency unaffected under ingest load.

---

## Phase 3 — Raster Processing Service + Map Tile Service

**Goal:** independent scaling and specialised runtime for the heaviest work.

- **Extract the Raster Processing Service** (trigger: independent scaling + specialised
  GDAL/CPU runtime). It consumes jobs from the queue, reads/writes via the `Storage`
  seam, and writes results back through a narrow internal contract. Scale it on CPU
  independently of the API.
- **Map Tile Service** — serve XYZ/WMTS tiles from Cloud-Optimised GeoTIFFs (COG);
  convert stored rasters to COG on ingest. Powers the interactive map, time slider,
  and before/after comparison without shipping full rasters to the browser.
- Feature epics: **Interactive Map, Satellite Layer Switching (Sentinel-1/2, Landsat;
  Planet/Maxar placeholders), Time Slider, Before/After Comparison, Raster/Vector
  Catalogs, AOI Management, Boundary Editing.**

**Exit:** tiles render client-side from COGs; raster workers autoscale separately from
the API.

---

## Phase 4 — Analytics & Carbon Accounting Engine

**Goal:** the domain core that turns rasters into defensible carbon numbers.

- **Analytics Engine** and **Carbon Accounting Engine** (trigger: failure isolation —
  heavy batch analytics must not affect interactive traffic; and independent
  correctness/audit review of the accounting logic).
- Feature epics: **Vegetation Indices (NDVI/EVI/SAVI/NDMI/NBR), Canopy Cover/Density,
  Biomass & Carbon Stock Estimation, Carbon Sequestration Tracking, Change Detection,
  Deforestation Alerts, Reforestation Progress, Leakage/Additionality/Permanence
  indicators, Baseline & Monitoring-Period Management, Historical Trends, Uncertainty
  & Confidence Reporting.**
- All accounting methods versioned and reproducible (every KPI already carries its
  measurement CRS; extend with method + parameter provenance) — essential for registry
  audits.

**Exit:** a project produces versioned, uncertainty-quantified carbon estimates with a
full, reproducible lineage from raster to number.

---

## Phase 5 — Workflow, QA/QC, Verification, and Evidence

**Goal:** the MRV *process* — review, approval, and verifiable evidence.

- **Workflow Engine, Validation Service, Audit Service** (Audit is a strong extraction
  candidate: a registry-grade, append-only, independently-retained audit store).
- Feature epics: **QA/QC Dashboard, Automated Validation Pipeline, Validation Rules,
  Exception Management, Approval Workflow, Task Assignment, Reviewer/Verifier
  Dashboards, Evidence Management, Supporting Documents, Data-Quality Assessment,
  Workflow Status Tracking, Activity Feed.**

**Exit:** a dataset moves through draft → validated → reviewed → verified with a
complete, attributable, exportable evidence trail.

---

## Phase 6 — Enterprise frontend

**Goal:** the analytics UI comparable to ArcGIS Enterprise / Planet Explorer / Stripe
dashboard. (Phase-1 delivered only the production-serving fix and central config; the
UI build is a program in itself.)

- Design system + component library; responsive layout; **dark mode; accessibility
  (WCAG); role-based navigation; global search; advanced filters; saved views;
  keyboard shortcuts; notification center; dashboard customization; real-time updates**
  (SSE/WebSocket off the job events from Phase 2).
- Feature epics: **Project Portfolio Dashboard, Project Timeline, Project Health Score,
  KPI Dashboard, Pixel/Area Statistics, Monthly/Annual Analytics, Export (PDF/CSV/
  Excel/GeoJSON/Shapefile), System/Storage/API/Operational dashboards.**

**Exit:** an enterprise-grade SPA driving the full API surface, with auth via httpOnly
cookies (completing the token-storage hardening).

---

## Phase 7 — Reporting, Notifications, Integrations, Admin

- **Report Generation Service** (PDF/Excel), **Notification Service** (email/webhook),
  **Administration Service**, **User & Team / Organization Management**, **API Tokens &
  Webhooks**, **Usage Analytics**.
- External data epics: **Climate (rainfall/temperature), Fire/Flood/Drought alerts,
  Disaster & Biodiversity/Habitat monitoring, Protected-Area/Admin/Road/Water overlays,
  OSM integration, Field/GPS/Drone/Mobile data, optional Google Earth Engine.**

---

## Phase 8 — Cloud, Kubernetes, IaC, DR

**Goal:** the production platform on AWS/EKS, review-ready for cloud/operational/DR
boards.

- **Terraform** (VPC, EKS, RDS/Aurora PostGIS, S3, ElastiCache, IAM, Secrets Manager),
  **Helm charts** per service, **HPA**, **Ingress**, **rolling + blue-green + canary**
  deploys, **pgbouncer** for cross-pod pooling.
- **Disaster Recovery:** automated backups + PITR, restore runbooks + drills, defined
  RPO/RTO, multi-AZ, read replicas.
- **Runbooks / Troubleshooting / Scaling / Security / Monitoring / DR guides**, SRS &
  SOP rewrites.
- API Gateway and dedicated Authentication Service extracted here **only if** multi-service
  auth or third-party API exposure justifies it (otherwise the gateway role is the
  Ingress + the monolith's auth).

**Exit:** one-command environment provisioning; a rehearsed restore that meets the
stated RPO/RTO; passing security and DR reviews.

---

## Service extraction summary

| Service | Phase | Trigger |
|---|---|---|
| Job/Worker runtime | 2 | Async processing; separate deploy of same image |
| Raster Processing | 3 | Independent CPU scaling + GDAL runtime |
| Map Tile | 3 | Specialised serving (COG/XYZ), edge-cacheable |
| Analytics Engine | 4 | Failure isolation from interactive traffic |
| Carbon Accounting | 4 | Independent correctness/audit review |
| Workflow Engine | 5 | Long-running stateful processes |
| Validation | 5 | Independent rule cadence |
| Audit | 5 | Registry-grade append-only, separate retention |
| Report Generation | 7 | Bursty CPU, isolate from API |
| Notification | 7 | Third-party IO, ret/DLQ isolation |
| Admin / User / Org | 7 | Distinct ownership |
| API Gateway / Auth | 8 | Multi-service auth / external API exposure |

Everything else (Project, Dataset, File management) remains in the core service —
splitting them would create chatty, transaction-spanning calls with no scaling or
ownership benefit.

## A note on effort

This is a multi-quarter program for a team, not a one-pass generation. The value of
Phase-1 is that it is *real and verified*, and that every later phase attaches to an
existing seam rather than requiring a rewrite. Each phase should ship behind feature
flags with the same bar Phase-1 met: green tests, clean lint, migrations, and a
verification checklist.
