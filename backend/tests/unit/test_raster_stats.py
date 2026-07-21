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


def _truth_band_stats(arr):
    valid = arr[arr != 0].astype(np.float64)
    pixel_ha = (10 * 10) / 10_000.0
    total = round(float(valid.size) * pixel_ha, 4)
    return (
        round(float(valid.min()), 4),
        round(float(valid.max()), 4),
        round(float(valid.mean()), 4),
        round(float(valid.std()), 4),
        total,
    )


def test_windowed_stats_exactly_match_naive(utm_lulc):
    """Without a legend there is no classification to report areas for - this
    proves the windowed band-stats pass matches a naive whole-array computation,
    not that it invents per-pixel-value "classes" (the bug being fixed)."""
    path, arr = utm_lulc
    minv, maxv, mean, std, total = _truth_band_stats(arr)
    # tiny block forces many windows; result must be identical to whole-array
    stats = R.compute_stats(path, legend=None, block=37)
    assert stats.class_area_ha is None
    assert stats.band_stats.min == pytest.approx(minv, abs=1e-6)
    assert stats.band_stats.max == pytest.approx(maxv, abs=1e-6)
    assert stats.band_stats.mean == pytest.approx(mean, abs=1e-6)
    assert stats.band_stats.stddev == pytest.approx(std, abs=1e-6)
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
    assert stats.band_stats is None
    assert any(k.startswith("Class ") for k in stats.class_area_ha)
    out = tmp_path / "prev.png"
    R.render_preview(path, str(out), legend, max_dim=64)
    assert out.exists() and out.stat().st_size > 0


def test_unmapped_pixel_values_bucket_into_unclassified(utm_lulc):
    """The main correctness fix: a legend that only names SOME of the raster's
    values must not turn the rest into their own fake per-value classes - they
    all collapse into one "Unclassified" total."""
    path, arr = utm_lulc
    pixel_ha = (10 * 10) / 10_000.0
    # fixture has values 1-5; only name 1 and 2, leave 3/4/5 unmapped
    legend = {"1": {"label": "Forest"}, "2": {"label": "Water"}}
    stats = R.compute_stats(path, legend=legend, block=200)
    assert set(stats.class_area_ha) == {"Forest", "Water", "Unclassified"}
    expected_unclassified = round(int(np.isin(arr, [3, 4, 5]).sum()) * pixel_ha, 4)
    assert stats.class_area_ha["Unclassified"] == pytest.approx(expected_unclassified, abs=1e-6)
    # total area is unaffected by the legend - still every non-nodata pixel
    assert stats.class_area_ha["Forest"] + stats.class_area_ha["Water"] + (
        stats.class_area_ha["Unclassified"]
    ) == pytest.approx(stats.total_area_ha, abs=1e-6)


def test_flat_string_legend_format_is_accepted(utm_lulc, tmp_path):
    """The upload form's documented legend shape is flat {"1": "Forest"} (no
    nested color) - must work for both stats and preview, not just the nested
    {"label", "color"} shape."""
    path, _ = utm_lulc
    legend = {str(i): f"Class {i}" for i in range(1, 6)}
    stats = R.compute_stats(path, legend=legend, block=200)
    assert set(stats.class_area_ha) == {f"Class {i}" for i in range(1, 6)}
    out = tmp_path / "prev_flat.png"
    R.render_preview(path, str(out), legend, max_dim=64)  # must not raise
    assert out.exists() and out.stat().st_size > 0


def test_empty_legend_is_treated_as_no_legend(utm_lulc):
    path, _ = utm_lulc
    stats = R.compute_stats(path, legend={}, block=200)
    assert stats.class_area_ha is None
    assert stats.band_stats is not None


@pytest.fixture
def utm_raw_multiband(tmp_path):
    """A synthetic raw/unclassified scene: 3 bands of smoothly-varying
    "reflectance" (a gradient, not discrete classes) - like a real satellite
    scene, unlike utm_lulc's handful-of-class-values raster."""
    h = w = 200
    rng = np.random.default_rng(7)
    bands = np.stack(
        [
            (np.linspace(0, 4000, w) + rng.normal(0, 50, (h, w))).clip(0, 4000)
            for _ in range(3)
        ]
    ).astype("uint16")
    path = tmp_path / "raw.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=0,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(bands)
    return str(path)


def test_no_legend_preview_is_a_real_composite_not_a_class_palette(utm_raw_multiband, tmp_path):
    """Regression test: render_preview used to run the SAME per-value
    class-color logic on band 1 regardless of legend, cycling through a
    12-color DEFAULT_PALETTE by `value % 12`. For continuous reflectance
    values that produces near-random speckle capped at 12 distinct output
    colors. A real band composite of a smooth gradient should produce a
    smooth gradient back out - many more than 12 distinct colors, and it must
    handle a genuinely multi-band source without error."""
    out = tmp_path / "raw_prev.png"
    R.render_preview(utm_raw_multiband, str(out), legend=None, max_dim=64)
    assert out.exists() and out.stat().st_size > 0

    from PIL import Image

    img = np.array(Image.open(out))
    assert img.shape[-1] == 4  # RGBA
    unique_colors = {tuple(px) for px in img.reshape(-1, 4)}
    assert len(unique_colors) > 12, (
        "output is capped at DEFAULT_PALETTE's 12 colors - the class-palette "
        "bug is back"
    )


@pytest.fixture
def utm_raw_multiband_no_nodata_half_padded(tmp_path):
    """Real data in the left half, warp-fill padding (exactly 0 in every
    band) in the right half, and crucially NO `nodata` value set at all -
    matches a real ingested "Satellite / Raw Imagery" layer confirmed to have
    nodata=None at every pipeline stage, whose rotated real footprint left
    zero-padding around it with nothing recorded to mark it as "not data"."""
    h = w = 200
    rng = np.random.default_rng(5)
    bands = np.zeros((3, h, w), dtype="uint16")
    real_data = (np.linspace(500, 3500, w // 2) + rng.normal(0, 80, (h, w // 2))).clip(0, 4000)
    for b in range(3):
        bands[b, :, : w // 2] = real_data
    path = tmp_path / "half_padded.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(bands)
    return str(path)


def test_no_nodata_padding_renders_transparent_not_opaque_black(
    utm_raw_multiband_no_nodata_half_padded, tmp_path
):
    """Regression test: without a `nodata` value, warp-fill padding (0 in
    every band) used to be treated as ordinary data - it both skewed the
    percentile stretch and painted solid opaque black instead of
    transparent. The padding half must come out transparent and the real-data
    half must keep genuine contrast, unskewed by the zeros next to it."""
    out = tmp_path / "half_padded_prev.png"
    R.render_preview(utm_raw_multiband_no_nodata_half_padded, str(out), legend=None, max_dim=200)

    from PIL import Image

    img = np.array(Image.open(out))
    left, right = img[:, : img.shape[1] // 2], img[:, img.shape[1] // 2 :]
    assert (right[:, :, 3] == 0).mean() > 0.9, "padding half must render (near-)fully transparent"
    visible_left = left[left[:, :, 3] > 0]
    assert visible_left.size > 0
    assert visible_left[:, :3].std() > 5, "real-data half must keep genuine contrast"


@pytest.fixture
def lulc_no_nodata_with_padding(tmp_path):
    """A classified LULC raster with a real, irregular classified footprint
    (values 1-9, like the real Bairluty legend) sitting inside a larger
    axis-aligned raster, warp-fill padding (0) filling everything outside
    that footprint, and crucially NO `nodata` value set at all - matches a
    real ingested classified layer confirmed to have nodata=None, whose
    Unclassified area came out as ~59% of the total (the padding, not a real
    ninth-plus class)."""
    h = w = 200
    arr = np.zeros((h, w), dtype="uint16")
    # An irregular (non-rectangular) real footprint, not just "one corner" -
    # a diagonal band - so this can't be mistaken for a legitimate rectangular
    # nodata border a real nodata tag would have caught anyway.
    rng = np.random.default_rng(9)
    for row in range(h):
        lo = max(0, row - 40)
        hi = min(w, row + 40)
        arr[row, lo:hi] = rng.integers(1, 10, size=hi - lo)
    real_pixel_count = int((arr != 0).sum())
    path = tmp_path / "lulc_padded.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)
    return str(path), real_pixel_count


def test_no_nodata_padding_excluded_from_area_not_bucketed_as_unclassified(
    lulc_no_nodata_with_padding,
):
    """The main bug this fix addresses: a classified raster with no real
    nodata tag had its warp-fill padding (value 0) silently counted as
    "Unclassified" area AND folded into Total Area - for a real dataset this
    was 59% of the reported total. Padding must be excluded from area
    entirely: it must not appear as "Unclassified", and Total Area must equal
    only the genuinely classified (non-zero) pixels."""
    path, real_pixel_count = lulc_no_nodata_with_padding
    legend = {str(i): {"label": f"Class {i}"} for i in range(1, 10)}
    stats = R.compute_stats(path, legend=legend, block=37)

    assert "Unclassified" not in stats.class_area_ha, (
        "warp-fill padding must be excluded entirely, not bucketed as a fake "
        "Unclassified class"
    )
    pixel_ha = (10 * 10) / 10_000.0
    expected_total = round(real_pixel_count * pixel_ha, 4)
    assert stats.total_area_ha == pytest.approx(expected_total, abs=1e-6), (
        "Total Area must cover only real classified pixels, not the padding "
        "around them"
    )
    assert sum(stats.class_area_ha.values()) == pytest.approx(stats.total_area_ha, abs=1e-6)


def test_legend_defined_class_zero_is_not_dropped_as_padding(tmp_path):
    """Regression test: `padding_value`'s no-nodata fallback used to treat
    value 0 as padding unconditionally, silently dropping a real,
    legend-defined class 0 (e.g. Dynamic World's Water=0) - it never showed
    up in class_area_ha and total_area_ha was understated by its entire
    area. A legend that names class 0 makes the padding heuristic inapplicable
    (there's no way to tell a real 0 from padding by value alone), so every
    pixel must count as real data."""
    h = w = 64
    arr = np.zeros((h, w), dtype="uint16")
    arr[:32, :] = 0  # Water
    arr[32:, :] = 1  # Forest
    path = tmp_path / "class_zero.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)

    legend = {"0": {"label": "Water"}, "1": {"label": "Forest"}}
    stats = R.compute_stats(str(path), legend=legend, block=37)
    pixel_ha = (10 * 10) / 10_000.0
    assert "Water" in stats.class_area_ha
    assert stats.class_area_ha["Water"] == pytest.approx(32 * w * pixel_ha, abs=1e-6)
    assert stats.class_area_ha["Forest"] == pytest.approx(32 * w * pixel_ha, abs=1e-6)
    assert stats.total_area_ha == pytest.approx(h * w * pixel_ha, abs=1e-6), (
        "every pixel is a real class - none should be excluded as padding"
    )


def test_accumulate_band_stats_checks_all_bands_for_padding(tmp_path):
    """Regression test: `_accumulate_band_stats` used to read ONLY band 1 -
    both to decide what's padding and to compute stats. A real pixel with
    band 1 == 0 but real data in other bands was wrongly excluded (band-1-
    only padding check), and true warp-fill padding (every band 0) was only
    excluded if band 1 itself happened to be 0. This raster has: a region
    where band 1 is genuinely 0 but bands 2/3 are real data (must be KEPT),
    and a separate region that is 0 in every band (must be EXCLUDED)."""
    h = w = 64
    bands = np.zeros((3, h, w), dtype="uint16")
    # rows 0-19: band 1 == 0 but bands 2/3 real - must count as real data
    bands[1, :20, :] = 500
    bands[2, :20, :] = 700
    # rows 20-39: ordinary real data in all bands
    bands[:, 20:40, :] = 300
    # rows 40-63: every band 0 - true warp-fill padding, must be excluded
    path = tmp_path / "multiband_padding.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(bands)

    stats = R.compute_stats(str(path), legend=None, block=17)
    pixel_ha = (10 * 10) / 10_000.0
    expected_count = 40 * w  # rows 0-39 are real; rows 40-63 are padding
    assert stats.total_area_ha == pytest.approx(expected_count * pixel_ha, abs=1e-6)
    # band 1's own stats over the real (unmasked) pixels: 20*w zeros + 20*w 300s
    assert stats.band_stats.min == pytest.approx(0.0, abs=1e-6)
    assert stats.band_stats.max == pytest.approx(300.0, abs=1e-6)


def test_genuinely_unlisted_real_value_still_reports_as_unclassified(tmp_path):
    """The other half of the fix's contract: ONLY padding is excluded - a
    real pixel value the legend simply doesn't name is still legitimate
    information and must still show up as Unclassified (this is not a
    license to hide every unmapped value, just the padding)."""
    h = w = 64
    arr = np.full((h, w), 1, dtype="uint16")
    arr[:10, :10] = 99  # a real, deliberate, unlisted class code - not padding
    path = tmp_path / "stray_value.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)

    legend = {"1": {"label": "Forest"}}
    stats = R.compute_stats(str(path), legend=legend, block=200)
    assert "Unclassified" in stats.class_area_ha
    pixel_ha = (10 * 10) / 10_000.0
    expected_unclassified = round(100 * pixel_ha, 4)  # the 10x10 block of value 99
    assert stats.class_area_ha["Unclassified"] == pytest.approx(expected_unclassified, abs=1e-6)
