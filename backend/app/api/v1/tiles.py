"""
Map tile serving (Phase 3 Wave A).

Existing implementation: none - this is the first raster-tile endpoint.

Auth is deliberately NOT the standard `Authorization: Bearer` dependency every
other route uses: a map library's tile requests are ultimately `<img>`-style GETs
(Leaflet/MapLibre set a URL template and let the browser fetch each tile), and a
browser cannot attach a custom header to those. Requiring one would silently
break the map rather than reject cleanly. Instead, a short-lived signed token
(minted by `ProjectService.get_layers` alongside the tile URL template - see
`app/core/security.create_tile_token`) travels in the URL's query string, which
`<img src=...>` can send. See that function's docstring for the known,
documented limitation of this approach (a time-boxed capability, not a
per-user-revocable grant - the same tradeoff S3 presigned URLs make).
"""
from __future__ import annotations

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import get_tile_service
from app.core.errors import ValidationError
from app.services.tile_service import TileService

router = APIRouter(tags=["tiles"])

_SUPPORTED_EXT = "png"
# Tiles are content-addressed by (cog_key, z, x, y) and never mutate in place -
# a layer's COG is written once by the ingest job and is never updated - so a
# long, "immutable" cache lifetime is correct, not just permissive.
_CACHE_CONTROL = "public, max-age=86400, immutable"


@router.get("/tiles/{layer_id}/{z}/{x}/{y}.{ext}")
def get_tile(
    request: Request,
    layer_id: UUID,
    z: int,
    x: int,
    y: int,
    ext: str,
    token: Annotated[str, Query(...)],
    svc: Annotated[TileService, Depends(get_tile_service)],
) -> Response:
    if ext != _SUPPORTED_EXT:
        raise ValidationError(
            f"Unsupported tile format '.{ext}'. Only '.{_SUPPORTED_EXT}' is served."
        )

    svc.verify_token(layer_id, token)
    cog_key = svc.get_cog_key(layer_id)  # NotFoundError (404) if no COG yet

    etag = f'"{hashlib.sha256(f"{cog_key}:{z}:{x}:{y}".encode()).hexdigest()[:16]}"'
    if request.headers.get("if-none-match") == etag:
        # Already has this exact tile cached - skip the render entirely.
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL})

    png = svc.render(cog_key, z, x, y)  # NotFoundError (404) if outside bounds
    return Response(
        content=png,
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
    )
