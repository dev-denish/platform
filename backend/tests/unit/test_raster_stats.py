"""Unit tests for the raster keystone. Proves the windowed (memory-bounded) path is
numerically identical to a naive whole-array computation, that area is measured in a
projected/equal-area CRS (not degrees), and that reprojection yields valid lon/lat."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.ingestion import raster as R


@pytest.fixture
def utm_lulc(tmp_path):
    """A synthetic classified LULC raster in EPSG:32643 (UTM 43N), 10 m pixels, with a
    block of nodata."""
    h = w = 400
    rng = np.random.default_rng(42)
    arr = rng.integers(1, 6, size=(h, w)).astype("uint8")
    arr[:40, :] = 0  # nodata rows
    path = tmp_path / "lulc.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)
    return str(path), arr


def _truth(arr):
    vals, cnts = np.unique(arr[arr != 0], return_counts=True)
    pixel_ha = (10 * 10) / 10_000.0
    per_class = {f"class_{v}": round(c * pixel_ha, 4) for v, c in zip(vals.tolist(), cnts.tolist())}
    total = round(sum(cnts.tolist()) * pixel_ha, 4)
    return per_class, total


def test_windowed_stats_exactly_match_naive(utm_lulc):
    path, arr = utm_lulc
    per_class, total = _truth(arr)
    # tiny block forces many windows; result must be identical to whole-array
    stats = R.compute_stats(path, legend=None, block=37)
    assert stats.class_area_ha == per_class
    assert stats.total_area_ha == pytest.approx(total, abs=1e-6)


def test_area_measured_in_projected_metres(utm_lulc):
    path, _ = utm_lulc
    stats = R.compute_stats(path, legend=None, block=128)
    # native CRS is projected metres -> area measured there (exact), not in degrees
    assert "32643" in stats.area_crs


def test_reprojection_produces_valid_lonlat_bounds(utm_lulc, tmp_path):
    path, _ = utm_lulc
    dst = tmp_path / "reproj.tif"
    src_crs, bounds = R.reproject_to_4326(path, str(dst), block=128)
    assert "32643" in src_crs
    minx, miny, maxx, maxy = bounds
    assert -180 <= minx <= 180 and -90 <= miny <= 90
    assert maxx > minx and maxy > miny
    with rasterio.open(dst) as d:
        assert d.crs.to_epsg() == 4326


def test_legend_labels_and_preview(utm_lulc, tmp_path):
    path, _ = utm_lulc
    legend = {str(i): {"label": f"Class {i}", "color": "#228b22"} for i in range(1, 6)}
    stats = R.compute_stats(path, legend=legend, block=200)
    assert any(k.startswith("Class ") for k in stats.class_area_ha)
    out = tmp_path / "prev.png"
    R.render_preview(path, str(out), legend, max_dim=64)
    assert out.exists() and out.stat().st_size > 0
