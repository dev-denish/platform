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

from app.domain.enums import DatasetType, ProjectStatus, Role

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
    crs: str
    bounds: list[list[float]]  # [[minLat,minLng],[maxLat,maxLng]] for Leaflet
    pixel_size_m: float
    preview_url: str
    date_processed: str | None
    # Phase 3: a {z}/{x}/{y} URL template (Leaflet/MapLibre-ready) carrying a
    # short-lived signed tile token in its query string - see
    # app/core/security.py's create_tile_token. None if this layer has no COG yet
    # (conversion pending/failed) - there is nothing to tile from.
    tile_url_template: str | None = None
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


class ProjectLayers(BaseModel):
    project_id: UUID
    layers: list[LayerOut]


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

    project_name: str = Field(min_length=1, max_length=256)
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


# ---------------------------------------------------------------- jobs (Phase 2)


class JobAccepted(BaseModel):
    """202 response for `POST /datasets/upload`: the job is queued, poll
    `status_url` (`GET /jobs/{id}`) for its outcome."""

    job_id: UUID
    status_url: str


class JobOut(BaseModel):
    id: UUID
    kind: str
    status: str
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: dict | None
    error: dict | None


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
