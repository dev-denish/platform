"""
Tile serving orchestration (Phase 3 Wave A).

Matches the rest of the app's service-layer convention (ProjectService,
JobService, IngestionService): the API route stays thin, this is where token
verification, the layer/cog_key lookup, and rendering are actually wired
together and translated into the domain-exception vocabulary the global
handlers already understand.
"""
from __future__ import annotations

from uuid import UUID

from rio_tiler.errors import TileOutsideBounds

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AuthError, NotFoundError
from app.core.security import decode_token
from app.repositories.datasets import LayerRepository
from app.services.ingestion.storage import Storage
from app.services.tile_renderer import render_tile


class TileService:
    def __init__(self, db: Database, settings: Settings, storage: Storage) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage

    def verify_token(self, layer_id: UUID, token: str) -> None:
        payload = decode_token(self.settings, token, expected_type="tile")
        if payload.get("sub") != str(layer_id):
            raise AuthError("This tile token is not valid for this layer.")

    def get_cog_key(self, layer_id: UUID) -> str:
        """Raises NotFoundError if the layer doesn't exist or hasn't been
        converted to a COG yet (conversion pending/failed - see workers/jobs.py) -
        either way there is nothing to tile from."""
        with self.db.connection() as conn, conn.cursor() as cur:
            layer = LayerRepository(cur).get(layer_id)
        if not layer or not layer["cog_key"]:
            raise NotFoundError("No tiles available for this layer.")
        return layer["cog_key"]

    def render(self, cog_key: str, z: int, x: int, y: int) -> bytes:
        cog_path = self.storage.local_path_for_processing(cog_key)
        try:
            return render_tile(cog_path, z, x, y)
        except TileOutsideBounds as e:
            # The normal, expected outcome for XYZ requests at a viewport's
            # edges - not a failure - so this maps to the same clean 404 any
            # tile server returns for "no data here", not a 500.
            raise NotFoundError("No data at this tile.") from e
