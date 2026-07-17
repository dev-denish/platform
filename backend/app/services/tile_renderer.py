"""
XYZ map tile rendering from a Cloud-Optimized GeoTIFF (Phase 3 Wave A).

Dynamic (render-on-request) tiling via rio-tiler (pinned) - not a pre-generated
tile pyramid. This is the standard modern approach for exactly this scale: it
needs no cache-warming step, serves every zoom level from one COG, and the COG's
own overviews (built by ingestion/cog.py) keep each request's I/O bounded to
roughly one overview level's worth of data, not the full-resolution raster.

Format: PNG, not WEBP. Our COGs are single-band CLASSIFIED rasters (discrete
land-cover/class values, not photographic RGB) - PNG is lossless, so hard class
boundaries render pixel-exact with no compression artefacts blurring a boundary
between two classes (a real interpretive/compliance concern for an MRV product,
not just a quality nicety). PNG also needs no browser feature-detection and
matches the format `raster.render_preview` already uses for the same reason.

Known gap: color comes from the SAME default palette as `render_preview`'s
no-legend fallback, not any custom `class_legend` a user supplied at upload time.
Legends are used at ingest time (compute_stats/render_preview) and are not
persisted anywhere past that - there is no column to read one back from here.
Tiles are therefore visually consistent and stable, but will not reproduce a
custom legend's colors/labels. Wiring that would need a small additive migration
(store the legend against the dataset/layer) - not done here since it wasn't
asked for in this wave; flagging it as the natural next step if custom-legend
tile coloring matters.
"""
from __future__ import annotations

from rio_tiler.io import Reader

from app.services.ingestion.raster import color_for_value

_TILE_FORMAT = "PNG"


def _colormap_for_uint8(nodata: float | None) -> dict[int, tuple[int, int, int, int]]:
    """Full 0-255 colormap so rio-tiler can map every possible class byte value -
    same DEFAULT_PALETTE + color_for_value as render_preview, nodata -> fully
    transparent, matching that function's convention exactly."""
    cmap: dict[int, tuple[int, int, int, int]] = {}
    for v in range(256):
        if nodata is not None and v == int(nodata):
            cmap[v] = (0, 0, 0, 0)
            continue
        hexc = color_for_value(v, legend=None).lstrip("#")
        r, g, b = (int(hexc[i : i + 2], 16) for i in (0, 2, 4))
        cmap[v] = (r, g, b, 235)
    return cmap


def render_tile(cog_path: str, z: int, x: int, y: int) -> bytes:
    """Render one PNG tile. Raises `rio_tiler.errors.TileOutsideBounds` if the
    requested tile doesn't intersect the raster - the normal, expected outcome
    for XYZ requests at a viewport's edges, not a failure. The caller (the tiles
    API route) maps that to a clean 404, matching how any XYZ tile server
    behaves for out-of-coverage tiles."""
    with Reader(cog_path) as cog:
        img = cog.tile(x, y, z)
        colormap = _colormap_for_uint8(cog.dataset.nodata)
        return img.render(img_format=_TILE_FORMAT, colormap=colormap)
