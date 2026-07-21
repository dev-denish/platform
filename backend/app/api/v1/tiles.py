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

Symbology params (Phase 3 Wave F): `bands`, `stretch`, `colors` are plain query
params alongside the token, NOT encoded into it. Deliberate: the token's job is
authorizing "can this bearer see tiles for LAYER X at all", and every band of
that layer's OWN raster is already reachable through the token's existing
grant (any z/x/y, the default band composite) - picking a DIFFERENT band
combination or stretch of the SAME layer's OWN data crosses no access boundary
the token wasn't already covering, the same way the z/x/y coordinates
themselves aren't in the token either. These are rendering preferences, not a
second authorization dimension, so binding them into the signed payload would
add complexity (re-minting a token on every symbology change) for no real
security gain. They ARE validated for correctness (band indices against the
layer's real band count, stretch bounds, color format) - just not for
authorization.
"""
from __future__ import annotations

import hashlib
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import CurrentUserDep, get_tile_service
from app.core.errors import ValidationError
from app.domain.dtos import PixelValue
from app.services.tile_service import TileService

router = APIRouter(tags=["tiles"])

_SUPPORTED_EXT = "png"
# Tiles are content-addressed by (cog_key, z, x, y) and never mutate in place -
# a layer's COG is written once by the ingest job and is never updated - so a
# long, "immutable" cache lifetime is correct, not just permissive.
_CACHE_CONTROL = "public, max-age=86400, immutable"
_HEX_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


def _parse_bands(raw: str | None) -> tuple[int, ...] | None:
    """'5' -> grayscale from band 5; '4,3,2' -> R,G,B from those bands. Range
    (is a given index a real band on THIS layer) is validated later, in
    render_tile, against the actual opened COG - the only place that knows
    the true band count."""
    if raw is None:
        return None
    try:
        values = tuple(int(v) for v in raw.split(","))
    except ValueError as e:
        raise ValidationError("bands must be comma-separated integers, e.g. '4,3,2' or '5'.") from e
    if len(values) not in (1, 3):
        raise ValidationError("bands must specify exactly 1 band (grayscale) or 3 bands (R,G,B).")
    if any(v < 1 for v in values):
        raise ValidationError("Band indices are 1-based; every value must be >= 1.")
    return values


def _parse_stretch(raw: str | None) -> tuple[int, int] | None:
    if raw is None:
        return None
    parts = raw.split(",")
    if len(parts) != 2:
        raise ValidationError("stretch must be two comma-separated percentiles, e.g. '2,98'.")
    try:
        lo, hi = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise ValidationError("stretch percentiles must be whole numbers, e.g. '2,98'.") from e
    if not (0 <= lo < hi <= 100):
        raise ValidationError("stretch must satisfy 0 <= low < high <= 100.")
    return lo, hi


def _parse_colors(raw: str | None) -> dict[str, str] | None:
    """'1:2e7d32,2:1565c0' -> per-class color overrides for classified
    rendering. Ignored (harmlessly) if the render ends up in raw-bands mode -
    there's no "class" to color there."""
    if raw is None:
        return None
    overrides: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            raise ValidationError("colors must be 'value:hexcolor' pairs, e.g. '1:2e7d32,2:1565c0'.")
        value, hexcolor = pair.split(":", 1)
        if not _HEX_COLOR.match(hexcolor):
            raise ValidationError(f"Invalid hex color '{hexcolor}' for class value '{value}' - use rrggbb.")
        overrides[value.strip()] = f"#{hexcolor}"
    return overrides


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
    bands: Annotated[
        str | None, Query(description="1-based band indices: '5' (grayscale) or '4,3,2' (R,G,B).")
    ] = None,
    stretch: Annotated[
        str | None, Query(description="Percentile stretch bounds, e.g. '2,98'. Default 2,98.")
    ] = None,
    colors: Annotated[
        str | None, Query(description="Per-class color overrides for classified layers, e.g. '1:2e7d32'.")
    ] = None,
) -> Response:
    if ext != _SUPPORTED_EXT:
        raise ValidationError(
            f"Unsupported tile format '.{ext}'. Only '.{_SUPPORTED_EXT}' is served."
        )

    band_indexes = _parse_bands(bands)
    stretch_range = _parse_stretch(stretch)
    color_overrides = _parse_colors(colors)

    svc.verify_token(layer_id, token)
    # NotFoundError (404) if no COG yet
    cog_key, legend = svc.get_render_context(layer_id)

    cache_key = f"{cog_key}:{z}:{x}:{y}:{band_indexes}:{stretch_range}:{color_overrides}"
    etag = f'"{hashlib.sha256(cache_key.encode()).hexdigest()[:16]}"'
    if request.headers.get("if-none-match") == etag:
        # Already has this exact tile cached - skip the render entirely.
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL})

    png = svc.render(  # NotFoundError (404) if outside bounds, ValidationError (422) if bands out of range
        cog_key, z, x, y,
        legend=legend, bands=band_indexes, stretch=stretch_range, color_overrides=color_overrides,
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
    )


@router.get("/layers/{layer_id}/pixel", response_model=PixelValue)
def get_pixel(
    layer_id: UUID,
    _user: CurrentUserDep,
    svc: Annotated[TileService, Depends(get_tile_service)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    lat: Annotated[float, Query(ge=-90, le=90)],
) -> PixelValue:
    """Phase 3 Wave D: click-to-inspect a raster pixel. Unlike `get_tile`
    above, this is a normal fetch() call from an already-authenticated
    frontend session (not an `<img>` GET), so it goes through the standard
    `Authorization: Bearer` dependency every other route uses instead of the
    signed tile-token scheme - no reason to mint a second capability type for
    a request the browser can attach a real header to."""
    values = svc.read_pixel(layer_id, lon, lat)  # NotFoundError (404) if no COG or outside bounds
    return PixelValue(layer_id=layer_id, lon=lon, lat=lat, values=values)
