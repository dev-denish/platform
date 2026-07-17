VALUE NETWORK VENTURES (VNV)
GIS & Carbon Analytics Division

# Software Requirements Specification

dMRV Analytical Dashboard for Classified Datasets
Pilot Sample Project Implementation
Revision History

# Table of Contents

(Right-click the table above and choose "Update Field" in Microsoft Word to populate section page numbers.)

# 1. Introduction


## 1.1 Purpose

This Software Requirements Specification (SRS) defines the functional and non-functional requirements for the dMRV Analytical Dashboard, an internal GIS platform being developed at Value Network Ventures (VNV) to visualize and analyze classified datasets - land use/land cover (LULC), NDVI time-series, biomass and carbon stock estimates, and validated plot/bund boundaries - produced through VNV's existing GIS and remote-sensing workflows.
The document is intended to give the development team, the reviewing Team Lead, and any future contributor a single, unambiguous reference for what the system must do before implementation (Stage 2) begins.

## 1.2 Document Conventions

Requirement identifiers use the prefix FR- for functional requirements and NFR- for non-functional requirements.
Priority is expressed as High, Medium, or Low, reflecting criticality to the pilot sample project.
"Shall" denotes a mandatory requirement; "should" denotes a recommended but non-mandatory capability.

## 1.3 Intended Audience and Reading Suggestions

Team Lead / Reviewer - Sections 1, 2, 8, and 10 for scope, context, and sign-off criteria.
Developers (Stage 2 build) - Sections 3, 4, 5, and 6 for architecture and detailed requirements.
QA / Reviewer - Section 8 (Use Cases) and Appendix A (Traceability Matrix) for test planning.

## 1.4 Project Scope

The system in scope is a web-based analytical dashboard that ingests already-classified GIS datasets for a single pilot project, stores them in a spatial database, publishes them as standard map services, computes summary KPIs (including carbon stock estimates), and presents both in an interactive dashboard. This SRS covers the pilot (sample project) implementation. Scaling to the full project portfolio is addressed as a future phase (Stage 3) and is out of scope for the requirements below except where explicitly noted.
Out of scope for this SRS: satellite image classification/model training, field data collection tooling, and financial/carbon-credit transaction processing.

## 1.5 Definitions, Acronyms, and Abbreviations


## 1.6 References

VNV Stage 1 Standard Operating Procedure - Analytical Dashboard Development (companion document).
IEEE Std 830-1998 - Recommended Practice for Software Requirements Specifications (structural reference).
OGC Web Map Service (WMS) and Web Feature Service (WFS) Implementation Standards.
Verra VCS Standard v5 (AFOLU requirements context, referenced for data governance expectations).

# 2. Overall Description


## 2.1 Product Perspective

The dMRV Analytical Dashboard is a new, internally-developed system. It is not a replacement for existing GIS processing tools (QGIS, Google Earth Engine scripts) used to produce classified datasets - it consumes their outputs. It sits downstream of the classification/processing workflow and upstream of any external reporting or verification review.

## 2.2 Product Functions

Ingest and validate classified datasets against defined data-quality rules.
Store validated datasets in a spatial database with full metadata.
Publish map layers via WMS/WFS for consumption by the dashboard and external GIS clients.
Compute derived KPIs, including NDVI trend statistics and carbon stock estimates.
Present an interactive map-and-charts dashboard for internal stakeholders.
Control access by role and maintain an audit trail of key actions.

## 2.3 User Classes and Characteristics


## 2.4 Operating Environment

Server-side components (database, API, map server) deployed via Docker containers on a Linux host (on-premise server or cloud VM, to be confirmed).
Client-side dashboard accessed through modern web browsers (Chrome, Edge, Firefox - latest two major versions).
No native mobile application is in scope; the dashboard shall be usable on tablet-sized browser viewports.

## 2.5 Design and Implementation Constraints

All software components shall be open source to avoid licensing cost, per VNV's technology policy for this pilot.
The technology stack shall reuse components already proven in VNV's existing Automated GIS Satellite Platform (PostGIS, GeoServer, Flask/FastAPI, Leaflet) to minimise new-tool risk.
The system shall store all spatial data in EPSG:4326 to keep a single consistent internal standard.

## 2.6 Assumptions and Dependencies

Classified datasets (LULC, NDVI, biomass/carbon) are already produced and QA/QC'd by the existing GIS workflow; this system does not perform classification itself.
A server or cloud environment will be made available for hosting PostGIS and GeoServer before Stage 2 begins.
Google Earth Engine outputs (GEDI L4A, Dynamic World, Sentinel-1 SAR) are exported in a format ingestible by the validation module.

# 3. System Architecture Overview

The system is designed as a set of independently deployable microservices running in containers, rather than a single monolithic application. Each service owns one responsibility - ingestion, validation, analytics, map publishing, or serving the dashboard - and can be built, tested, deployed, and scaled on its own. This keeps the pilot small enough to build in two weeks while giving Stage 3 a structure that scales to many projects without a rewrite.
Requests from the dashboard client enter through a single API Gateway, which handles authentication, routing, and rate limiting before forwarding to the relevant backend service. Services communicate with each other either directly (for synchronous requests, e.g. fetching a KPI) or through an event bus for longer-running or asynchronous work such as dataset validation and carbon-stock computation. All services run inside a container orchestration layer (Kubernetes for production, Docker Compose for local/pilot development), which handles restarts, scaling, and service discovery.
Figure 1: Cloud-Native Microservices Architecture

## 3.1 Component Summary


## 3.2 Why Microservices for This Pilot

A single sample project does not strictly need this much separation - a monolith would work too. The reason to start with service boundaries now is Stage 3: once the dashboard covers many projects, ingestion and analytics load will grow independently of dashboard traffic. Splitting them from day one means scaling one service under load doesn't require scaling the rest, and a bug in, say, the analytics computation cannot take down map publishing or the dashboard itself.
The tradeoff is added operational complexity - more moving parts to deploy and monitor than a single application. For the pilot, this is kept manageable by running all services through Docker Compose on one host; the same container images move to Kubernetes without modification once the environment is ready for Stage 3.

## 3.3 Deployment Topology

Each service is packaged as its own Docker image with a minimal base (python:3.11-slim or node:20-alpine as appropriate).
Pilot deployment: Docker Compose on a single VM, with PostGIS and GeoServer as long-running containers and the API services restarting independently.
Stage 3 target: Kubernetes cluster with one Deployment per service, a ClusterIP Service for internal routing, and an Ingress controller in front of the API Gateway.
Configuration (database URLs, conversion factors, feature flags) is injected via environment variables / ConfigMaps, not hard-coded, so the same image runs in any environment.

# 4. Functional Requirements

Functional requirements are grouped by module. Each requirement carries a unique identifier for traceability (see Appendix A).

## 4.1 Data Ingestion & Validation Module

Handles intake of classified GIS/remote-sensing datasets from source projects and verifies they meet quality standards before entering the spatial database.

## 4.2 Spatial Database Management Module

Provides persistent, queryable storage for validated vector and raster datasets using PostgreSQL with the PostGIS spatial extension.

## 4.3 Map Publishing Service Module

Exposes stored spatial layers as standards-based map services so they can be consumed by the dashboard and by external GIS clients.

## 4.4 Carbon & Biomass Analytics Module

Computes derived analytical outputs - vegetation trends, biomass estimates, and carbon stock figures - from the underlying classified and remote-sensing datasets.

## 4.5 KPI & Reporting API Module

Serves computed KPIs, metadata, and analytical results to the dashboard and to downstream reporting tools through a documented REST API.

## 4.6 Dashboard & Visualization Module

Presents an interactive, map-centric interface where users can explore classified layers, KPIs, and trends for a project.

## 4.7 User & Access Management Module

Controls who can view, edit, or administer data and dashboard content, based on defined roles.

# 5. Data Requirements

The core data model consists of six entities: Project, Dataset, SpatialLayer, KPI, User, and AuditLog. Relationships are illustrated below.
Figure 2: Entity Relationship Overview

## 5.1 Data Dictionary


### Project


### Dataset


### SpatialLayer


### KPI


### User


### AuditLog


# 6. External Interface Requirements


## 6.1 User Interfaces

Web-based dashboard accessible via standard browsers; no desktop installation required.
Map view, KPI summary cards, and trend charts shall be visible on a single landing screen per project.
All interactive controls (layer toggles, filters, export) shall be reachable via mouse and keyboard.

## 6.2 Hardware Interfaces

No specialised hardware is required. Standard server infrastructure (virtual or physical) hosts all backend components; end users require only a standard workstation, laptop, or tablet with a modern browser.

## 6.3 Software Interfaces


## 6.4 Communication Interfaces

All client-server communication shall occur over HTTPS.
The API shall accept and return JSON; map services shall conform to OGC WMS/WFS standards.

# 7. Non-Functional Requirements


## 7.1 Performance


## 7.2 Security


## 7.3 Reliability & Availability


## 7.4 Scalability


## 7.5 Maintainability


## 7.6 Usability


## 7.7 Compliance & Data Governance


# 8. Use Cases

The following use cases describe the primary interactions supported by the system for the pilot sample project.

## UC-01 - Ingest a Classified Dataset


## UC-02 - Publish a Map Layer


## UC-03 - View Project Dashboard


## UC-04 - Compute Carbon Stock Estimate


## UC-05 - Review Validation Error Log


## UC-06 - Export Dashboard Report


## UC-07 - Manage User Roles


## UC-08 - Query Layer via WFS (External Client)


## UC-09 - Configure Analytical Parameters


## UC-10 - Onboard a New Sample Project


# 9. Acceptance Criteria

The pilot sample project implementation will be considered acceptance-ready when the following conditions are met:
At least one sample project's classified datasets (LULC, NDVI, biomass/carbon, plot boundaries) are ingested, validated, and stored in PostGIS with zero unresolved validation errors.
All four dataset types are published and independently viewable via WMS on the dashboard.
Project-level KPIs (total area, carbon stock estimate, dataset count) are computed and match manual spot-check calculations within an agreed tolerance.
The dashboard loads the sample project's map and KPI view within the performance targets defined in NFR-01 and NFR-02.
Role-based access is demonstrated: a Viewer-role account cannot upload or edit data; a GIS Associate account can.
The Team Lead has reviewed and signed off on the dashboard against this SRS and the companion Stage 1 SOP.

# 10. Appendix A: Requirements Traceability Matrix (Sample)

A representative subset of the full traceability matrix is shown below, linking key requirements to their originating use case and verification approach. The complete matrix will be maintained as a living spreadsheet during Stage 2.

# Appendix B: Approval


**Table 1**

| Document Title | Software Requirements Specification - dMRV Analytical Dashboard |
|---|---|
| Document Owner | Denish M, Junior GIS Associate |
| Reviewed By | Jibotosh, Team Lead |
| Version | 1.0 - Draft for Review |
| Date | 09 July 2026 |
| Classification | Internal - VNV Use Only |


**Table 2**

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0 | 09 July 2026 | Denish M | Initial draft for team review |


**Table 3**

| Term | Definition |
|---|---|
| dMRV | Digital Monitoring, Reporting and Verification |
| LULC | Land Use / Land Cover classification |
| NDVI | Normalized Difference Vegetation Index |
| GEDI L4A | Global Ecosystem Dynamics Investigation, Level 4A biomass product |
| SAR | Synthetic Aperture Radar (e.g. Sentinel-1) |
| PostGIS | Spatial database extension for PostgreSQL |
| WMS | Web Map Service - OGC standard for serving map images |
| WFS | Web Feature Service - OGC standard for serving vector feature data |
| KPI | Key Performance Indicator |
| CRS | Coordinate Reference System |
| API | Application Programming Interface |
| JWT | JSON Web Token, used for API authentication |
| RBAC | Role-Based Access Control |


**Table 4**

| User Class | Description | Technical Proficiency |
|---|---|---|
| Administrator | Manages users, roles, and system configuration | High - comfortable with system administration |
| GIS Associate | Uploads, validates, and publishes classified datasets | High - familiar with GIS tools and file formats |
| Analyst | Reviews and tunes analytical parameters (e.g. carbon conversion factors) | Medium-High - domain expert, moderate technical skill |
| Viewer | Views dashboards and exports reports; no editing rights | Low-Medium - general staff, minimal training assumed |
| External GIS Client | Third-party or partner system consuming WMS/WFS layers | High - technical system, not a human end user |


**Table 5**

| Component | Responsibility |
|---|---|
| API Gateway | Single entry point for the dashboard client; handles authentication, request routing, and rate limiting before forwarding to backend services |
| Ingestion Service | Accepts uploaded dataset files and stages them for validation |
| Validation Service | Checks geometry, CRS, and attribute completeness; writes error logs; publishes a "validated" event when a dataset passes |
| Analytics / KPI Service | Computes NDVI trends, biomass, and carbon stock estimates; listens for validation events and recomputes KPIs as needed |
| Map Publishing Service | Wraps GeoServer to publish validated layers as WMS/WFS endpoints |
| Dashboard BFF Service | Backend-for-frontend that aggregates KPI, metadata, and layer information into the shapes the React dashboard needs |
| Event Bus | Message queue carrying asynchronous jobs (validation, KPI recomputation) between services without tight coupling |
| PostGIS | Shared spatial database; each service accesses only the schema it owns |
| Object Storage | Holds raw uploaded files, generated exports, and database backups |


**Table 6**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-101 | The system shall accept dataset uploads in GeoJSON, Shapefile, GeoTIFF, and CSV formats. | High |
| FR-102 | The system shall validate that all polygon/line geometries are topologically valid (no self-intersections, no null geometries) before acceptance. | High |
| FR-103 | The system shall verify that the Coordinate Reference System (CRS) of an incoming dataset matches the project standard (EPSG:4326) and shall reproject automatically when a supported alternate CRS is detected. | High |
| FR-104 | The system shall check that mandatory attribute fields (Plot ID, classification label, processing date) are present and non-null. | High |
| FR-105 | The system shall detect and flag duplicate records based on a configurable unique key (e.g. Plot ID + date). | Medium |
| FR-106 | The system shall generate a validation error log for every ingestion attempt, listing rejected records and the reason for rejection. | High |
| FR-107 | The system shall allow a GIS Associate to review the validation error log and manually correct or discard flagged records before re-submission. | Medium |
| FR-108 | The system shall record dataset metadata (source, classification method, accuracy/confidence score, date of processing) at the time of ingestion. | Medium |


**Table 7**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-201 | The system shall store validated vector datasets as PostGIS geometry-typed tables. | High |
| FR-202 | The system shall store raster datasets (e.g. NDVI rasters) using PostGIS raster types or a linked object store reference. | Medium |
| FR-203 | The system shall support spatial queries including area calculation, intersection, buffer, and nearest-neighbour search. | High |
| FR-204 | The system shall maintain a spatial index on every geometry column to ensure query performance at scale. | High |
| FR-205 | The system shall version each dataset load with a timestamp and a load-batch identifier to support rollback. | Medium |
| FR-206 | The system shall enforce referential integrity between a Dataset record and its parent Project record. | Medium |
| FR-207 | The system shall support incremental loading of new dataset versions without requiring a full table rebuild. | Low |


**Table 8**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-301 | The system shall publish every validated spatial layer as a Web Map Service (WMS) endpoint for map rendering. | High |
| FR-302 | The system shall publish every validated spatial layer as a Web Feature Service (WFS) endpoint for feature-level queries. | High |
| FR-303 | The system shall allow layer styling (colour ramps, classification breaks) to be configured per layer without modifying source data. | Medium |
| FR-304 | The system shall support layer grouping so related layers (e.g. all LULC years for one project) can be toggled together. | Low |
| FR-305 | The system shall provide a capabilities document (GetCapabilities) listing all published layers and their metadata. | Medium |
| FR-306 | The system shall restrict WFS write operations (transactional WFS) to authorised roles only. | High |


**Table 9**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-401 | The system shall compute NDVI time-series summary statistics (mean, min, max, trend slope) per project per time period. | High |
| FR-402 | The system shall calculate above-ground biomass estimates using GEDI L4A, Dynamic World, and Sentinel-1 SAR-derived inputs. | High |
| FR-403 | The system shall convert biomass estimates to carbon stock figures using an approved, configurable conversion factor. | High |
| FR-404 | The system shall compute LULC class-wise area totals and year-on-year change statistics. | High |
| FR-405 | The system shall flag statistically significant anomalies in NDVI or biomass trends for manual review. | Medium |
| FR-406 | The system shall allow the conversion factors and analytical parameters to be updated by an authorised analyst without a code deployment. | Low |


**Table 10**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-501 | The system shall expose a REST API endpoint returning project-level KPIs (total area, carbon stock, dataset count) in JSON format. | High |
| FR-502 | The system shall expose an endpoint returning dataset-level metadata for catalog and search purposes. | Medium |
| FR-503 | The system shall support filtering of KPI results by project, date range, and dataset type. | Medium |
| FR-504 | The system shall provide an OpenAPI/Swagger specification describing all available endpoints. | Medium |
| FR-505 | The system shall support export of KPI results as CSV and PDF. | Medium |
| FR-506 | The system shall authenticate all API requests using a token-based scheme (JWT). | High |


**Table 11**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-601 | The system shall render published WMS layers on an interactive Leaflet map with pan, zoom, and layer-toggle controls. | High |
| FR-602 | The system shall display KPI summary cards (total area, carbon stock, number of datasets) on the dashboard landing view. | High |
| FR-603 | The system shall render NDVI and biomass trend charts using time-series data from the API. | High |
| FR-604 | The system shall allow users to click a map feature and view its attribute data in a side panel. | Medium |
| FR-605 | The system shall provide a search/filter control to locate a project or plot by name or ID. | Medium |
| FR-606 | The system shall allow authorised users to export the current dashboard view as a PDF report. | Low |
| FR-607 | The system shall be responsive and usable on both desktop and tablet screen sizes. | Medium |


**Table 12**

| ID | Requirement Description | Priority |
|---|---|---|
| FR-701 | The system shall support at minimum three roles: Administrator, GIS Associate, and Viewer. | High |
| FR-702 | The system shall restrict dataset upload and validation actions to Administrator and GIS Associate roles. | High |
| FR-703 | The system shall allow Viewer-role users read-only access to published dashboards. | High |
| FR-704 | The system shall log all create, update, and delete actions with user identity and timestamp (audit trail). | Medium |
| FR-705 | The system shall enforce authentication via username/password with the option to add OAuth2/SSO in a later phase. | Medium |


**Table 13**

| Field | Type | Description |
|---|---|---|
| project_id | UUID | Primary key |
| name | VARCHAR(150) | Project display name |
| region | VARCHAR(100) | Geographic region / state |
| start_date | DATE | Project start date |
| status | VARCHAR(20) | Active / Archived |


**Table 14**

| Field | Type | Description |
|---|---|---|
| dataset_id | UUID | Primary key |
| project_id | UUID | Foreign key to Project |
| type | VARCHAR(30) | LULC / NDVI / Biomass / Boundary |
| source | VARCHAR(150) | Sentinel-2, GEDI L4A, field survey, etc. |
| accuracy_score | DECIMAL(5,2) | Classification accuracy / Kappa, where applicable |
| date_processed | DATE | Date the dataset was classified/processed |


**Table 15**

| Field | Type | Description |
|---|---|---|
| layer_id | UUID | Primary key |
| dataset_id | UUID | Foreign key to Dataset |
| geom | GEOMETRY | PostGIS geometry column |
| crs | VARCHAR(20) | Coordinate reference system (e.g. EPSG:4326) |
| wms_url | TEXT | Published WMS endpoint |
| wfs_url | TEXT | Published WFS endpoint |


**Table 16**

| Field | Type | Description |
|---|---|---|
| kpi_id | UUID | Primary key |
| dataset_id | UUID | Foreign key to Dataset |
| metric_name | VARCHAR(60) | e.g. carbon_stock_tCO2e, ndvi_mean |
| value | DECIMAL(14,4) | Computed metric value |
| unit | VARCHAR(20) | Unit of measure |
| computed_at | TIMESTAMP | Computation timestamp |


**Table 17**

| Field | Type | Description |
|---|---|---|
| user_id | UUID | Primary key |
| name | VARCHAR(100) | Full name |
| role | VARCHAR(20) | Administrator / GIS Associate / Viewer |
| email | VARCHAR(150) | Login email |


**Table 18**

| Field | Type | Description |
|---|---|---|
| log_id | UUID | Primary key |
| user_id | UUID | Foreign key to User |
| action | VARCHAR(50) | Action performed |
| timestamp | TIMESTAMP | When the action occurred |


**Table 19**

| Interface | Description |
|---|---|
| Google Earth Engine | Source of NDVI, GEDI L4A, Dynamic World, and Sentinel-1 SAR exports consumed during ingestion |
| QGIS | Used by GIS Associates to inspect/prepare datasets before upload; not directly integrated |
| GeoServer WMS/WFS | Standard OGC interface consumed by the dashboard and any external GIS client |
| REST/JSON API | FastAPI-served endpoints consumed by the dashboard frontend |


**Table 20**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-01 | Dashboard map layers shall render within 3 seconds for a project area up to 5,000 hectares under normal network conditions. | High |
| NFR-02 | KPI API endpoints shall respond within 1 second for pre-computed metrics. | High |
| NFR-03 | Dataset validation for a batch of up to 5,000 features shall complete within 5 minutes. | Medium |


**Table 21**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-04 | All API traffic shall be encrypted in transit using TLS 1.2 or higher. | High |
| NFR-05 | Passwords shall be stored using a salted one-way hash (e.g. bcrypt); plaintext storage is prohibited. | High |
| NFR-06 | Role-based access control shall be enforced at both the API and database layer. | High |
| NFR-07 | The system shall lock an account after 5 consecutive failed login attempts for 15 minutes. | Medium |


**Table 22**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-08 | The system shall target 99% uptime during business hours for the pilot deployment. | Medium |
| NFR-09 | The system shall retain a rolling 30-day backup of the spatial database. | High |
| NFR-10 | A failed dataset ingestion shall not corrupt or partially commit data to the production database. | High |


**Table 23**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-11 | The database schema shall support at minimum 50 concurrent projects without redesign. | Medium |
| NFR-12 | The architecture shall allow horizontal scaling of the API layer independent of the database layer. | Low |


**Table 24**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-13 | All source code shall be version-controlled in Git with descriptive commit messages. | High |
| NFR-14 | Configuration values (conversion factors, thresholds) shall be externalised from code. | Medium |
| NFR-15 | The system shall include automated tests covering core validation and KPI computation logic. | Medium |


**Table 25**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-16 | A first-time Viewer-role user shall be able to locate a project and view its KPIs without training, within 3 clicks. | Medium |
| NFR-17 | The dashboard shall present error and loading states clearly rather than a blank screen. | Medium |


**Table 26**

| ID | Requirement Description | Priority |
|---|---|---|
| NFR-18 | Dataset metadata shall record data provenance sufficient to support external MRV/verification review. | High |
| NFR-19 | The system shall not permanently delete a dataset; deletions shall be soft-deletes retaining an audit record. | Medium |


**Table 27**

| Actor | GIS Associate |
|---|---|
| Precondition | A classified dataset (e.g. LULC output) has been produced and is ready for upload. |
| Main Flow | 1. GIS Associate selects the target project and uploads the dataset file.<br>2. System validates geometry, CRS, and required attributes.<br>3. System displays a validation summary (accepted / rejected record counts).<br>4. GIS Associate reviews rejected records and corrects or discards them.<br>5. System loads accepted records into the spatial database and logs the batch. |
| Postcondition | Dataset is available in the spatial database with a recorded validation log. |


**Table 28**

| Actor | GIS Associate |
|---|---|
| Precondition | A dataset has been successfully loaded into the spatial database. |
| Main Flow | 1. GIS Associate selects the dataset to publish.<br>2. System generates a WMS and WFS endpoint for the layer.<br>3. GIS Associate configures layer styling (colour, classification breaks).<br>4. System confirms the layer is live and returns the capabilities URL. |
| Postcondition | Layer is publicly queryable via WMS/WFS and appears as an option on the dashboard. |


**Table 29**

| Actor | Viewer |
|---|---|
| Precondition | At least one project has published layers and computed KPIs. |
| Main Flow | 1. Viewer logs in and selects a project from the list.<br>2. System loads KPI summary cards and the interactive map.<br>3. Viewer toggles layers and inspects features on the map. |
| Postcondition | Viewer has an up-to-date view of the project's classified data and KPIs. |


**Table 30**

| Actor | System (scheduled) / Analyst |
|---|---|
| Precondition | Biomass-relevant input layers (GEDI L4A, Dynamic World, Sentinel-1 SAR) are available for the project. |
| Main Flow | 1. Analyst triggers (or scheduler initiates) the biomass/carbon computation job.<br>2. System retrieves the relevant input layers from the spatial database.<br>3. System applies the biomass-to-carbon conversion factor.<br>4. System stores the resulting KPI and timestamps the computation. |
| Postcondition | Updated carbon stock KPI is available via the API and dashboard. |


**Table 31**

| Actor | GIS Associate |
|---|---|
| Precondition | A dataset ingestion attempt has produced one or more rejected records. |
| Main Flow | 1. GIS Associate opens the error log for the ingestion batch.<br>2. System lists each rejected record with its rejection reason.<br>3. GIS Associate corrects the source file or discards invalid records.<br>4. GIS Associate re-submits the corrected dataset (returns to UC-01). |
| Postcondition | Rejected records are resolved or intentionally excluded, with the decision logged. |


**Table 32**

| Actor | Viewer / GIS Associate |
|---|---|
| Precondition | A project dashboard with KPIs and map view is loaded. |
| Main Flow | 1. User selects Export from the dashboard toolbar.<br>2. System renders the current KPI and map state to a PDF.<br>3. System returns the generated file for download. |
| Postcondition | A shareable PDF snapshot of the dashboard is produced. |


**Table 33**

| Actor | Administrator |
|---|---|
| Precondition | Administrator is authenticated with administrator privileges. |
| Main Flow | 1. Administrator opens the user management screen.<br>2. Administrator adds, edits, or deactivates a user account.<br>3. Administrator assigns or changes the user's role.<br>4. System logs the change to the audit trail. |
| Postcondition | User accounts and roles are up to date and auditable. |


**Table 34**

| Actor | External GIS Client |
|---|---|
| Precondition | A layer has been published and its WFS endpoint is available. |
| Main Flow | 1. External client sends a GetFeature request to the WFS endpoint.<br>2. System authenticates the request (if required) and executes the spatial query.<br>3. System returns matching features in the requested format (GML/GeoJSON). |
| Postcondition | External system receives the requested feature data without duplicating VNV's dataset. |


**Table 35**

| Actor | Analyst / Administrator |
|---|---|
| Precondition | A new conversion factor or threshold needs to be applied. |
| Main Flow | 1. Analyst opens the analytical configuration screen.<br>2. Analyst updates the relevant parameter (e.g. biomass-to-carbon factor).<br>3. System validates the input range and saves the new configuration.<br>4. System flags affected KPIs for recomputation. |
| Postcondition | Future KPI computations use the updated parameter; change is logged. |


**Table 36**

| Actor | GIS Associate / Team Lead |
|---|---|
| Precondition | A new project has been approved for dashboard onboarding. |
| Main Flow | 1. Team Lead creates the project record with name, region, and scope.<br>2. GIS Associate ingests the project's initial classified datasets (UC-01).<br>3. GIS Associate publishes the resulting layers (UC-02).<br>4. System computes initial KPIs and the dashboard becomes available (UC-03). |
| Postcondition | New project is fully onboarded and visible to authorised Viewers. |


**Table 37**

| Requirement ID | Use Case | Verification Approach |
|---|---|---|
| FR-101 | UC-01 | Upload accepts GeoJSON/Shapefile/GeoTIFF/CSV |
| FR-102 | UC-01 | Invalid geometries rejected with reason |
| FR-103 | UC-01 | CRS mismatch auto-reprojected or flagged |
| FR-106 | UC-05 | Validation error log generated and reviewable |
| FR-301 | UC-02 | Layer available as WMS after publish |
| FR-302 | UC-08 | External client can query via WFS |
| FR-401 | UC-04 | NDVI summary stats computed per period |
| FR-403 | UC-04 | Carbon stock derived from biomass estimate |
| FR-501 | UC-03 | Project KPI endpoint returns expected fields |
| FR-601 | UC-03 | Map renders published WMS layer |
| FR-701 | UC-07 | Role assignment restricts/permits actions correctly |
| FR-704 | UC-07 | Audit trail records role change |
| NFR-04 | UC-08 | External WFS call occurs over TLS |
| NFR-06 | UC-07 | RBAC enforced at API and DB layer |


**Table 38**

| Prepared By | Reviewed By | Date |
|---|---|---|
| Denish M | Jibotosh | 09 July 2026 |
