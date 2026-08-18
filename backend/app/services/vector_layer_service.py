"""Serves a vector layer's real geometries (Wave: multi-format layers, Part A).

Mirrors TileService.require_layer_access: a normal authenticated fetch() call
(not an `<img>`/tileLayer request), so it gets a live project-membership
check every time rather than a minted capability token."""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError, ValidationError
from app.domain.authz import require_project_view
from app.domain.dtos import CurrentUser
from app.repositories.admin_boundaries import AdminBoundaryRegistryRepository
from app.repositories.datasets import LayerRepository
from app.repositories.vector_layers import VectorFeatureRepository


class VectorLayerService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_geojson(
        self, layer_id: UUID, user: CurrentUser, district_lgd_code: str | None = None
    ) -> dict:
        with self.db.connection() as conn, conn.cursor() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if not layer or layer["layer_kind"] != "vector":
                raise NotFoundError("No vector data for this layer.")
            require_project_view(cur, layer["project_id"], user)
            # Wave: Admin Boundaries. A layer this large (Village) has no
            # whole-layer response at all - unlike every other vector layer,
            # where district_lgd_code simply isn't provided and the query
            # below is unfiltered.
            if layer["requires_district_scope"] and not district_lgd_code:
                raise ValidationError(
                    "This layer requires a district_lgd_code query param - "
                    "it is too large to fetch as a whole."
                )
            return VectorFeatureRepository(cur).as_geojson_feature_collection(
                layer_id, district_lgd_code
            )

    def get_village_coverage(
        self, layer_id: UUID, user: CurrentUser, district_lgd_code: str
    ) -> dict:
        """Wave: Admin Boundaries. The "boundary not available" list - every
        officially-registered village in this district (admin_village_
        registry) cross-checked against which of them actually have a
        polygon in this Village layer, not just the aggregate coverage
        percentage from the sourcing research."""
        with self.db.connection() as conn, conn.cursor() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if not layer or layer["layer_kind"] != "vector":
                raise NotFoundError("No vector data for this layer.")
            require_project_view(cur, layer["project_id"], user)
            return AdminBoundaryRegistryRepository(cur).village_coverage_for_district(
                str(layer_id), district_lgd_code
            )
