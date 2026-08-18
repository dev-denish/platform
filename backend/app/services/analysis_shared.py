"""Shared analysis-refresh validation (Wave: VNV Pipeline NDFI go-live).

Extracted out of `GEEAnalysisService._prepare_refresh` - that logic was
already entirely catalog-generic (no `ee` import, no GEE call in it at
all), so `VNVAnalysisService.enqueue_refresh` can reuse the EXACT same
catalog/permission/boundary/config validation instead of a second,
slightly-different copy. `GEEAnalysisService._prepare_refresh` is now a
thin wrapper around `prepare_analysis_refresh` below - its own signature,
call sites, and behavior are unchanged by this extraction.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError, ValidationError
from app.domain import analysis_config
from app.domain.analysis_catalog import AnalysisCatalogEntry, get_catalog_entry
from app.domain.authz import require_project_upload
from app.domain.dtos import CurrentUser
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.forest_definition import ForestDefinitionRepository


def prepare_analysis_refresh(
    db: Database,
    project_id: UUID,
    analysis_id: str,
    actor: CurrentUser,
    request_params: dict[str, Any] | None = None,
) -> tuple[AnalysisCatalogEntry, dict[str, Any], float, dict[str, Any] | None]:
    """Catalog/permission/boundary validation shared by every analysis
    compute path (GEE and VNV Pipeline alike) - a bad request (unknown
    analysis, not implemented yet, no upload permission, no boundary, an
    unsupported config combination) fails immediately with a normal HTTP
    error rather than surfacing later inside an async job with no one
    watching.

    `request_params` is resolved+validated BEFORE the permission/boundary DB
    round trip, same "validate what's knowable from static data first" order
    this had before the extraction - an unsupported source/masking
    combination is rejected with its specific reason whether or not the
    caller could even upload to this project. Unconfigured ids (`"config"
    not in entry`, true for every VNV Pipeline entry today) pass
    `request_params` through unchanged.

    Returns `(entry, boundary_geojson, canopy_cover_pct, resolved_params)`.
    """
    entry = get_catalog_entry(analysis_id)
    if entry is None:
        raise NotFoundError("Unknown analysis.")
    if entry["status"] != "available":
        raise ValidationError(f"'{entry['name']}' is not implemented yet.")
    resolved_params = (
        analysis_config.resolve_and_validate(analysis_id, entry["config"], request_params)
        if "config" in entry
        else request_params
    )

    with db.connection() as conn, conn.cursor() as cur:
        require_project_upload(cur, project_id, actor)
        boundary_geojson = AnalysisResultRepository(cur).get_project_boundary_geojson(project_id)
        canopy_cover_pct = float(ForestDefinitionRepository(cur).get()["canopy_cover_pct"])

    if boundary_geojson is None:
        raise ValidationError(
            "This project has no Boundary layer yet - upload one before running an analysis."
        )
    return entry, boundary_geojson, canopy_cover_pct, resolved_params
