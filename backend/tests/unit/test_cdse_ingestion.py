"""Unit tests for the CDSE ingestion keystone logic that doesn't need real
network/S3 access: vsis3 path conversion, SCL cloud-mask class selection, and
the merge-then-precise-clip mosaic path against synthetic in-memory rasters
(same style as test_raster_stats.py's tmp_path fixtures)."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
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


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 report-generation fix: PreparedRaster.aoi_pixel_count
# must be the TRUE polygon-footprint pixel count, not width*height (the
# bounding-box grid `rasterio.mask.mask(..., crop=True)` produces) - proven
# with a genuinely non-rectangular AOI whose bbox pixel count is
# provably larger than its own true area.
# --------------------------------------------------------------------------


def _fake_settings():
    from types import SimpleNamespace

    return SimpleNamespace(
        cdse_client_id="x", cdse_client_secret="x",
        cdse_s3_access_key="x", cdse_s3_secret_key="x",
    )


def test_prepared_raster_aoi_pixel_count_is_less_than_bbox_for_a_triangular_aoi(tmp_path):
    """A right-triangle AOI (3 of the square grid's 4 corners) has a bbox
    identical to the full square grid, but a true area of only about half of
    it - proof `aoi_pixel_count` reads the true polygon footprint, not
    `width * height`."""
    h = w = 40
    transform = from_origin(500000, 1600000, 10, 10)
    scene_arr = np.full((6, h, w), 3000, dtype="uint16")

    bounds = rasterio.transform.array_bounds(h, w, transform)
    from rasterio.warp import transform_bounds
    lon_min, lat_min, lon_max, lat_max = transform_bounds(
        "EPSG:32644", "EPSG:4326", bounds[0], bounds[1], bounds[2], bounds[3]
    )
    # Triangle covering only the lower-left half of the square - its bbox
    # (computed from these same 3 points) still spans the FULL square, since
    # it includes both the (lon_min, lat_min) and (lon_max, lat_max) corners.
    triangle_aoi = {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min], [lon_max, lat_min], [lon_min, lat_max], [lon_min, lat_min],
        ]],
    }

    client = C.CDSEClient(_fake_settings())
    prepared = client._prepare(
        scenes=[{"id": "s1", "properties": {"datetime": "2026-01-01", "eo:cloud_cover": 5.0}}],
        collection="sentinel-2-l2a",
        band_names=("B02", "B03", "B04", "B08", "B11", "B12"),
        aoi_4326=triangle_aoi,
        output_path=str(tmp_path / "raster.tif"),
        process_scene=lambda s: (scene_arr, transform, CRS.from_epsg(32644)),
    )

    bbox_pixel_count = prepared.width * prepared.height
    assert prepared.aoi_pixel_count < bbox_pixel_count
    assert prepared.aoi_pixel_count > 0
    # Roughly half the bbox (a right triangle is exactly half its bounding
    # rectangle) - generous tolerance for edge-pixel rasterization.
    assert 0.35 * bbox_pixel_count < prepared.aoi_pixel_count < 0.65 * bbox_pixel_count


def test_prepared_raster_aoi_pixel_count_equals_bbox_for_a_rectangular_aoi(tmp_path):
    """No-op check: a genuinely rectangular AOI (matching its own bbox
    exactly) must not lose any pixels to this fix - `aoi_pixel_count` should
    equal (or be a near-total match of) the full grid."""
    h = w = 20
    transform = from_origin(500000, 1600000, 10, 10)
    scene_arr = np.full((6, h, w), 3000, dtype="uint16")

    bounds = rasterio.transform.array_bounds(h, w, transform)
    from rasterio.warp import transform_bounds
    lon_min, lat_min, lon_max, lat_max = transform_bounds(
        "EPSG:32644", "EPSG:4326", bounds[0], bounds[1], bounds[2], bounds[3]
    )
    rectangular_aoi = {
        "type": "Polygon",
        "coordinates": [[
            [lon_min, lat_min], [lon_max, lat_min],
            [lon_max, lat_max], [lon_min, lat_max], [lon_min, lat_min],
        ]],
    }

    client = C.CDSEClient(_fake_settings())
    prepared = client._prepare(
        scenes=[{"id": "s1", "properties": {"datetime": "2026-01-01", "eo:cloud_cover": 5.0}}],
        collection="sentinel-2-l2a",
        band_names=("B02", "B03", "B04", "B08", "B11", "B12"),
        aoi_4326=rectangular_aoi,
        output_path=str(tmp_path / "raster.tif"),
        process_scene=lambda s: (scene_arr, transform, CRS.from_epsg(32644)),
    )

    bbox_pixel_count = prepared.width * prepared.height
    assert prepared.aoi_pixel_count == bbox_pixel_count


if __name__ == "__main__":
    # ponytail: smallest runnable self-check when pytest isn't installed
    # (known standing issue in this repo's live Docker images).
    test_to_vsis3_rewrites_s3_uri()
    test_invalid_scl_classes_exclude_cloud_not_snow_or_shadow_pixels()
    print("cdse_ingestion self-checks passed (vsis3 + SCL classes; merge/clip needs pytest)")
