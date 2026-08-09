"""Renders one static PNG of an analysis' map (GEE tiles + AOI boundary
outline) for the PDF report - server-side, no headless browser.

Deliberately reuses the SAME live-map tile pipeline the interactive Leaflet
map already renders from (`tile_url_template`, a GEE `getMapId()` XYZ tile
URL - see gee_analysis_service.py) rather than adding a new GEE code path
(`getThumbURL`/`getDownloadURL` are unused elsewhere in this codebase) or a
headless browser (Playwright exists in this repo only as a frontend E2E test
tool, never server-side) - this file's whole job is standard XYZ tile
stitching plus a boundary outline, both plain Pillow/httpx work with no new
dependency. `tile_url_template` itself must be freshly fetched by the caller
(see report_service.py) - a GEE map-tile token is short-lived, so a URL taken
from an old stored analysis_result row would likely already be expired by
report-generation time.
"""
from __future__ import annotations

import math
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageDraw

_TILE_SIZE = 256
_MIN_ZOOM = 4
_MAX_ZOOM = 19
_MAX_TILE_SPAN = 4  # cap stitching at a 4x4 tile grid per image
_PADDING_FRACTION = 0.15  # 15% margin around the boundary on each side


def _iter_coords(geometry: dict[str, Any]):
    """Yields every [lon, lat] pair in a GeoJSON Polygon/MultiPolygon
    (ST_Union's own output shape, app/repositories/analysis_results.py)."""
    coords = geometry["coordinates"]
    kind = geometry["type"]
    if kind == "Polygon":
        rings = coords
    elif kind == "MultiPolygon":
        rings = [ring for polygon in coords for ring in polygon]
    else:
        raise ValueError(f"Unsupported boundary geometry type: {kind!r}")
    for ring in rings:
        yield from ring


def _bounds(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    lons = []
    lats = []
    for lon, lat in _iter_coords(geometry):
        lons.append(lon)
        lats.append(lat)
    return min(lons), min(lats), max(lons), max(lats)


def _lonlat_to_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Standard Web Mercator slippy-map projection - same formula the
    frontend's own Leaflet map uses (spherical Mercator, 256px tiles)."""
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n * _TILE_SIZE
    lat_rad = math.radians(max(min(lat, 85.0511), -85.0511))  # Web Mercator's own lat clamp
    y = (
        (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi)
        / 2.0
        * n
        * _TILE_SIZE
    )
    return x, y


def _pick_zoom(bounds: tuple[float, float, float, float]) -> int:
    """Highest zoom where the padded bbox still fits within
    _MAX_TILE_SPAN tiles in both directions - mirrors Leaflet's own
    fitBounds, just computed in Python instead of in the browser."""
    min_lon, min_lat, max_lon, max_lat = bounds
    for zoom in range(_MAX_ZOOM, _MIN_ZOOM - 1, -1):
        x0, y0 = _lonlat_to_pixel(min_lon, max_lat, zoom)
        x1, y1 = _lonlat_to_pixel(max_lon, min_lat, zoom)
        width_tiles = abs(x1 - x0) / _TILE_SIZE
        height_tiles = abs(y1 - y0) / _TILE_SIZE
        if width_tiles <= _MAX_TILE_SPAN and height_tiles <= _MAX_TILE_SPAN:
            return zoom
    return _MIN_ZOOM


def _fetch_tile(
    client: httpx.Client, url_template: str, z: int, x: int, y: int
) -> Image.Image | None:
    url = url_template.format(z=z, x=x, y=y)
    try:
        resp = client.get(url, timeout=10.0)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGBA")
    except (httpx.HTTPError, OSError):
        # A single missing/blank/errored tile must not fail the whole map -
        # it just renders as a transparent gap, same tolerance principle as
        # gee_analysis_service.py's own "caching must never break a request".
        return None


def render_boundary_map_png(
    tile_url_template: str, boundary_geojson: dict[str, Any]
) -> bytes:
    min_lon, min_lat, max_lon, max_lat = _bounds(boundary_geojson)
    lon_pad = (max_lon - min_lon) * _PADDING_FRACTION or 0.01
    lat_pad = (max_lat - min_lat) * _PADDING_FRACTION or 0.01
    padded = (min_lon - lon_pad, min_lat - lat_pad, max_lon + lon_pad, max_lat + lat_pad)

    zoom = _pick_zoom(padded)
    px0, py0 = _lonlat_to_pixel(padded[0], padded[3], zoom)  # top-left (min_lon, max_lat)
    px1, py1 = _lonlat_to_pixel(padded[2], padded[1], zoom)  # bottom-right (max_lon, min_lat)

    tile_x0, tile_y0 = int(px0 // _TILE_SIZE), int(py0 // _TILE_SIZE)
    tile_x1, tile_y1 = int(px1 // _TILE_SIZE), int(py1 // _TILE_SIZE)
    n_tiles = 2**zoom

    canvas_w = (tile_x1 - tile_x0 + 1) * _TILE_SIZE
    canvas_h = (tile_y1 - tile_y0 + 1) * _TILE_SIZE
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (240, 240, 240, 255))

    with httpx.Client() as client:
        for tx in range(tile_x0, tile_x1 + 1):
            for ty in range(tile_y0, tile_y1 + 1):
                tile = _fetch_tile(client, tile_url_template, zoom, tx % n_tiles, ty)
                if tile is not None:
                    canvas.paste(tile, ((tx - tile_x0) * _TILE_SIZE, (ty - tile_y0) * _TILE_SIZE))

    # Crop the stitched canvas down to exactly the padded bbox, so the image
    # isn't dominated by whole extra tiles of margin beyond what was asked for.
    origin_x, origin_y = tile_x0 * _TILE_SIZE, tile_y0 * _TILE_SIZE
    crop_box = (
        int(px0 - origin_x), int(py0 - origin_y),
        int(px1 - origin_x), int(py1 - origin_y),
    )
    cropped = canvas.crop(crop_box)

    draw = ImageDraw.Draw(cropped)
    kind = boundary_geojson["type"]
    rings = (
        boundary_geojson["coordinates"]
        if kind == "Polygon"
        else [ring for polygon in boundary_geojson["coordinates"] for ring in polygon]
    )
    for ring in rings:
        points = [
            (
                _lonlat_to_pixel(lon, lat, zoom)[0] - origin_x - crop_box[0],
                _lonlat_to_pixel(lon, lat, zoom)[1] - origin_y - crop_box[1],
            )
            for lon, lat in ring
        ]
        draw.line(points, fill=(255, 255, 255, 255), width=3, joint="curve")

    buf = BytesIO()
    cropped.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
