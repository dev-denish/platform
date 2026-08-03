"""Serves a vector layer's real geometries (Wave: multi-format layers, Part A).

Mirrors TileService.require_layer_access: a normal authenticated fetch() call
(not an `<img>`/tileLayer request), so it gets a live project-membership
check every time rather than a minted capability token."""
from __future__ import annotations

from uuid import UUID

from app.core.db import Database
from app.core.errors import NotFoundError
from app.domain.authz import require_project_view
from app.domain.dtos import CurrentUser
from app.repositories.datasets import LayerRepository
from app.repositories.vector_layers import VectorFeatureRepository


class VectorLayerService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_geojson(self, layer_id: UUID, user: CurrentUser) -> dict:
        with self.db.connection() as conn, conn.cursor() as cur:
            layer = LayerRepository(cur).get(layer_id)
            if not layer or layer["layer_kind"] != "vector":
                raise NotFoundError("No vector data for this layer.")
            require_project_view(cur, layer["project_id"], user)
            return VectorFeatureRepository(cur).as_geojson_feature_collection(layer_id)
