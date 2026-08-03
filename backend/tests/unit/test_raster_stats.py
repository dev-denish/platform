"""Unit tests for the raster keystone. Proves the windowed (memory-bounded) path is
numerically identical to a naive whole-array computation, that area is measured in a
projected/equal-area CRS (not degrees), and that reprojection yields valid lon/lat."""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

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


def test_has_real_mask_true_after_reproject_false_on_a_plain_file(utm_lulc, tmp_path):
    """`reproject_to_4326`'s output always carries a real geometric mask -
    `has_real_mask` must say so. A plain file nobody ever ran through the
    warp (e.g. a legacy pre-fix COG) has none - the exact case
    ProjectService._needs_reingestion flags for re-upload."""
    path, _ = utm_lulc
    dst = tmp_path / "reproj_masked.tif"
    R.reproject_to_4326(path, str(dst), block=128)
    assert R.has_real_mask(str(dst)) is True
    assert R.has_real_mask(path) is False


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
def utm_raw_multiband_no_nodata_half_zero(tmp_path):
    """Real data in the left half, ordinary zeros baked directly into every
    band's pixel VALUES in the right half, NO `nodata` value set, and NO
    rotation/CRS mismatch either - an axis-aligned, flat raster exactly as
    it would be handed to this app by an external tool that already
    zero-filled part of it before upload.

    See test_baked_in_zero_across_all_bands_is_excluded_from_a_raw_composite
    below - Wave: production render audit reconsiders this exact fixture's
    verdict for the 3-band composite path specifically."""
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


def test_baked_in_zero_across_all_bands_is_excluded_from_a_raw_composite(
    utm_raw_multiband_no_nodata_half_zero, tmp_path
):
    """Wave: production render audit reverses this fixture's previous verdict
    for the 3-band composite path specifically - proven against a real ad-hoc
    upload rendering as white/washed-out patches despite a geometric mask
    reporting 100% valid (no rotation/reprojection mismatch for `add_alpha`
    to reveal, since the source needed no reprojection at all).

    Why this reversal doesn't reopen the bug the OLD `padding_value()`
    heuristic caused (see test_legend_defined_class_zero_is_not_dropped_as_
    padding below): that bug was a single CLASSIFIED band where a real
    legend can define class value 0 as meaningful data (e.g. Water=0) - one
    band's 0 is real. This fixture has 3 INDEPENDENT bands landing on the
    exact same sentinel at once, with no legend at all - a real
    reflectance/DN measurement varies at least slightly band-to-band, so
    `_band_composite_rgba` now treats all-3-bands-exactly-0 as fill, same as
    tile_renderer._mask_uninitialized_fill. Fixing a genuinely ambiguous
    SINGLE band's zero still needs a real nodata tag - that limitation is
    unchanged and is exactly what test_no_nodata_zero_fill_without_rotation_
    counts_as_unclassified (classified, single-band) still documents."""
    out = tmp_path / "half_zero_prev.png"
    R.render_preview(utm_raw_multiband_no_nodata_half_zero, str(out), legend=None, max_dim=200)

    from PIL import Image

    img = np.array(Image.open(out))
    left, right = img[:, : img.shape[1] // 2], img[:, img.shape[1] // 2 :]
    assert (right[:, :, 3] == 0).mean() > 0.9, (
        "baked-in zero across all 3 bands at once must now be excluded as "
        "fill, not rendered as real (if visually flat) data"
    )
    visible_left = left[left[:, :, 3] > 0]
    assert visible_left.size > 0
    assert visible_left[:, :3].std() > 5, "real-data half must keep genuine contrast"


@pytest.fixture
def lulc_no_nodata_with_zero_fill(tmp_path):
    """A classified LULC raster with a real, irregular classified footprint
    (values 1-9, like the real Bairluty legend) sitting inside a larger
    axis-aligned raster, ordinary zeros filling everything outside that
    footprint, and crucially NO `nodata` value set and NO rotation - matches
    a real ingested classified layer confirmed to have nodata=None. Renamed
    from `..._with_padding` (Wave: geometric padding fix) - see
    test_no_nodata_zero_fill_without_rotation_counts_as_unclassified below
    for why this is no longer treated as padding."""
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


def test_no_nodata_zero_fill_without_rotation_counts_as_unclassified(
    lulc_no_nodata_with_zero_fill,
):
    """Documents the wave's own explicitly stated limitation - this is NOT a
    bug: this fixture's zero-fill is baked directly into an axis-aligned,
    non-rotated raster's ordinary pixel values, with no `nodata` tag - there
    is no warp-introduced geometric mismatch for `add_alpha` to reveal, so
    it's indistinguishable from real data. Now that `padding_value()`'s
    value-based guess is gone with NO fallback, every pixel counts: the
    unlisted 0s land in "Unclassified" - the SAME rule any other
    legend-unlisted real value already gets (see
    test_genuinely_unlisted_real_value_still_reports_as_unclassified) -
    and Total Area is genuinely the FULL raster. See this module's own
    docstring for why: fixing this specific pattern needs the source to
    carry a real nodata tag, which no warp-time mask can invent."""
    path, real_pixel_count = lulc_no_nodata_with_zero_fill
    legend = {str(i): {"label": f"Class {i}"} for i in range(1, 10)}
    stats = R.compute_stats(path, legend=legend, block=37)

    h = w = 200
    pixel_ha = (10 * 10) / 10_000.0
    print(f"real (non-zero) pixels: {real_pixel_count}, full raster: {h * w}")
    print(f"MEASURED total_area_ha: {stats.total_area_ha}, Unclassified: {stats.class_area_ha.get('Unclassified')}")

    assert "Unclassified" in stats.class_area_ha, (
        "without a nodata tag or geometric mismatch, the zero-fill is no "
        "longer excluded - it's ordinary unlisted data, same as any other "
        "legend-unlisted value"
    )
    expected_unclassified = round((h * w - real_pixel_count) * pixel_ha, 4)
    assert stats.class_area_ha["Unclassified"] == pytest.approx(expected_unclassified, abs=1e-6)
    assert stats.total_area_ha == pytest.approx(h * w * pixel_ha, abs=1e-6), (
        "every pixel counts now - there's no safe way to guess which zeros "
        "are real vs fill without a nodata tag or a genuine warp mismatch"
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
    """Wave: production render audit / area-accuracy follow-up reverses part
    of this test's previous verdict: proven against 4 real ingested layers
    where Total Area was inflated by ~59% - an exact 0 in every one of >=3
    real bands at once is now excluded, same as
    tile_renderer._mask_uninitialized_fill already excludes it from
    rendering.

    What's UNCHANGED and still the critical regression guard - the actual
    Wave H bug: band 1 reading 0 while other bands carry real data (rows
    0-19) must still always count as real. Only a pixel where EVERY band
    reads 0 AT ONCE (rows 40-63) is now excluded - a lone dark band is never
    enough on its own."""
    h = w = 64
    bands = np.zeros((3, h, w), dtype="uint16")
    # rows 0-19: band 1 == 0 but bands 2/3 real - must still count as real data
    bands[1, :20, :] = 500
    bands[2, :20, :] = 700
    # rows 20-39: ordinary real data in all bands
    bands[:, 20:40, :] = 300
    # rows 40-63: every band 0 at once, no nodata, no rotation - now excluded
    path = tmp_path / "multiband_padding.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:32643", transform=from_origin(640000, 1445000, 10, 10), nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(bands)

    stats = R.compute_stats(str(path), legend=None, block=17)
    pixel_ha = (10 * 10) / 10_000.0
    real_rows = 40  # rows 0-39 only - rows 40-63 are now excluded fake fill
    print(f"MEASURED total_area_ha: {stats.total_area_ha} (real rows only: {real_rows * w * pixel_ha})")
    assert stats.total_area_ha == pytest.approx(real_rows * w * pixel_ha, abs=1e-6), (
        "rows 40-63 (every band 0 at once) must now be excluded as fake fill"
    )
    # band 1's own stats over the real rows only: 20*w at 0 (rows 0-19, a
    # legitimately dark band 1 with real data elsewhere) + 20*w at 300 (rows
    # 20-39) - rows 40-63's 0s are gone, but band 1's min was already 0 from
    # rows 0-19, so min is unaffected; this is the proof that a real dark
    # band is untouched by the fix.
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


# ============================================================ Wave: geometric padding fix


@pytest.fixture
def rotated_lulc_with_class_zero(tmp_path):
    """A REAL rotated raster - genuine geotransform rotation (b, d != 0 in
    the affine transform), like a locally-surveyed plot grid not aligned to
    true north - 100% real classified data (including class 0 = Water), NO
    padding baked into the source at all. Every consumer of this module
    reads through a north-up `WarpedVRT` (including `compute_stats` itself,
    now unconditionally), which necessarily introduces real corner fill
    outside the rotated footprint once forced onto a north-up grid. This is
    the exact scenario the geometric mask fix is for, built with a REAL
    GDAL warp (this fixture's own rotated transform), not a numpy
    simulation of padding."""
    h = w = 200
    arr = np.zeros((h, w), dtype="uint16")
    arr[: h // 2, :] = 0  # Water - legend-defined real class 0
    arr[h // 2 :, :] = 1  # Forest
    angle = np.radians(20)
    transform = Affine(
        10 * np.cos(angle), -10 * np.sin(angle), 640000,
        10 * np.sin(angle), 10 * np.cos(angle), 1445000,
    )
    path = tmp_path / "rotated.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=transform, nodata=None,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)
    # Independent ground truth, computed from the SOURCE's own geometry
    # directly - not from anything this module computes. Rotation changes
    # neither pixel count nor pixel area, only orientation, so the true
    # area is exactly the source's own pixel count * its own pixel area.
    pixel_ha = abs(transform.a * transform.e - transform.b * transform.d) / 10_000.0
    truth_water_ha = round((h // 2) * w * pixel_ha, 4)
    truth_forest_ha = round((h - h // 2) * w * pixel_ha, 4)
    return str(path), truth_water_ha, truth_forest_ha


def test_rotated_real_warp_with_class_zero_matches_ground_truth(rotated_lulc_with_class_zero):
    """The exact bug this wave fixes, rebuilt with a REAL GDAL warp (genuine
    geotransform rotation), not a numpy simulation: a real, legend-defined
    class 0 (Water) in a raster whose own rotation forces compute_stats'
    now-unconditional north-up warp to introduce real corner fill. The OLD
    `padding_value()` would have given up entirely the moment the legend
    named class 0 (returning None - "no safe padding value" - so it never
    excluded the corner fill at all, counting it as real Forest/Water and
    overstating both). The NEW geometric mask excludes ONLY the genuine
    corner fill, independent of pixel value, so Water=0 is fully kept and
    the fill is fully dropped."""
    path, truth_water_ha, truth_forest_ha = rotated_lulc_with_class_zero
    legend = {"0": {"label": "Water"}, "1": {"label": "Forest"}}
    stats = R.compute_stats(path, legend=legend, block=37)

    measured_water = stats.class_area_ha.get("Water")
    measured_forest = stats.class_area_ha.get("Forest")
    print(f"GROUND TRUTH: Water={truth_water_ha} ha, Forest={truth_forest_ha} ha, "
          f"total={truth_water_ha + truth_forest_ha} ha")
    print(f"MEASURED:     Water={measured_water} ha, Forest={measured_forest} ha, "
          f"total={stats.total_area_ha} ha")

    assert "Water" in stats.class_area_ha
    assert measured_water == pytest.approx(truth_water_ha, rel=0.01)
    assert measured_forest == pytest.approx(truth_forest_ha, rel=0.01)
    assert "Unclassified" not in stats.class_area_ha, (
        "the rotated corner fill must be excluded entirely by the geometric "
        "mask, not bucketed as a fake Unclassified class"
    )
    assert stats.total_area_ha == pytest.approx(truth_water_ha + truth_forest_ha, rel=0.01)


def test_rotated_source_with_real_padding_tag_still_respects_it(tmp_path):
    """A rotated source that DOES carry a real `nodata` tag (a legitimate
    no-data region within its own real footprint, e.g. a cloud mask) must
    still have that respected - `add_alpha` tracks BOTH the declared nodata
    AND the geometric corner fill from de-rotation, simultaneously, not
    one at the expense of the other."""
    h = w = 100
    arr = np.full((h, w), 1, dtype="uint16")  # Forest everywhere
    arr[:20, :] = 5  # an explicit nodata region WITHIN the real rotated footprint
    angle = np.radians(15)
    transform = Affine(
        10 * np.cos(angle), -10 * np.sin(angle), 640000,
        10 * np.sin(angle), 10 * np.cos(angle), 1445000,
    )
    path = tmp_path / "rotated_with_nodata.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=transform, nodata=5,
    )
    with rasterio.open(path, "w", **profile) as d:
        d.write(arr, 1)

    pixel_ha = abs(transform.a * transform.e - transform.b * transform.d) / 10_000.0
    truth_forest_ha = round((h - 20) * w * pixel_ha, 4)

    legend = {"1": {"label": "Forest"}}
    stats = R.compute_stats(str(path), legend=legend, block=23)
    print(f"GROUND TRUTH Forest: {truth_forest_ha} ha; MEASURED: {stats.class_area_ha.get('Forest')} ha")
    assert stats.class_area_ha["Forest"] == pytest.approx(truth_forest_ha, rel=0.01)
    assert "Unclassified" not in stats.class_area_ha


def test_padding_value_is_removed(tmp_path):
    """Confirms `padding_value` is actually gone from the module - not left
    dead/unreachable - so nothing can silently start calling it again."""
    assert not hasattr(R, "padding_value")
    assert not hasattr(R, "legend_defines")
