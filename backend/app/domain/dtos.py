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
from typing import Generic, TypeVar
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
    kpis: dict[str, KpiValue]


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


class ProjectLayers(BaseModel):
    project_id: UUID
    layers: list[LayerOut]


# ---------------------------------------------------------------- ingestion


class IngestMetadata(BaseModel):
    """Validated upload metadata. Replaces the pile of untyped Form fields."""

    project_name: str = Field(min_length=1, max_length=256)
    region: str = Field(default="Unspecified", max_length=256)
    dataset_type: DatasetType
    source: str = Field(min_length=1, max_length=256)
    classification_method: str = Field(default="", max_length=256)
    accuracy_score: float = Field(ge=0.0, le=100.0)
    date_processed: date
    pixel_size_m: float = Field(default=10.0, gt=0.0, le=10_000.0)


class IngestResult(BaseModel):
    project_id: UUID
    dataset_id: UUID
    batch_id: UUID
    total_area_ha: float
    class_stats: dict[str, float]


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
