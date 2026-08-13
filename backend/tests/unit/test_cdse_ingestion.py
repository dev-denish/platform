"""Unit tests for the CDSE ingestion keystone logic that doesn't need real
network/S3 access: vsis3 path conversion, SCL cloud-mask class selection, and
the merge-then-precise-clip mosaic path against synthetic in-memory rasters
(same style as test_raster_stats.py's tmp_path fixtures)."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services import cdse_ingestion as C


def test_to_vsis3_rewrites_s3_uri():
    assert C._to_vsis3("s3://eodata/foo/bar.jp2") == "/vsis3/eodata/foo/bar.jp2"


def test_to_vsis3_rejects_non_s3_href():
    with pytest.raises(C.CDSESearchError):
        C._to_vsis3("https://example.com/foo.jp2")


def test_invalid_scl_classes_exclude_cloud_not_snow_or_shadow_pixels():
    # 2 (dark area) and 11 (snow) must stay valid; 3/8/9/10 (cloud-family) and
    # 0/1 (no-data/defective) must be excluded - see module docstring.
    assert C._INVALID_SCL_CLASSES == {0, 1, 3, 8, 9, 10}
    for keep in (2, 4, 5, 6, 7, 11):
        assert keep not in C._INVALID_SCL_CLASSES


def _write_geotiff(path, arr, transform, crs="EPSG:32644", nodata=0):
    count, h, w = arr.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=count,
        dtype=arr.dtype, crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(arr)


def test_merge_and_clip_prefers_priority_scene_and_fills_its_gaps(tmp_path):
    """Two synthetic 2-band scenes on the identical grid: the priority scene
    has a masked (nodata) hole; the second scene has real data there. After
    merge, the hole must be filled from the second scene, and pixels the
    priority scene DID cover must still come from the priority scene."""
    h = w = 20
    transform = from_origin(500000, 1600000, 10, 10)

    priority = np.full((2, h, w), 5, dtype="uint16")
    priority[:, 5:10, 5:10] = 0  # cloud-masked hole

    fallback = np.full((2, h, w), 9, dtype="uint16")

    # A square AOI covering the whole synthetic grid in EPSG:4326 - real
    # coordinates aren't material here, only that transform_geom + crop
    # round-trip through the same grid without shrinking it away.
    from rasterio.warp import transform_bounds
    bounds = rasterio.transform.array_bounds(h, w, transform)
    lon_min, lat_min, lon_max, lat_max = transform_bounds(
        "EPSG:32644", "EPSG:4326", bounds[0], bounds[1], bounds[2], bounds[3]
    )
    aoi = {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min], [lon_max, lat_min],
            [lon_max, lat_max], [lon_min, lat_max], [lon_min, lat_min],
        ]],
    }

    import rasterio as rio
    from rasterio.crs import CRS

    merged, merged_transform, merged_crs = C._merge_and_clip(
        [
            (priority, transform, CRS.from_epsg(32644)),
            (fallback, transform, CRS.from_epsg(32644)),
        ],
        aoi,
        nodata=0,
    )

    assert merged.shape[0] == 2
    # Priority scene's real pixels win outside the hole.
    assert merged[0, 0, 0] == 5
    # The hole is filled from the fallback scene, not left at 0.
    assert merged[0, 7, 7] == 9


if __name__ == "__main__":
    # ponytail: smallest runnable self-check when pytest isn't installed
    # (known standing issue in this repo's live Docker images).
    test_to_vsis3_rewrites_s3_uri()
    test_invalid_scl_classes_exclude_cloud_not_snow_or_shadow_pixels()
    print("cdse_ingestion self-checks passed (vsis3 + SCL classes; merge/clip needs pytest)")
