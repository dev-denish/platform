VALUE NETWORK VENTURES (VNV)
GIS & Carbon Analytics Division

# Technology Stack Guide

dMRV Analytical Dashboard
25 Tools - Purpose, Rationale, Alternatives, and Tradeoffs
Revision History

# Table of Contents

(Right-click the table above and choose "Update Field" in Microsoft Word to populate section page numbers.)

# 1. Introduction

This guide documents every tool, library, and platform used across the dMRV Analytical Dashboard, and why it was chosen over the realistic alternatives. It is written for two audiences at once: a reviewer deciding whether the stack is sound, and a future contributor who wants to understand a tradeoff without having to ask the person who made it.
Every entry follows the same structure: what the tool does here, why it was picked, what else was considered, its advantages and limitations as they apply to this project specifically (not a generic pros/cons list), licensing, community health, and how it scales as the project grows from a single pilot project toward Stage 3.

## 1.1 Selection Principles

Open source only, per VNV's technology policy for this pilot - no per-seat or per-request licensing cost.
Reuse what the team already knows - several tools here carry over directly from the existing Automated GIS Satellite Platform project, which lowers the risk of the two-week Stage 2 build.
Prefer the tool that solves the specific problem in front of it over the most powerful or most popular tool in its category - several "alternatives considered" entries below are objectively more capable, just not for this project's actual requirements.

# 2. Frontend & Visualization


## React


## Leaflet


## Chart.js


## Tailwind CSS


# 3. Backend & API


## FastAPI


## Python


## JWT (JSON Web Tokens)


# 4. Database & Map Serving


## PostgreSQL


## PostGIS


## GeoServer


# 5. GIS Processing Libraries


## GeoPandas


## Shapely


## Rasterio


## GDAL


## Fiona


## PyProj


## QGIS


# 6. Remote Sensing Data Sources


## Google Earth Engine


## Sentinel-1 / Sentinel-2


## GEDI L4A


# 7. Deployment & Infrastructure


## Docker


## Docker Compose


## Kubernetes


## Nginx


## Git


# 8. Full Stack at a Glance

A single reference table of all 25 tools, grouped by category, for quick lookup without paging through the detailed entries above.

## Frontend & Visualization


## Backend & API


## Database & Map Serving


## GIS Processing Libraries


## Remote Sensing Data Sources


## Deployment & Infrastructure


**Table 1**

| Field | Detail |
|---|---|
| Document Title | Technology Stack Guide - dMRV Analytical Dashboard |
| Document Owner | Denish M, Junior GIS Associate |
| Reviewed By | Jibotosh, Team Lead |
| Companion Documents | SRS_dMRV_Dashboard.docx, SOP_dMRV_Dashboard_Full.docx, Solution_Architecture_Document.docx |
| Version | 1.0 - Draft for Review |
| Date | 09 July 2026 |
| Classification | Internal - VNV Use Only |


**Table 2**

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0 | 09 July 2026 | Denish M | Initial guide covering all 25 tools used across the SRS, SOP, and Solution Architecture Document |


**Table 3**

| Purpose | Component-based JavaScript framework powering the dashboard's UI. |
|---|---|
| Why Selected | The dashboard needs a landing view, map view, and chart view that all share state (selected project, active layers). React's component model maps directly onto that without extra scaffolding, and it's what the team already knows from the Automated GIS Satellite Platform project. |
| Alternatives Considered | Vue.js (smaller learning curve, less relevant team experience), Svelte (less GIS-library ecosystem support), plain server-rendered HTML (would not support the interactive map/chart state the dashboard needs). |
| Advantages | Huge ecosystem of GIS-compatible components (react-leaflet, etc.); large hiring pool; mature tooling. |
| Limitations | More boilerplate than a meta-framework like Next.js for simple pages; requires deliberate state management as the app grows. |
| Licensing | MIT - free for commercial use. |
| Community Support | Very large; long-term maintenance risk is low. |
| Scalability | Scales well for a single-page dashboard; component splitting keeps bundle size manageable as views are added. |


**Table 4**

| Purpose | Renders the interactive map: WMS layers, feature clicks, pan/zoom. |
|---|---|
| Why Selected | Leaflet speaks WMS/WFS natively, which is exactly what GeoServer publishes. It is lighter-weight than a full GIS-in-browser stack (e.g. OpenLayers) for what this dashboard actually needs: toggle layers, click a feature, show a popup. |
| Alternatives Considered | OpenLayers (more powerful, notably heavier and more complex API for this use case), Mapbox GL JS (vector-tile focused, and introduces a paid API dependency the project is trying to avoid). |
| Advantages | Simple API, small footprint, wide plugin ecosystem, well-documented WMS/WFS integration. |
| Limitations | Less suited to very large vector datasets rendered client-side (not a concern here since layers are served as WMS raster tiles, not raw geometry). |
| Licensing | BSD-2-Clause - free for commercial use. |
| Community Support | Mature and stable; slower feature pace than newer libraries, but that stability is a plus for a production dashboard. |
| Scalability | Handles many WMS layers without client-side performance issues, since rendering happens server-side in GeoServer. |


**Table 5**

| Purpose | Renders NDVI trend lines and class-wise area bar charts. |
|---|---|
| Why Selected | The dashboard's charting needs (line and bar charts, tied to time-series and category data) are exactly Chart.js's core use case, without pulling in a heavier charting library built for use cases the dashboard doesn't have (3D, network graphs, etc.). |
| Alternatives Considered | D3.js (far more powerful and far more code to hand-write the same chart), Recharts (React-specific wrapper, similar capability, smaller community than Chart.js). |
| Advantages | Minimal setup for standard chart types; good default styling; responsive out of the box. |
| Limitations | Less flexible than D3 for a fully custom visualization, not a concern for standard trend/bar charts. |
| Licensing | MIT - free for commercial use. |
| Community Support | Large and active. |
| Scalability | Fine for the KPI/trend chart volumes in scope; would need a different tool only if the dashboard grew highly custom visualization requirements. |


**Table 6**

| Purpose | Utility-first styling for dashboard layout and components. |
|---|---|
| Why Selected | Keeps styling co-located with components instead of a separate growing stylesheet, which matters when several people may touch dashboard UI code over Stage 3. |
| Alternatives Considered | Plain CSS/SASS (more flexible, slower to keep consistent across a team), Bootstrap (faster initial build, harder to make the dashboard look distinct rather than generic). |
| Advantages | Consistent spacing/color scale enforced by the framework itself; fast to build with once the utility classes are familiar. |
| Limitations | Class-heavy markup can look noisy; requires a build step (already present via the React toolchain, so no added cost here). |
| Licensing | MIT - free for commercial use. |
| Community Support | Very large and actively maintained. |
| Scalability | No scalability concern - it is a build-time tool, not a runtime dependency. |


**Table 7**

| Purpose | Serves the REST API consumed by the dashboard (KPIs, metadata, auth). |
|---|---|
| Why Selected | FastAPI is Python-native, which matters because the same language is used for GeoPandas/Shapely validation logic - a developer can move between the API layer and the GIS processing code without a language switch. It also generates OpenAPI documentation automatically (FR-504 in the SRS). |
| Alternatives Considered | Flask (simpler, but no built-in async support or automatic schema validation), Django REST Framework (heavier than needed for a service-oriented backend), Node/Express (would split the stack across two languages for no clear benefit). |
| Advantages | Async support for I/O-bound work (e.g. calling GeoServer's REST API); automatic request validation via Pydantic; built-in Swagger UI. |
| Limitations | Younger ecosystem than Flask/Django, though now well past the point of being a risky choice. |
| Licensing | MIT - free for commercial use. |
| Community Support | Fast-growing, strong documentation, active maintenance. |
| Scalability | Async design suits the microservice-per-responsibility approach; each service scales independently per the deployment architecture. |


**Table 8**

| Purpose | Primary language for backend services and GIS data processing. |
|---|---|
| Why Selected | Python is the de facto language for the GIS/remote-sensing ecosystem (GeoPandas, Rasterio, GDAL bindings, Earth Engine's Python API). Using one language across ingestion, validation, analytics, and API layers avoids maintaining parallel logic in two languages. |
| Alternatives Considered | Java/Scala (stronger typing, much smaller GIS library ecosystem), Go (excellent for lightweight services, weak GIS tooling). |
| Advantages | Direct access to the entire scientific Python and geospatial ecosystem; large hiring pool; fast to prototype and iterate. |
| Limitations | Slower raw execution than compiled languages for CPU-bound work; mitigated here since the heavy lifting (geometry ops, raster ops) happens in C-backed libraries (GDAL, Shapely) rather than pure Python loops. |
| Licensing | PSF License - free for commercial use. |
| Community Support | Enormous, especially in the geospatial and data science space. |
| Scalability | Each service scales horizontally as a container; language choice does not constrain that. |


**Table 9**

| Purpose | Stateless authentication token format used across the API Gateway and services. |
|---|---|
| Why Selected | JWTs let the API Gateway validate a request without a database round-trip on every call, and let backend services trust a signed token's claims (role, user ID) without re-authenticating (FR-506, NFR-05). |
| Alternatives Considered | Session cookies with server-side session store (adds a stateful dependency the microservice architecture is specifically trying to avoid), OAuth2 full flow (appropriate for third-party access, more than the pilot needs for internal users). |
| Advantages | Stateless, so any service replica can validate a token without shared session storage; standard libraries exist in every language the stack uses. |
| Limitations | Revoking a single token before expiry requires an extra mechanism (e.g. a blocklist) - acceptable given the short expiry window used here. |
| Licensing | Open standard (RFC 7519); library-dependent, generally MIT/Apache-2.0. |
| Community Support | Extremely widely adopted; not tied to a single vendor. |
| Scalability | Stateless by design - scales with the number of service replicas without additional session infrastructure. |


**Table 10**

| Purpose | Relational database engine underlying the spatial database. |
|---|---|
| Why Selected | PostgreSQL is the database PostGIS extends, and its transaction guarantees are exactly what Section 8 of the SOP relies on for all-or-nothing batch loads. |
| Alternatives Considered | MySQL (weaker spatial support historically), a NoSQL document store (would lose the relational integrity between Project/Dataset/KPI that the ER model depends on). |
| Advantages | Mature, ACID-compliant, excellent indexing (including GIST for spatial queries), strong tooling for backup/replication. |
| Limitations | Vertical scaling has limits eventually; not a pilot-stage concern, and read replicas (per the physical architecture) address read-heavy load first. |
| Licensing | PostgreSQL License (permissive, similar to MIT/BSD) - free for commercial use. |
| Community Support | One of the largest and most active open-source database communities. |
| Scalability | Read replicas for query load; vertical scaling and partitioning available as data volume grows in Stage 3. |


**Table 11**

| Purpose | Adds spatial data types, indexing, and query functions to PostgreSQL. |
|---|---|
| Why Selected | Every dataset in this system is fundamentally geometric. PostGIS lets the database itself answer "what area does this polygon cover" or "which plots intersect this boundary" instead of pulling all rows into application code to compute it. |
| Alternatives Considered | Doing spatial operations entirely in application code with Shapely (works for validation but would be far slower and more memory-intensive for database-scale queries), a dedicated spatial database like Oracle Spatial (proprietary, no clear advantage for this scale). |
| Advantages | Industry-standard spatial SQL functions; GIST indexing keeps map queries fast even as data grows; directly compatible with GeoServer. |
| Limitations | Requires understanding spatial indexing to use well - a naive query on an unindexed geometry column is slow; mitigated by the mandatory index step in the SOP (Section 5.3). |
| Licensing | GPL v2 - free for commercial use, including as a service backend (no distribution of PostGIS itself to end users). |
| Community Support | Large, GIS-specific, well-integrated with the broader open-source geospatial stack (GDAL, GeoServer, QGIS). |
| Scalability | Scales with proper indexing and read replicas; partitioning by project is a viable Stage 3 strategy if a single table grows very large. |


**Table 12**

| Purpose | Publishes PostGIS layers as WMS/WFS services. |
|---|---|
| Why Selected | GeoServer is the most widely deployed open-source map server with a REST API for programmatic layer publishing, which the Map Publishing Service scripts directly (SOP Section 5.4). |
| Alternatives Considered | MapServer (comparable capability, less convenient REST API for automated publishing), a custom tile-rendering service (significant engineering effort to replicate what GeoServer already does well). |
| Advantages | Standards-compliant WMS/WFS out of the box; REST API for automation; built-in styling (SLD) support; broad client compatibility (any GIS tool, not just this dashboard). |
| Limitations | Java-based and can be memory-hungry under heavy concurrent tile requests; addressed by giving it dedicated node resources in the deployment architecture. |
| Licensing | GPL v2 - free for commercial use as a service. |
| Community Support | Long-established in the GIS community; backed by an active open-source foundation (OSGeo). |
| Scalability | Can be scaled horizontally behind a load balancer; tile caching (e.g. GeoWebCache, built in) reduces repeated rendering cost. |


**Table 13**

| Purpose | Vector data handling in Python - reading, filtering, and validating GeoJSON/Shapefile data. |
|---|---|
| Why Selected | GeoPandas extends the familiar pandas DataFrame with geometry operations, so the same validation code that checks "is this field null" can also check "is this geometry valid" without switching libraries. |
| Alternatives Considered | Fiona + Shapely used directly without GeoPandas (more verbose for the same result), a fully custom parser (no reason to build this from scratch). |
| Advantages | Familiar DataFrame API; integrates directly with Shapely, Fiona, and PostGIS (via GeoAlchemy2); large existing codebase reuse from the Automated GIS Satellite Platform project. |
| Limitations | Can be memory-heavy for very large vector datasets loaded entirely in memory; not a concern at the pilot's per-batch scale (up to 5,000 features per NFR-03). |
| Licensing | BSD-3-Clause - free for commercial use. |
| Community Support | Core part of the Python geospatial stack; active maintenance. |
| Scalability | Sufficient for per-batch validation at pilot scale; very large datasets would be chunked or processed via Dask-GeoPandas in a future phase if needed. |


**Table 14**

| Purpose | Geometric operations - validity checks, buffering, intersection tests. |
|---|---|
| Why Selected | Shapely is what GeoPandas uses under the hood for geometry operations, and is also called directly in validation logic (VR-01, geometry validity) per the SOP's example code. |
| Alternatives Considered | JTS (Java Topology Suite - the library Shapely wraps; would mean dropping out of Python), hand-rolled geometry math (unnecessary and error-prone for solved problems like polygon validity). |
| Advantages | Mature, well-tested geometry engine; simple Pythonic API for otherwise complex computational geometry. |
| Limitations | Single-threaded by default for bulk operations; acceptable given the pilot's batch sizes. |
| Licensing | BSD-3-Clause - free for commercial use. |
| Community Support | Foundational library in the Python GIS ecosystem. |
| Scalability | Fine at pilot scale; CPU-bound geometry checks could be parallelized across batches in Stage 3 if needed. |


**Table 15**

| Purpose | Reads and writes raster data (NDVI, biomass rasters) and checks resolution/format. |
|---|---|
| Why Selected | NDVI and some biomass outputs arrive as raster (GeoTIFF), and Rasterio is the standard Python interface for reading pixel data and metadata without shelling out to GDAL command-line tools directly. |
| Alternatives Considered | Direct GDAL Python bindings (lower-level, more boilerplate for the same result), Pillow (not spatially aware - no CRS/geotransform support). |
| Advantages | Clean, Pythonic API over GDAL's raster capabilities; integrates with NumPy for pixel-level analysis (e.g. NDVI trend statistics). |
| Limitations | Large rasters need careful memory handling (windowed reads); addressed by reading in windows rather than loading full scenes where datasets are large. |
| Licensing | BSD-3-Clause - free for commercial use. |
| Community Support | Actively maintained, widely used in remote sensing workflows. |
| Scalability | Windowed/chunked reads keep memory bounded regardless of raster size. |


**Table 16**

| Purpose | Underlying format conversion and reprojection engine used by GeoPandas, Rasterio, and Fiona. |
|---|---|
| Why Selected | GDAL is the de facto standard translator layer for geospatial formats; almost every other tool in this stack (GeoPandas, Rasterio, QGIS, GeoServer) either wraps it or is compatible with it, which keeps format handling consistent end to end. |
| Alternatives Considered | Format-specific libraries per file type (would fragment format support and reprojection logic across many tools instead of one). |
| Advantages | Supports virtually every geospatial format in use; battle-tested reprojection and transformation logic (used for the automatic CRS reprojection in VR-02). |
| Limitations | C library with a sometimes unfriendly error-message style when used directly; mitigated by using it through GeoPandas/Rasterio's friendlier Python wrappers rather than raw GDAL calls. |
| Licensing | MIT-style X11/MIT License - free for commercial use. |
| Community Support | One of the most foundational and widely depended-upon libraries in all of GIS software. |
| Scalability | Not a scalability bottleneck; it processes data efficiently at the file level regardless of overall system scale. |


**Table 17**

| Purpose | Reads and writes vector file formats (GeoJSON, Shapefile) at a lower level than GeoPandas. |
|---|---|
| Why Selected | GeoPandas relies on Fiona for file I/O under the hood; understanding it directly is occasionally needed for edge-case formats or custom read/write logic outside GeoPandas's default behavior. |
| Alternatives Considered | OGR (GDAL's vector API, which Fiona wraps) called directly - more verbose for the same result. |
| Advantages | Thin, reliable wrapper over OGR; predictable behavior for format edge cases. |
| Limitations | Lower-level than most day-to-day code needs; used mostly indirectly via GeoPandas. |
| Licensing | BSD-3-Clause - free for commercial use. |
| Community Support | Stable, maintained as part of the core Python geospatial toolchain. |
| Scalability | Not a scaling concern - a thin I/O layer, not a processing bottleneck. |


**Table 18**

| Purpose | Coordinate reference system transformations. |
|---|---|
| Why Selected | CRS reprojection (VR-02: auto-reproject to EPSG:4326) needs a correct, authoritative transformation library rather than hand-written trigonometry - PyProj wraps the PROJ library that is the standard for this. |
| Alternatives Considered | Hand-implemented projection math (error-prone and unnecessary - this is a solved problem), relying solely on GDAL's built-in reprojection without PyProj's more Pythonic interface for one-off transforms. |
| Advantages | Authoritative, accurate CRS definitions and transformations; integrates directly with GeoPandas. |
| Limitations | None significant for this project's needs; a mature, narrow-purpose library. |
| Licensing | MIT - free for commercial use. |
| Community Support | Core dependency of the Python geospatial stack; stable and well-maintained. |
| Scalability | Transformation cost is per-geometry and negligible at this project's data volumes. |


**Table 19**

| Purpose | Desktop GIS tool used by GIS Associates to inspect and prepare datasets before upload. |
|---|---|
| Why Selected | QGIS is the standard free desktop GIS tool already used across VNV's GIS workflows (LULC classification, NDVI analysis) - datasets naturally pass through it before ever reaching this system. |
| Alternatives Considered | ArcGIS Desktop (proprietary, licensing cost VNV's technology policy is trying to avoid for this pilot), a custom web-based dataset preview tool (unnecessary - QGIS already does this well). |
| Advantages | Full-featured desktop GIS with strong format support, symbology tools, and a plugin ecosystem; no licensing cost. |
| Limitations | Desktop-only, not part of the automated pipeline itself - used for manual inspection, not integrated programmatically. |
| Licensing | GPL v2 - free for commercial use. |
| Community Support | Large, active, backed by OSGeo. |
| Scalability | Not applicable - a desktop tool used outside the automated pipeline. |


**Table 20**

| Purpose | Cloud platform providing access to satellite imagery and pre-built analysis-ready datasets (Sentinel, Dynamic World, GEDI). |
|---|---|
| Why Selected | Earth Engine avoids VNV needing to store and process raw petabyte-scale satellite archives itself - the heavy imagery processing happens on Google's infrastructure, and only the derived, already-classified outputs enter this system. |
| Alternatives Considered | Downloading and processing raw Sentinel/Landsat data locally (would require significant storage and compute infrastructure VNV does not need to own), a commercial imagery provider (added cost with no clear benefit for this use case). |
| Advantages | Free tier sufficient for this project's data volumes; access to Dynamic World, GEDI L4A, and Sentinel-1/2 without separate licensing arrangements; Python API integrates with the existing GIS workflow. |
| Limitations | Usage quotas apply on the free tier; a dependency on a third-party platform's availability and terms of service. |
| Licensing | Free for research and some commercial uses under Google's terms; VNV should confirm current terms apply to its specific commercial use case. |
| Community Support | Large user base in remote sensing and environmental monitoring. |
| Scalability | Scales well since the heavy processing is offloaded to Google's infrastructure rather than VNV's own systems. |


**Table 21**

| Purpose | Source SAR (Sentinel-1) and optical (Sentinel-2) satellite imagery underlying LULC classification and biomass estimation. |
|---|---|
| Why Selected | Sentinel data is free, globally available, and already the basis of VNV's existing classification workflows - there is no reason to introduce a paid imagery source when Sentinel's resolution and revisit frequency meet the project's needs. |
| Alternatives Considered | Commercial high-resolution imagery (Planet, Maxar) - higher resolution but at a licensing cost not justified for the current classification accuracy requirements. |
| Advantages | Free, global, frequent revisit (5-12 days); SAR (Sentinel-1) works through cloud cover, which optical alone cannot. |
| Limitations | 10m resolution may be coarse for very small or narrow plot boundaries; accepted as a known constraint of the current classification approach. |
| Licensing | Free and open (Copernicus programme, ESA). |
| Community Support | Extremely widely used in the remote sensing and environmental science community. |
| Scalability | Not applicable in the traditional sense - a data source, not a processing system. |


**Table 22**

| Purpose | Spaceborne lidar-derived biomass product used as a primary input to carbon stock estimation. |
|---|---|
| Why Selected | GEDI L4A provides a scientifically validated aboveground biomass density estimate, which is the foundation the carbon stock calculation (SOP Section 5.5) is built on rather than deriving biomass from optical imagery alone. |
| Alternatives Considered | Field-measured biomass plots only (far more accurate per plot, but not feasible at the spatial coverage this project needs), optical-only biomass proxies (weaker correlation with actual biomass than lidar-derived estimates). |
| Advantages | Peer-reviewed, widely used in carbon monitoring research; free and public. |
| Limitations | Footprint-based sampling (not continuous coverage) requires combination with Dynamic World/Sentinel-1 for full-area estimates, which is exactly how the current computation combines them. |
| Licensing | Free and open (NASA/University of Maryland). |
| Community Support | Established in the carbon monitoring and forestry research community. |
| Scalability | Not applicable - a data source, not a processing system. |


**Table 23**

| Purpose | Packages each microservice into a portable, consistent container image. |
|---|---|
| Why Selected | Every service in the architecture (Section 4, SRS) is designed to be independently deployable; Docker is what makes "the same image runs identically on a laptop and in production" true rather than aspirational. |
| Alternatives Considered | Virtual machines per service (far heavier, slower to start, harder to version), no containerization at all (would reintroduce "works on my machine" environment drift the whole architecture is trying to avoid). |
| Advantages | Lightweight compared to full VMs; consistent environment from local development through to Kubernetes; huge ecosystem of base images. |
| Limitations | Adds a layer of operational knowledge required (image builds, registries) compared to running code directly on a host - accepted as a standard, well-understood cost. |
| Licensing | Apache 2.0 (Docker Engine) - free for commercial use. |
| Community Support | Industry-standard; effectively universal at this point. |
| Scalability | Directly enables the horizontal scaling described in the deployment architecture. |


**Table 24**

| Purpose | Runs the full multi-service stack locally and for the pilot deployment. |
|---|---|
| Why Selected | For a single pilot server, Compose gives the same container images used later in Kubernetes without the operational overhead of running a full cluster before it's needed (SOP Section 4.2). |
| Alternatives Considered | Running each service manually without orchestration (error-prone, no dependency ordering), jumping straight to Kubernetes for the pilot (unnecessary operational complexity for a two-week, single-host deployment). |
| Advantages | Simple, declarative multi-container setup; identical images to what Kubernetes will run later, so there is no re-work moving to Stage 3. |
| Limitations | Single-host only - no built-in scaling or failover, which is exactly why Kubernetes is the Stage 3 target rather than the permanent solution. |
| Licensing | Apache 2.0 - free for commercial use. |
| Community Support | Maintained by Docker; extremely widely used for local/small deployments. |
| Scalability | Intentionally limited to single-host; scaling is Kubernetes' job once the project grows past the pilot. |


**Table 25**

| Purpose | Container orchestration platform targeted for Stage 3 production deployment. |
|---|---|
| Why Selected | Once multiple projects are onboarded, ingestion and analytics load will grow independently of dashboard traffic (SRS Section 3.2) - Kubernetes is what lets each service scale on its own schedule rather than scaling the whole application together. |
| Alternatives Considered | Docker Swarm (simpler, smaller ecosystem, less operational tooling available), a fully managed serverless platform (less control over the long-running, stateful-adjacent workloads like GeoServer). |
| Advantages | Industry-standard orchestration; handles restarts, scaling, and service discovery automatically; the same container images used in Compose move over unchanged. |
| Limitations | Meaningful operational complexity and learning curve; justified specifically because the project's growth trajectory (Stage 3) needs the scaling behavior it provides. |
| Licensing | Apache 2.0 - free for commercial use. |
| Community Support | The dominant container orchestration platform; vast ecosystem and hiring pool. |
| Scalability | This is precisely what it is chosen for - horizontal, per-service scaling as load grows. |


**Table 26**

| Purpose | Reverse proxy / ingress layer in front of the API Gateway. |
|---|---|
| Why Selected | Nginx is a stable, well-understood layer for TLS termination and request routing at the cluster edge (Section 8, Security Architecture), sitting in front of the API Gateway rather than exposing it directly. |
| Alternatives Considered | Traefik (more Kubernetes-native, a reasonable alternative for Stage 3), exposing the API Gateway directly without a reverse proxy (loses a layer of defense and standard TLS handling). |
| Advantages | Extremely well-documented, battle-tested, low resource footprint. |
| Limitations | Configuration is less "Kubernetes-native" than newer ingress controllers like Traefik; acceptable given the team's existing familiarity with Nginx. |
| Licensing | BSD-2-Clause (open-source edition) - free for commercial use. |
| Community Support | One of the most widely deployed web servers/proxies in existence. |
| Scalability | Runs as a lightweight, horizontally replicable layer; not a bottleneck at this project's scale. |


**Table 27**

| Purpose | Version control for all service code, infrastructure configuration, and documentation. |
|---|---|
| Why Selected | Every change - code, configuration, even this document set - needs a traceable history, which is a baseline expectation (SOP Section 10) rather than a differentiator. |
| Alternatives Considered | No practical alternative for source control at this point; the only real decision is which hosting platform (GitHub, GitLab, etc.), which is separate from Git itself. |
| Advantages | Distributed, branching model supports the feature-branch workflow described in the SOP; universal tooling support. |
| Limitations | None material for this project's needs. |
| Licensing | GPL v2 - free for commercial use. |
| Community Support | Universal in professional software development. |
| Scalability | Not applicable in the system-scaling sense; scales fine with repository size management practices (e.g. not committing large binaries). |


**Table 28**

| Tool | One-Line Purpose |
|---|---|
| React | Component-based JavaScript framework powering the dashboard's UI. |
| Leaflet | Renders the interactive map: WMS layers, feature clicks, pan/zoom. |
| Chart.js | Renders NDVI trend lines and class-wise area bar charts. |
| Tailwind CSS | Utility-first styling for dashboard layout and components. |


**Table 29**

| Tool | One-Line Purpose |
|---|---|
| FastAPI | Serves the REST API consumed by the dashboard (KPIs, metadata, auth). |
| Python | Primary language for backend services and GIS data processing. |
| JWT (JSON Web Tokens) | Stateless authentication token format used across the API Gateway and services. |


**Table 30**

| Tool | One-Line Purpose |
|---|---|
| PostgreSQL | Relational database engine underlying the spatial database. |
| PostGIS | Adds spatial data types, indexing, and query functions to PostgreSQL. |
| GeoServer | Publishes PostGIS layers as WMS/WFS services. |


**Table 31**

| Tool | One-Line Purpose |
|---|---|
| GeoPandas | Vector data handling in Python - reading, filtering, and validating GeoJSON/Shapefile data. |
| Shapely | Geometric operations - validity checks, buffering, intersection tests. |
| Rasterio | Reads and writes raster data (NDVI, biomass rasters) and checks resolution/format. |
| GDAL | Underlying format conversion and reprojection engine used by GeoPandas, Rasterio, and Fiona. |
| Fiona | Reads and writes vector file formats (GeoJSON, Shapefile) at a lower level than GeoPandas. |
| PyProj | Coordinate reference system transformations. |
| QGIS | Desktop GIS tool used by GIS Associates to inspect and prepare datasets before upload. |


**Table 32**

| Tool | One-Line Purpose |
|---|---|
| Google Earth Engine | Cloud platform providing access to satellite imagery and pre-built analysis-ready datasets (Sentinel, Dynamic World, GEDI). |
| Sentinel-1 / Sentinel-2 | Source SAR (Sentinel-1) and optical (Sentinel-2) satellite imagery underlying LULC classification and biomass estimation. |
| GEDI L4A | Spaceborne lidar-derived biomass product used as a primary input to carbon stock estimation. |


**Table 33**

| Tool | One-Line Purpose |
|---|---|
| Docker | Packages each microservice into a portable, consistent container image. |
| Docker Compose | Runs the full multi-service stack locally and for the pilot deployment. |
| Kubernetes | Container orchestration platform targeted for Stage 3 production deployment. |
| Nginx | Reverse proxy / ingress layer in front of the API Gateway. |
| Git | Version control for all service code, infrastructure configuration, and documentation. |
