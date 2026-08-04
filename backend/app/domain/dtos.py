"""
Data Transfer Objects (request + response contracts).

Existing implementation (MVP): endpoints accepted loose `Form(...)` params with no
enum validation and returned raw dicts, so response shapes drifted between routes
and clients had no schema.

Enterprise solution: every request is validated by a Pydantic model (types, ranges,
enums, string lengths) before it touches business logic, and every response is a
typed model that FastAPI publishes in OpenAPI. Invalid input fails at the edge with
a 422 and a precise error, never deep in the ingestion code.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DatasetType, LayerKind, ProjectStatus, Role

# ---------------------------------------------------------------- auth


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - not a secret; OAuth2 token_type literal
    expires_in: int  # seconds until the access token expires


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class CurrentUser(BaseModel):
    user_id: UUID
    username: str
    role: Role


# ---------------------------------------------------------------- bulk delete
# Wave: bulk select + delete (Projects, Users). One shared shape for both -
# a bulk delete is always "attempt each id, report success/failure per id",
# whether the underlying action is a soft-delete (projects) or a permanent
# hard-delete (users). Not a general bulk-action framework: delete only, per
# the wave's own non-goals.


class BulkDeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)


class BulkDeleteItemResult(BaseModel):
    id: UUID
    name: str  # project name or username - what the confirmation UI listed
    success: bool
    error: str | None = None


class BulkDeleteResult(BaseModel):
    results: list[BulkDeleteItemResult]


# ---------------------------------------------------------------- user management


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: UUID
    username: str
    role: Role
    created_at: datetime
    # None = active. Deliberately no separate "is_active" bool - the FE
    # derives it from this the same way the API itself does (deleted_at IS
    # NULL), one source of truth for "active" instead of two that could drift.
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None
    # Wave: three-tier removal. Independent of deleted_at/deleted_by by
    # design - a user can be deactivated-and-hidden, hidden-without-
    # deactivated, or deactivated-without-hidden; hiding never touches login
    # (deleted_at alone controls that).
    hidden_at: datetime | None = None
    hidden_by: UUID | None = None


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    # Length is enforced against app.core.security.MIN_PASSWORD_LENGTH in
    # UserService.create_user, not duplicated here as a second Field(min_length=...)
    # rule that could drift from it.
    password: str = Field(min_length=1, max_length=256)
    role: Role


# ---------------------------------------------------------------- projects


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    project_id: UUID
    name: str
    region: str | None
    status: ProjectStatus
    latest_dataset_id: UUID | None = None
    latest_accuracy: float | None = None
    latest_processed: date | None = None


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    dataset_id: UUID
    type: DatasetType
    source: str | None
    accuracy_score: float | None
    date_processed: date | None
    loaded_at: datetime


class ProjectDetail(BaseModel):
    project_id: UUID
    name: str
    region: str | None
    status: ProjectStatus
    start_date: date | None
    datasets: list[DatasetOut]


# ---------------------------------------------------------------- membership


class MemberOut(BaseModel):
    user_id: UUID
    username: str
    role: Role  # this project's role for this user - never Administrator
    added_at: datetime
    added_by: UUID | None


class ProjectMembers(BaseModel):
    project_id: UUID
    members: list[MemberOut]


class AddMemberRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    # None -> the service prefills from the target user's own global role
    # (never Administrator - see PROJECT_ROLES).
    role: Role | None = None


class UpdateMemberRoleRequest(BaseModel):
    role: Role


class AdminResetPasswordRequest(BaseModel):
    """Administrator resetting someone else's password (Wave: password
    reset). No old password field - the admin isn't that person, so there is
    nothing of theirs to verify. Length is enforced against
    app.core.security.MIN_PASSWORD_LENGTH in UserService.admin_reset_password,
    same convention as CreateUserRequest."""
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    """Self-service password change - the inverse case: the caller IS that
    person, so `current_password` must be verified against their own stored
    hash before `new_password` is accepted (see AuthService.change_password)."""
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


# ---------------------------------------------------------------- KPIs / layers


class KpiValue(BaseModel):
    value: float
    unit: str | None


class ProjectKpis(BaseModel):
    project_id: UUID
    # Phase 3 Wave G: keyed by layer_id, one metric_name->KpiValue dict per
    # real layer - not a single project-wide flattened dict. The old flat
    # `kpis: dict[str, KpiValue]` silently lost data for any project with 2+
    # layers sharing a metric name (nearly every multi-layer classified
    # project, since every classified layer has its own "total_area" etc) -
    # same-named metrics from different layers overwrote each other in the
    # dict comprehension that built it. See KpiRepository.for_project.
    layers: dict[str, dict[str, KpiValue]]


class LayerOut(BaseModel):
    layer_id: UUID
    type: DatasetType
    # Wave: multi-format layers. Discriminates which of the four render paths
    # the frontend should use - raster tiles (tile_url_template), real
    # geometries (features_url), or a live external WMS/WFS proxy
    # (tile_url_template / features_url again, just backed by
    # external_layers.py instead of tiles.py - the frontend doesn't need to
    # tell WMS apart from raster, both are "a tile URL template").
    layer_kind: LayerKind = LayerKind.RASTER
    crs: str
    bounds: list[list[float]]  # [[minLat,minLng],[maxLat,maxLng]] for Leaflet
    # None for vector/external layers - "pixels per metre" has no meaning
    # once there's no raster grid backing this layer.
    pixel_size_m: float | None
    preview_url: str | None
    date_processed: str | None
    # Phase 3: a {z}/{x}/{y} URL template (Leaflet/MapLibre-ready) carrying a
    # short-lived signed tile token in its query string - see
    # app/core/security.py's create_tile_token. None if this layer has no COG yet
    # (conversion pending/failed) - there is nothing to tile from. Also used
    # (Wave: multi-format layers) for an external_wms layer, pointing at this
    # backend's own SSRF-guarded proxy (app/api/v1/external_layers.py), never
    # at the third-party server directly.
    tile_url_template: str | None = None
    # Wave: multi-format layers. Set only for layer_kind == "vector" - the
    # authenticated endpoint (GET /layers/{id}/geojson) the frontend fetches
    # once and renders via Leaflet's <GeoJSON>. Real geometries are never
    # embedded inline here: a layer can hold thousands of features, and this
    # response is fetched on every project-detail page load.
    features_url: str | None = None
    # Phase 3 Wave F (symbology): the real band count backing this layer's COG,
    # so the frontend can populate band-to-channel pickers without guessing.
    # None only for a layer ingested before this column existed AND whose COG
    # is itself missing (ProjectService backfills it on the fly otherwise).
    band_count: int | None = None
    # The class_legend supplied at upload time, persisted verbatim (previously
    # this was used only transiently at ingest and never stored - see
    # tile_renderer.py's history). None for an unclassified/raw layer - the
    # frontend uses this to decide whether to offer a "Classified" symbology
    # mode at all.
    class_legend: dict[str, Any] | None = None
    # Wave: Reference Layer Library. Pulled straight off the same joined
    # dataset row LayerRepository.list_for_project already reads type/
    # date_processed from - the frontend's Layers panel info popover used to
    # get these via a fragile client-side (type, date_processed) match
    # against a SEPARATE per-project datasets list, which only ever worked
    # for a layer's OWN project; a reference layer viewed from a DIFFERENT
    # project has no such match. Reading them here instead is both simpler
    # and correct everywhere.
    source: str | None = None
    # Optional admin-set override for how this layer's name is displayed
    # (rename-a-layer). None means "no override" - the frontend falls back
    # to its existing type/source-based label, exactly as before this field
    # existed. Distinct from `source`/`type`: this never replaces or aliases
    # either, it's a purely cosmetic display label layered on top.
    display_name: str | None = None
    accuracy_score: float | None = None
    # True for a layer visible on every project's Layers panel (Wave:
    # Reference Layer Library), not just the one it's nominally attached to -
    # see LayerRepository.list_for_project's WHERE clause.
    is_reference: bool = False
    # Wave 3 (Added Layers): true for a quick, informal upload from the map -
    # no class_legend/accuracy_score, excluded from Key Metrics and Landscape
    # Evolution (see KpiRepository.for_project / ProjectService.get_evolution)
    # so it never pollutes a project's official numbers.
    is_adhoc: bool = False
    # True for a raster layer ingested before the geometric padding-mask fix
    # (see raster.has_real_mask) - its COG has no real per-pixel warp mask
    # baked in, so warp-fill padding can render as opaque data instead of
    # transparent. Always False for a non-raster layer or one with no COG
    # yet. Computed live off the actual file (ProjectService._needs_
    # reingestion), never persisted, so it can't go stale - re-ingesting
    # from the original source is the only real fix.
    needs_reingestion: bool = False


class ProjectLayers(BaseModel):
    project_id: UUID
    layers: list[LayerOut]


class ActivityItem(BaseModel):
    """One `audit_log` row already scoped to a single project - see
    AuditRepository.list_for_project. `target`/`detail` are free-form
    (their shape depends on `action`, same as everywhere else audit_log is
    read), never re-interpreted here."""

    actor_name: str
    action: str
    detail: str | None
    target: str | None
    created_at: datetime


class ActivityFeed(BaseModel):
    items: list[ActivityItem]


class EvolutionChange(BaseModel):
    from_date: str
    to_date: str
    # None for BOTH fields together, always - this class wasn't part of the
    # legend at one or both of these dates, so there's nothing real to
    # subtract/divide (see EvolutionClassRow.area_by_date_ha).
    net_change_ha: float | None
    # "new": grew from a recorded 0 ha baseline - mathematically infinite %,
    # never returned as Infinity/NaN over JSON. A shrink TO zero (positive ->
    # 0) has no such problem - dividing BY zero is the only undefined case,
    # so that's a completely ordinary -100.0. Going from 0 ha to 0 ha is a
    # real (if unremarkable) 0.0, not "new" - nothing grew.
    pct_change: float | Literal["new"] | None


class EvolutionClassRow(BaseModel):
    # The same raw kpi.metric_name GET /projects/{id}/kpis already returns
    # (e.g. "class_area_cropland") - the frontend's existing
    # humanizeMetricName() labels it, no second humanization convention
    # introduced here.
    metric_name: str
    # Keyed by date (same ISO string as LayerOut.date_processed). None means
    # this date's legend didn't define this class at all; 0.0 means it WAS
    # defined but genuinely zero hectares were measured - these are
    # deliberately different (compute_stats only ever writes a KPI row for a
    # pixel value that occurs at least once, so a legend-defined-but-absent
    # class has no row to read a real 0 from - see
    # raster.legend_class_labels).
    area_by_date_ha: dict[str, float | None]
    first_vs_last: EvolutionChange
    # One entry per adjacent date pair, in date order - same length as
    # len(dates) - 1 for every row, regardless of how many dates exist.
    consecutive: list[EvolutionChange]


class ProjectEvolution(BaseModel):
    """Phase 3 Wave G: land-class change across a project's real classified
    (legend-bearing) dated layers - read-only, computed on demand from
    already-persisted KPI rows (see ProjectService.get_evolution), nothing
    stored. A project mixing raw/unclassified imagery in with classified
    LULC across dates only compares the classified ones - a raw-imagery date
    is excluded from `dates` entirely, not errored on."""

    project_id: UUID
    # False if fewer than 2 eligible (classified, dated) layers exist (0 or
    # 1) - `dates`/`classes` still reflect whatever WAS found (e.g. the one
    # eligible date, if there's exactly one), so the frontend can explain
    # why instead of showing an unexplained empty table.
    applicable: bool
    dates: list[str]
    classes: list[EvolutionClassRow]


class PixelValue(BaseModel):
    """Phase 3 Wave D: the real per-band pixel values at one lon/lat for a
    layer's COG. Raw numbers only - no classified-label lookup here, since the
    caller already has this layer's `class_legend` from GET
    /projects/{id}/layers and can map a value to its label itself without a
    second round trip. `None` entries are nodata/no-coverage at that band."""

    layer_id: UUID
    lon: float
    lat: float
    values: list[float | None]


# ---------------------------------------------------------------- ingestion


class IngestMetadata(BaseModel):
    """Validated upload metadata. Replaces the pile of untyped Form fields."""

    # None only when `project_id` (below) is set - Wave 3's ad-hoc endpoint
    # already knows which (existing) project it's uploading into, so it never
    # needs a find-or-create-by-name.
    project_name: str | None = Field(default=None, max_length=256)
    region: str = Field(default="Unspecified", max_length=256)
    dataset_type: DatasetType
    source: str = Field(min_length=1, max_length=256)
    classification_method: str = Field(default="", max_length=256)
    # Required only when a class_legend is supplied (enforced in the upload
    # endpoint, which is the only place that knows about the legend) - there is
    # no classification to be accurate about for a raw, unclassified scene.
    accuracy_score: float | None = Field(default=None, ge=0.0, le=100.0)
    date_processed: date
    pixel_size_m: float = Field(default=10.0, gt=0.0, le=10_000.0)
    # Wave: multi-format layers. Required only for a .csv upload (validated in
    # the upload endpoint, the only place that knows the file's extension) -
    # deliberately never guessed from header names like "lat"/"latitude": a
    # wrong guess would silently place points at the wrong coordinates with
    # no error at all.
    lat_column: str | None = Field(default=None, max_length=256)
    lon_column: str | None = Field(default=None, max_length=256)
    # Wave: Reference Layer Library. When true, `project_name`/`region` above
    # are ignored server-side - IngestionService resolves the one shared
    # Reference Layer Library project instead (see
    # app.services.project_access.resolve_reference_library_project).
    is_reference: bool = False
    # Wave 3 (Added Layers): set only by POST /projects/{id}/adhoc-layers -
    # the project already exists (this IS its id), so
    # IngestionService._resolve_project re-checks project-level upload access
    # directly (app.domain.authz.require_project_upload) instead of
    # find-or-create-by-name. Mutually exclusive with is_reference in
    # practice (an ad-hoc layer is always project-scoped), but not enforced
    # here - both endpoints that set either flag never set the other.
    project_id: UUID | None = None
    is_adhoc: bool = False


class BandStatsOut(BaseModel):
    min: float
    max: float
    mean: float
    stddev: float


class IngestResult(BaseModel):
    project_id: UUID
    dataset_id: UUID
    batch_id: UUID
    total_area_ha: float
    # Exactly one of these is populated: class_stats when a class_legend was
    # supplied at upload, band_stats when the scene was ingested unclassified.
    class_stats: dict[str, float] | None = None
    band_stats: BandStatsOut | None = None
    # Wave: multi-format layers. Lets workers/jobs.py decide whether the
    # best-effort COG-conversion step even applies (raster only - a vector
    # layer has no raster grid to convert) without a second DB round trip.
    layer_kind: LayerKind = LayerKind.RASTER
    feature_count: int | None = None


# ---------------------------------------------------------------- jobs (Phase 2)


class JobAccepted(BaseModel):
    """202 response for `POST /datasets/upload`: the job is queued, poll
    `status_url` (`GET /jobs/{id}`) for its outcome."""

    job_id: UUID
    status_url: str


class ScanValuesResult(BaseModel):
    """Response for `POST /datasets/scan-values` (Class Legend Builder's
    "Scan file" action): every distinct real value found in band 1 of the
    uploaded raster, ascending. Synchronous - unlike the ingest job above,
    this is a quick, bounded read with nothing to persist, so there is no
    job/polling round trip."""

    values: list[int]


class UpdateClassLegendRequest(BaseModel):
    """Request for `PATCH /layers/{id}/class-legend` (Wave: editable class
    legend). The FULL replacement legend, same shape as an upload's own
    `class_legend` - a value present here (however it got there: unchanged,
    renamed, recolored) is counted; a value from the old legend simply
    absent here is what "removed" means. Reuses ClassLegendBuilder's own
    `buildLegend()` shape on the frontend, so there's no separate add/
    remove/rename wire format to keep in sync."""

    class_legend: dict[str, dict[str, str] | str]


class ClassLegendUpdateResult(BaseModel):
    layer_id: UUID
    class_legend: dict[str, Any]
    total_area_ha: float
    # None only if the new legend is empty - the layer reports as unclassified
    # (band_stats) instead, same as a fresh upload with no legend at all.
    class_area_ha: dict[str, float] | None


class RenameLayerRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=256)


class LayerRenameResult(BaseModel):
    layer_id: UUID
    display_name: str


class JobOut(BaseModel):
    id: UUID
    kind: str
    status: str
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict | None
    error: dict | None


# ---------------------------------------------------------------- external layers


class WmsDomainOut(BaseModel):
    domain_id: UUID
    domain: str
    added_by: UUID | None
    created_at: datetime


class AddWmsDomainRequest(BaseModel):
    # Bare hostname only (e.g. "mapserver.example.com") - never a full URL/
    # scheme, so there's nothing here that could smuggle a path or query
    # string past the allow-list check later. Validated further (basic
    # hostname shape) in WmsDomainRepository/the endpoint.
    domain: str = Field(min_length=1, max_length=253)


class CreateExternalLayerRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=256)
    region: str = Field(default="Unspecified", max_length=256)
    # Must already be a row in allowed_wms_domain - re-checked server-side,
    # never trusted from the client (see external_layers.py).
    domain: str = Field(min_length=1, max_length=253)
    service_kind: Literal["wms", "wfs"]
    # The path portion of the service endpoint on that domain, e.g.
    # "/geoserver/wms" - combined server-side with `domain` (never client-
    # supplied as a full URL) to build the base_url that's actually fetched.
    path: str = Field(default="", max_length=512)
    layer_name: str = Field(min_length=1, max_length=256)
    # Wave: Reference Layer Library. When true, `project_name`/`region` above
    # are ignored server-side - WmsService.create_external_layer resolves the
    # one shared Reference Layer Library project instead (see
    # app.services.project_access.resolve_reference_library_project).
    is_reference: bool = False


# ---------------------------------------------------------------- pagination


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard pagination envelope used by every list endpoint."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def next_offset(self) -> int | None:
        nxt = self.offset + self.limit
        return nxt if nxt < self.total else None


# ---------------------------------------------------------------- health


class HealthStatus(BaseModel):
    status: str
    service: str
    environment: str
    version: str


class ReadyStatus(BaseModel):
    status: str
    checks: dict[str, str]
