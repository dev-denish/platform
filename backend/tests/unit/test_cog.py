"""Unit test for COG conversion (Phase 3 Wave A). Proves rio-cogeo actually
produces a Cloud-Optimized GeoTIFF - checked with rio-cogeo's OWN validator, not
a hand-rolled assertion about internal tiling/overviews."""
from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rio_cogeo.cogeo import cog_validate

from app.services.ingestion.cog import convert_to_cog


def test_convert_to_cog_produces_a_valid_cog(tmp_path):
    # A classified raster in EPSG:4326 - exactly what this platform stores as a
    # layer's file_key (raster.reproject_to_4326's output), which is what
    # workers/jobs.py actually feeds convert_to_cog. Big enough (> the COG
    # profile's 512px block size) that rio-cogeo actually builds overviews -
    # a too-small raster is still a "valid" COG with none, which would make the
    # overview assertion below meaningless.
    h = w = 2048
    arr = np.zeros((h, w), dtype="uint8")
    arr[: h // 2, :] = 1
    arr[h // 2 :, :] = 2
    src = tmp_path / "reprojected.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0001, 0.0001), nodata=0,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(arr, 1)

    dst = tmp_path / "cog.tif"
    convert_to_cog(str(src), str(dst))

    assert dst.exists() and dst.stat().st_size > 0
    is_valid, errors, warnings = cog_validate(str(dst))
    assert is_valid is True, f"not a valid COG: errors={errors} warnings={warnings}"
    assert errors == []

    with rasterio.open(dst) as d:
        assert d.overviews(1), "expected internal overviews for tile serving"
        assert d.profile["tiled"] is True
