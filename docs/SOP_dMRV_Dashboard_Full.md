VALUE NETWORK VENTURES (VNV)
GIS & Carbon Analytics Division

# Standard Operating Procedure

dMRV Analytical Dashboard Development
Stage 1 - Full Development, Operations and Governance Procedure
Revision History

# Table of Contents

(Right-click the table above and choose "Update Field" in Microsoft Word to populate section page numbers.)

# 1. Purpose and Scope

This document is the working procedure for building, operating, and maintaining the dMRV Analytical Dashboard. It replaces the shorter 4-page Stage 1 draft with the full set of steps a developer or GIS Associate needs to actually carry out the work - not just what happens, but how to do it, what commands to run, what to check, and what to do when something goes wrong.
The procedure is written against the pilot sample project, using classified datasets VNV has already produced (LULC, NDVI, biomass/carbon, plot boundaries). Where a step will need to change for Stage 3 (multiple projects), that is noted directly in the relevant section rather than kept as a separate document, so this stays the single reference as the project grows.

## 1.1 In Scope

Environment setup for local/pilot development and the target production environment.
The full data pipeline: collection, validation, database loading, publishing, KPI computation, dashboard rendering, and review.
Data standards, validation rules, error handling, and rollback procedures.
Security, access control, monitoring, and incident response relevant to operating the pilot.

## 1.2 Out of Scope

Satellite image classification and model training (handled upstream by existing GIS workflows).
Field data collection tooling and survey logistics.
Financial or carbon-credit transaction processing.

# 2. Definitions and Abbreviations


# 3. Roles and Responsibilities

Responsibility is expressed below as a RACI matrix: Responsible (does the work), Accountable (owns the outcome), Consulted (input sought before the step), Informed (told after the step happens).
Where "Analyst" and "Developer" are listed separately from "GIS Associate", this reflects the target Stage 3 team shape. For the pilot, the GIS Associate role covers all three until those positions are filled.

# 4. Prerequisites and Environment Setup


## 4.1 Required Access

Git repository access (read/write) for the project codebase.
Credentials for the target PostGIS instance (local or pilot server).
GeoServer administrator login for layer/workspace configuration.
Docker installed locally, or SSH access to the pilot server where Docker Compose will run.

## 4.2 Local Development Setup

The full stack runs locally via Docker Compose for development and for the two-week pilot. A minimal compose file looks like this:
Bring the stack up with:

## 4.3 Environment Variables


# 5. Detailed Development Procedure

This section is the operational core of the SOP: the seven-step pipeline first outlined in the v1.0 draft, expanded here into concrete sub-procedures with the commands, schemas, and checks each step actually requires.

## 5.1 Step 1 - Data Collection

The GIS Associate exports the classified dataset from its source (QGIS, Google Earth Engine) into one of the accepted formats (GeoJSON, Shapefile, GeoTIFF, CSV) and places it in the ingestion drop folder or uploads it via the Ingestion Service's API.
Confirm the export includes all mandatory fields before leaving the source tool (see Section 6, Data Standards).
Name the file following the project convention: <project>_<datatype>_<date>.<ext>
Record the source and processing method in the accompanying metadata note.

## 5.2 Step 2 - Data Validation

The Validation Service runs each incoming dataset through the rule catalog in Appendix A. At a code level, the core checks look like this:
Every validation run produces an error log keyed by batch ID. Rows failing a Blocking rule (see Appendix A) are excluded from the load; rows failing a Warning rule are loaded but flagged for review.

## 5.3 Step 3 - Database Loading

Validated records are loaded into PostGIS using a schema where every spatial table carries a batch ID and load timestamp for traceability:
The GIST index is what keeps map queries fast as the table grows; do not skip it when adding new spatial tables.

## 5.4 Step 4 - Map Publishing

The Map Publishing Service wraps GeoServer's REST API. A new layer is published in three calls: create the workspace (once per project), register the PostGIS datastore, then publish the layer.
Confirm the layer is live by requesting GetCapabilities and checking it appears, then spot-check the rendered layer in GeoServer's own preview before considering the step complete.

## 5.5 Step 5 - KPI and Carbon Stock Computation

The Analytics Service computes two families of metrics: vegetation trend statistics and biomass/carbon estimates.
NDVI trend slope, per plot, is computed as an ordinary least-squares regression of NDVI value against time:
Carbon stock is derived from biomass using a configurable conversion factor (CARBON_CONVERSION_FACTOR), following the standard approach of treating carbon as roughly half of dry biomass:
The conversion factor is not hard-coded; it is read from configuration so the Analyst can adjust it as methodology guidance changes, without a code deployment (see FR-406 in the companion SRS).

## 5.6 Step 6 - Dashboard Rendering

The Dashboard BFF Service aggregates KPI and layer metadata into the shape the React frontend expects, then the frontend renders it in three coordinated pieces: a Leaflet map bound to the published WMS layers, KPI summary cards fed by the BFF's /kpis endpoint, and trend charts fed by /kpis/timeseries.
Map component subscribes to layer toggle state and re-requests WMS tiles only for active layers.
KPI cards poll on dashboard load and on manual refresh, not continuously, to avoid unnecessary API load.
Chart component uses Chart.js line charts for NDVI trend and bar charts for class-wise area breakdown.

## 5.7 Step 7 - Internal QA/QC Review

Before a sample project is considered onboarded, the Team Lead walks the checklist in Appendix C against the live dashboard, not just the underlying data. Any item that fails is logged, assigned back to the GIS Associate, and re-checked before sign-off.

# 6. Data Standards and Naming Conventions


## 6.1 File Naming

Pattern: <project>_<datatype>_<date>.<ext>, all lowercase, underscores only (no spaces or hyphens).

## 6.2 Coordinate System

All stored geometry uses EPSG:4326. Datasets arriving in a different CRS are reprojected automatically during validation (Rule VR-02); datasets in an unsupported or unidentifiable CRS are rejected rather than guessed at.

## 6.3 Layer Naming (GeoServer)

Published layer names mirror the source file name without the extension, so a person looking at the dashboard's layer list can trace it straight back to the source file and its batch ID.

## 6.4 Metadata Requirements

Every dataset must carry, at minimum: source (e.g. Sentinel-2, GEDI L4A, field survey), classification method, accuracy or confidence score where applicable, and date of processing. This is what makes the dashboard's output traceable enough to support an external MRV/verification review later, without redoing the work retroactively.

# 7. Validation Rule Catalog

The full set of rules the Validation Service applies. Blocking rules exclude a record from loading; Warning rules load the record but flag it for manual review.

# 8. Error Handling and Rollback Procedures


## 8.1 Failed Ingestion

If a batch fails partway through loading (for example, a constraint violation on row 4,000 of 5,000), the load is not left half-committed. The Ingestion Service wraps each batch in a single database transaction keyed by batch ID:
This guarantees a batch is either fully loaded or not loaded at all - there is no partial state to clean up manually.

## 8.2 Rolling Back a Bad Load

If a batch loaded successfully but is later found to contain bad data (e.g. wrong conversion factor applied upstream), it can be removed by batch ID without touching other batches:
Per Section 9 (Data Governance) this is treated as a soft-delete in practice: the row is moved to an archive table rather than hard-deleted, preserving the audit trail.

## 8.3 Recomputation After a Parameter Change

When an analytical parameter changes (e.g. CARBON_CONVERSION_FACTOR), affected KPIs are not silently left stale. The Analytics Service tags every computed KPI with the parameter version used, so a change triggers a recomputation job scoped only to KPIs computed under the old version.

# 9. QA/QC Checkpoints and Data Governance

Four checkpoints apply across the pipeline, each with a named owner:
Data governance note: no dataset is ever hard-deleted from the system. Removals are soft-deletes retaining an audit record, per the companion SRS (NFR-19), so that any published KPI can always be traced back to the data and parameters that produced it.

# 10. Version Control and Change Management


## 10.1 Branching Strategy

main - always deployable; protected, no direct commits.
feature/<short-description> - one branch per unit of work, merged via pull request.
Commit messages follow a short imperative style, e.g. "Add CRS reprojection to validation service".

## 10.2 Dataset Versioning

Datasets are versioned by batch ID and load timestamp rather than a semantic version number - since a "version" here is really just "which load produced this data". This is sufficient for the pilot's traceability needs; a more formal dataset versioning scheme can be introduced in Stage 3 if multiple teams start contributing datasets concurrently.

## 10.3 Configuration Changes

Changes to analytical parameters (e.g. conversion factors) are made through the configuration screen (FR-406 in the SRS) rather than by editing code, and are logged with who changed it, when, and the old/new values.

# 11. Security and Access Control


## 11.1 Authentication

All API requests are authenticated via JWT. The API Gateway validates the token signature and expiry before forwarding a request to any backend service; individual services trust the gateway rather than re-validating independently.

## 11.2 Role-Based Access Control


## 11.3 Secrets Management

Database passwords and JWT signing keys are never committed to Git; they are supplied via environment variables (local) or a secrets manager (production).
GeoServer admin credentials are rotated on a defined schedule, not left as the default install credentials.

# 12. Monitoring and Maintenance


## 12.1 Logging

Each microservice logs structured JSON to stdout, which is the convention that plays cleanly with container log collection regardless of which orchestrator (Docker Compose or Kubernetes) is in front of it. Minimum fields: timestamp, service name, batch ID (where relevant), and log level.

## 12.2 Backups

PostGIS: nightly logical backup (pg_dump), retained on a rolling 30-day window per NFR-09 in the SRS.
Object storage: versioned bucket or equivalent, so an overwritten export can still be recovered.

## 12.3 Health Checks

Each service exposes a lightweight /health endpoint returning 200 when it can reach its own dependencies (e.g. the Ingestion Service checks it can reach PostGIS). This is what the container orchestrator polls to decide whether to restart a service.

# 13. Incident Response and Troubleshooting

Common failure scenarios encountered during development or pilot operation, and the first thing to check for each:

# 14. Tools and Environment Reference


# 15. Approval

This SOP is a working document; Section 5 in particular is expected to be refined as Stage 2 build work surfaces details that only show up once the pipeline is actually running against real data.

# Appendix A: Full Validation Rule Catalog

Duplicated here from Section 7 for quick reference during implementation, without needing to scroll back.

# Appendix B: File and Layer Naming Quick Reference


# Appendix C: Sign-off Checklist

Used by the Team Lead at Checkpoint 4 (Section 9) before a sample project is marked complete.
[ ] All four dataset types (LULC, NDVI, biomass/carbon, plot boundaries) are ingested with zero unresolved Blocking-rule errors.
[ ] Each dataset type is published and independently toggleable on the dashboard map.
[ ] KPI summary cards match a manual spot-check within agreed tolerance.
[ ] Dashboard loads within the performance targets defined in the SRS (NFR-01, NFR-02).
[ ] A Viewer-role test account cannot upload or edit data; a GIS Associate test account can.
[ ] Audit log shows entries for every upload, publish, and configuration change made during the pilot.
[ ] Backup of the loaded PostGIS data exists and has been spot-restored at least once.

# Appendix D: Glossary


**Table 1**

| Field | Detail |
|---|---|
| Document Title | Standard Operating Procedure - dMRV Analytical Dashboard Development |
| Document Owner | Denish M, Junior GIS Associate |
| Reviewed By | Jibotosh, Team Lead |
| Scope | Pilot sample project, with procedures written to extend to Stage 3 (multi-project scale) |
| Version | 2.0 - Expanded Draft for Review |
| Date | 09 July 2026 |
| Classification | Internal - VNV Use Only |


**Table 2**

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0 | 09 July 2026 | Denish M | Initial 4-page draft covering the seven-step pipeline |
| 2.0 | 09 July 2026 | Denish M | Expanded to full operational SOP: environment setup, detailed sub-procedures, validation rule catalog, error handling, security, monitoring, and checklists |


**Table 3**

| Term | Definition |
|---|---|
| dMRV | Digital Monitoring, Reporting and Verification |
| LULC | Land Use / Land Cover classification |
| NDVI | Normalized Difference Vegetation Index |
| GEDI L4A | Global Ecosystem Dynamics Investigation, Level 4A biomass product |
| SAR | Synthetic Aperture Radar (e.g. Sentinel-1) |
| PostGIS | Spatial extension for PostgreSQL |
| WMS / WFS | Web Map Service / Web Feature Service - OGC standards for serving map layers |
| KPI | Key Performance Indicator |
| CRS | Coordinate Reference System |
| RBAC | Role-Based Access Control |
| BFF | Backend-for-Frontend - a service that shapes API responses for a specific client |
| GIST index | Generalized Search Tree index, used by PostGIS to speed up spatial queries |
| Batch ID | Unique identifier assigned to a single ingestion run, used for tracing and rollback |


**Table 4**

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Collect classified dataset | GIS Associate | Team Lead | QA/QC Team | - |
| Validate geometry/CRS/attributes | GIS Associate | - | QA/QC Team | Team Lead |
| Load into PostGIS | GIS Associate | - | - | Team Lead |
| Publish WMS/WFS layer | GIS Associate | - | Team Lead | - |
| Compute KPIs / carbon stock | Analyst | GIS Associate | Team Lead | - |
| Render dashboard views | Developer | GIS Associate | Team Lead | - |
| Internal QA/QC review | Team Lead | - | GIS Associate, QA/QC Team | - |
| Approve sample project sign-off | Team Lead | - | - | GIS Associate |
| Manage user roles/access | Administrator | - | - | Team Lead |
| Update analytical parameters | Analyst | Administrator | Team Lead | - |


**Table 5**

| version: "3.9"<br>services:<br>  postgis:<br>    image: postgis/postgis:16-3.4<br>    environment:<br>      POSTGRES_DB: dmrv<br>      POSTGRES_USER: dmrv_app<br>      POSTGRES_PASSWORD: ${DB_PASSWORD}<br>    ports: ["5432:5432"]<br>    volumes: ["pgdata:/var/lib/postgresql/data"]<br> <br>  geoserver:<br>    image: kartoza/geoserver:2.25.0<br>    ports: ["8080:8080"]<br>    depends_on: [postgis]<br> <br>  ingestion-service:<br>    build: ./services/ingestion<br>    environment:<br>      DATABASE_URL: postgresql://dmrv_app:${DB_PASSWORD}@postgis:5432/dmrv<br>    depends_on: [postgis]<br> <br>  api-gateway:<br>    build: ./services/gateway<br>    ports: ["8000:8000"]<br>    depends_on: [ingestion-service]<br> <br>volumes:<br>  pgdata: |
|---|


**Table 6**

| docker compose up -d<br>docker compose logs -f ingestion-service   # tail logs for one service |
|---|


**Table 7**

| Variable | Purpose |
|---|---|
| DATABASE_URL | Connection string used by every service that talks to PostGIS |
| DB_PASSWORD | Database password, injected via secret manager in production, .env locally |
| GEOSERVER_URL | Base URL the Map Publishing Service uses to call GeoServer's REST API |
| JWT_SECRET | Signing key for API authentication tokens |
| CARBON_CONVERSION_FACTOR | Configurable biomass-to-carbon multiplier used by the Analytics Service |


**Table 8**

| import geopandas as gpd<br> <br>def validate(gdf: gpd.GeoDataFrame) -> list[dict]:<br>    errors = []<br>    invalid = gdf[~gdf.geometry.is_valid]<br>    for idx in invalid.index:<br>        errors.append({"row": idx, "rule": "VR-01", "detail": "invalid geometry"})<br> <br>    if gdf.crs is None or gdf.crs.to_epsg() != 4326:<br>        gdf = gdf.to_crs(epsg=4326)  # auto-reproject per VR-02<br> <br>    required = ["plot_id", "class_label", "date_processed"]<br>    missing = gdf[required].isnull().any(axis=1)<br>    for idx in gdf[missing].index:<br>        errors.append({"row": idx, "rule": "VR-03", "detail": "missing required field"})<br> <br>    return errors |
|---|


**Table 9**

| CREATE TABLE dataset_lulc (<br>    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),<br>    project_id    UUID NOT NULL REFERENCES project(project_id),<br>    plot_id       TEXT NOT NULL,<br>    class_label   TEXT NOT NULL,<br>    date_processed DATE NOT NULL,<br>    batch_id      UUID NOT NULL,<br>    loaded_at     TIMESTAMP NOT NULL DEFAULT now(),<br>    geom          GEOMETRY(Polygon, 4326) NOT NULL<br>);<br> <br>CREATE INDEX idx_dataset_lulc_geom ON dataset_lulc USING GIST (geom);<br>CREATE INDEX idx_dataset_lulc_batch ON dataset_lulc (batch_id); |
|---|


**Table 10**

| curl -u admin:${GEOSERVER_PASSWORD} -X POST \<br>  -H "Content-type: text/xml" \<br>  -d "<workspace><name>sample_project</name></workspace>" \<br>  ${GEOSERVER_URL}/rest/workspaces<br> <br>curl -u admin:${GEOSERVER_PASSWORD} -X POST \<br>  -H "Content-type: application/json" \<br>  -d @datastore.json \<br>  ${GEOSERVER_URL}/rest/workspaces/sample_project/datastores<br> <br>curl -u admin:${GEOSERVER_PASSWORD} -X POST \<br>  -H "Content-type: application/json" \<br>  -d '{"featureType":{"name":"dataset_lulc"}}' \<br>  ${GEOSERVER_URL}/rest/workspaces/sample_project/datastores/postgis/featuretypes |
|---|


**Table 11**

| slope = Σ((t_i - t̄)(ndvi_i - ndvi̅)) / Σ((t_i - t̄)²) |
|---|


**Table 12**

| carbon_stock_tCO2e = biomass_tonnes * CARBON_CONVERSION_FACTOR |
|---|


**Table 13**

| Example | Meaning |
|---|---|
| sampleproj_lulc_20260601.geojson | LULC classification for "sampleproj", processed 1 June 2026 |
| sampleproj_ndvi_20260615.tif | NDVI raster for "sampleproj", processed 15 June 2026 |
| sampleproj_biomass_20260701.geojson | Biomass/carbon estimate layer, processed 1 July 2026 |


**Table 14**

| ID | Rule | Description | Severity | Handling |
|---|---|---|---|---|
| VR-01 | Geometry validity | Polygon/line geometries must not be self-intersecting or null. | Blocking | Auto-flagged, requires manual fix |
| VR-02 | CRS match | Dataset CRS must equal EPSG:4326 or a supported alternate that can be reprojected. | Blocking | Auto-reprojected where possible |
| VR-03 | Mandatory fields present | Plot ID, classification label, and processing date must be non-null. | Blocking | Manual fix required |
| VR-04 | Duplicate detection | No two records share the same Plot ID + date combination. | Warning | Flagged for review, not auto-rejected |
| VR-05 | Area sanity check | Computed polygon area must fall within 0.1-500 hectares for a single plot record. | Warning | Flagged for manual review |
| VR-06 | Date plausibility | Processing date must not be in the future or earlier than project start date. | Blocking | Manual fix required |
| VR-07 | Classification label validity | LULC class labels must match the approved class list (e.g. Forest, Cropland, Water, Built-up). | Blocking | Manual fix required |
| VR-08 | Accuracy score range | Reported classification accuracy/confidence must be between 0 and 100. | Warning | Flagged for review |
| VR-09 | Raster resolution check | NDVI/biomass rasters must match the project's approved resolution (10m or 30m). | Warning | Flagged, may be resampled |
| VR-10 | File format check | Only GeoJSON, Shapefile, GeoTIFF, and CSV are accepted at ingestion. | Blocking | Rejected outright |
| VR-11 | Coordinate bounds check | All coordinates must fall within the project's registered bounding box. | Blocking | Manual fix required |
| VR-12 | Metadata completeness | Source, classification method, and accuracy score must be recorded before publishing. | Blocking | Manual fix required |
| VR-13 | Layer naming convention | Layer name must follow <project>_<datatype>_<date> pattern before publishing. | Warning | Auto-corrected where possible |


**Table 15**

| BEGIN;<br>-- all inserts for this batch_id happen here<br>-- if any insert violates a constraint, the whole transaction rolls back<br>COMMIT; |
|---|


**Table 16**

| DELETE FROM dataset_lulc WHERE batch_id = '<batch-uuid>'; |
|---|


**Table 17**

| Checkpoint | What is Checked | When | Owner |
|---|---|---|---|
| 1 | Validation error log reviewed; no unresolved Blocking-rule failures remain | After Step 2, before loading | GIS Associate |
| 2 | Published layer renders correctly in GeoServer's own preview | After Step 4 | GIS Associate |
| 3 | KPI values cross-checked against a manual spot calculation on a sample of records | After Step 5 | Analyst |
| 4 | Full dashboard walkthrough against the Appendix C checklist | Before project sign-off | Team Lead |


**Table 18**

| Role | Can Do | Cannot Do |
|---|---|---|
| Administrator | Manage users/roles, change system configuration | - |
| GIS Associate | Upload, validate, publish datasets | Manage user accounts |
| Analyst | Update analytical parameters, review KPIs | Upload/publish datasets |
| Viewer | View dashboards, export reports | Upload, edit, or configure anything |


**Table 19**

| Symptom | Likely Cause | First Step |
|---|---|---|
| Dataset upload fails silently | File exceeds size limit or unsupported encoding | Check ingestion service logs for the batch ID; confirm file size and encoding (UTF-8 expected) |
| Validation rejects all records | CRS mismatch or corrupted geometry column | Open the file in QGIS to confirm CRS; re-export with EPSG:4326 if needed |
| Layer does not appear on dashboard after publishing | GeoServer cache not refreshed, or WMS URL not registered with the Dashboard BFF | Manually clear GeoServer layer cache; verify the capabilities document lists the new layer |
| KPI values look wrong after recomputation | Stale conversion factor or partial batch commit | Check Analytics Service logs for the last computation run; re-trigger with the corrected parameter |
| Dashboard map is slow to load | Missing spatial index or overly complex geometry | Confirm a GIST index exists on the geometry column; consider simplifying geometry for display |
| API returns 401 Unauthorized | Expired or malformed JWT token | Re-authenticate; check token expiry configuration on the API Gateway |
| Database load partially completes then errors | Constraint violation mid-batch | Roll back the batch using the load-batch identifier (see Section 8); fix source data and re-run |


**Table 20**

| Tool | Version | Purpose |
|---|---|---|
| GeoPandas | 0.14.x | Vector data handling, geometry validation |
| Shapely | 2.0.x | Geometry operations (validity checks, buffering) |
| Rasterio | 1.3.x | Raster read/write, resolution checks |
| GDAL | 3.8.x | Format conversion, reprojection |
| PostgreSQL | 16.x | Relational + spatial database engine |
| PostGIS | 3.4.x | Spatial extension for PostgreSQL |
| GeoServer | 2.25.x | WMS/WFS map publishing |
| FastAPI | 0.110.x | REST API framework (Python) |
| React | 18.x | Dashboard frontend framework |
| Leaflet | 1.9.x | Interactive map rendering |
| Chart.js | 4.x | Trend and KPI charting |
| Docker / Docker Compose | 24.x / 2.x | Containerisation and local orchestration |
| Kubernetes | 1.29.x (Stage 3 target) | Container orchestration at scale |
| Git | 2.4x | Version control |


**Table 21**

| Prepared By | Reviewed By | Date |
|---|---|---|
| Denish M | Jibotosh | 09 July 2026 |


**Table 22**

| ID | Rule | Description | Severity | Handling |
|---|---|---|---|---|
| VR-01 | Geometry validity | Polygon/line geometries must not be self-intersecting or null. | Blocking | Auto-flagged, requires manual fix |
| VR-02 | CRS match | Dataset CRS must equal EPSG:4326 or a supported alternate that can be reprojected. | Blocking | Auto-reprojected where possible |
| VR-03 | Mandatory fields present | Plot ID, classification label, and processing date must be non-null. | Blocking | Manual fix required |
| VR-04 | Duplicate detection | No two records share the same Plot ID + date combination. | Warning | Flagged for review, not auto-rejected |
| VR-05 | Area sanity check | Computed polygon area must fall within 0.1-500 hectares for a single plot record. | Warning | Flagged for manual review |
| VR-06 | Date plausibility | Processing date must not be in the future or earlier than project start date. | Blocking | Manual fix required |
| VR-07 | Classification label validity | LULC class labels must match the approved class list (e.g. Forest, Cropland, Water, Built-up). | Blocking | Manual fix required |
| VR-08 | Accuracy score range | Reported classification accuracy/confidence must be between 0 and 100. | Warning | Flagged for review |
| VR-09 | Raster resolution check | NDVI/biomass rasters must match the project's approved resolution (10m or 30m). | Warning | Flagged, may be resampled |
| VR-10 | File format check | Only GeoJSON, Shapefile, GeoTIFF, and CSV are accepted at ingestion. | Blocking | Rejected outright |
| VR-11 | Coordinate bounds check | All coordinates must fall within the project's registered bounding box. | Blocking | Manual fix required |
| VR-12 | Metadata completeness | Source, classification method, and accuracy score must be recorded before publishing. | Blocking | Manual fix required |
| VR-13 | Layer naming convention | Layer name must follow <project>_<datatype>_<date> pattern before publishing. | Warning | Auto-corrected where possible |


**Table 23**

| File:  <project>_<datatype>_<date>.<ext><br>  e.g. sampleproj_lulc_20260601.geojson<br> <br>Layer (GeoServer): matches file name minus extension<br>  e.g. sampleproj_lulc_20260601<br> <br>Batch ID: system-generated UUID, one per ingestion run |
|---|


**Table 24**

| Term | Definition |
|---|---|
| Batch | One complete ingestion run of a single dataset file, identified by a batch ID |
| Blocking rule | A validation rule whose failure excludes a record from being loaded |
| Warning rule | A validation rule whose failure flags a record for review but does not block loading |
| Soft delete | Marking a record as removed without physically deleting it, preserving audit history |
| BFF | Backend-for-Frontend - a service that assembles data specifically for one client (the dashboard) |
