"""Unit tests for XYZ tile rendering from a real COG (Phase 3 Wave A). No mocked
tile data - a real classified raster is generated and converted to a real COG
via rio-cogeo, exactly like the ingest pipeline does, then rendered with the
real rio-tiler code path."""
from __future__ import annotations

import morecantile
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rio_tiler.errors import TileOutsideBounds

from app.services.ingestion.cog import convert_to_cog
from app.services.tile_renderer import render_tile

_TMS = morecantile.tms.get("WebMercatorQuad")


@pytest.fixture
def cog_path(tmp_path):
    h = w = 512
    arr = np.zeros((h, w), dtype="uint8")
    arr[: h // 2, :] = 1
    arr[h // 2 :, :] = 2
    src = tmp_path / "src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=0,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(arr, 1)
    dst = tmp_path / "cog.tif"
    convert_to_cog(str(src), str(dst))
    return str(dst)


def _tile_covering_center(path):
    with rasterio.open(path) as d:
        bounds = d.bounds
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    z = 14
    t = _TMS.tile(cx, cy, z)
    return t.z, t.x, t.y


def test_render_tile_returns_real_png_bytes(cog_path):
    z, x, y = _tile_covering_center(cog_path)
    png = render_tile(cog_path, z, x, y)
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG magic number, not a stub
    assert len(png) > 100


def test_render_tile_outside_bounds_raises(cog_path):
    z, x, y = _tile_covering_center(cog_path)
    with pytest.raises(TileOutsideBounds):
        render_tile(cog_path, z, x + 1000, y + 1000)
