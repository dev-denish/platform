"""Unit tests for XYZ tile rendering from a real COG (Phase 3 Wave A). No mocked
tile data - a real classified raster is generated and converted to a real COG
via rio-cogeo, exactly like the ingest pipeline does, then rendered with the
real rio-tiler code path."""
from __future__ import annotations

import morecantile
import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin
from rio_tiler.errors import TileOutsideBounds

from app.core.errors import ValidationError
from app.services.ingestion.cog import convert_to_cog
from app.services.ingestion.raster import reproject_to_4326
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


def _multiband_cog_path(tmp_path, band_count: int):
    """A genuine multi-band raw/unclassified scene (smooth gradient + noise,
    like real reflectance data) - unlike `cog_path` above, which is a
    single-band CLASSIFIED raster (a handful of discrete class values)."""
    h = w = 256
    rng = np.random.default_rng(3)
    bands = np.stack(
        [
            (np.linspace(0, 4000, w) + rng.normal(0, 80, (h, w))).clip(0, 4000)
            for _ in range(band_count)
        ]
    ).astype("uint16")
    src = tmp_path / "raw_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=band_count, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=0,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(bands)
    dst = tmp_path / "raw_cog.tif"
    convert_to_cog(str(src), str(dst))
    return str(dst)


def test_multiband_raw_imagery_tile_renders_without_crashing(tmp_path):
    """Regression test: this used to raise
    rio_tiler.errors.InvalidFormat('Source data must be 1 band') for any
    layer with more than 1 band, since a classified colormap was built and
    applied unconditionally - Satellite/Raw Imagery's real multi-band scenes
    hit this on every tile request."""
    path = _multiband_cog_path(tmp_path, band_count=16)
    z, x, y = _tile_covering_center(path)
    png = render_tile(path, z, x, y)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_multiband_tile_has_real_structure_not_a_flat_colormap_artifact(tmp_path):
    """A smooth input gradient must render as a smooth (many-colored) output,
    not the old bug's near-random per-pixel noise capped at a 12-color
    palette."""
    path = _multiband_cog_path(tmp_path, band_count=3)
    z, x, y = _tile_covering_center(path)
    png = render_tile(path, z, x, y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    unique_colors = {tuple(px) for px in img.reshape(-1, img.shape[-1])}
    assert len(unique_colors) > 12


@pytest.fixture
def rotated_multiband_cog(tmp_path):
    """A REAL rotated source (genuine geotransform rotation - like a
    locally-surveyed plot grid not aligned to true north), 100% real
    multi-band data, NO nodata tag, routed through the ACTUAL ingestion
    pipeline (reproject_to_4326 -> convert_to_cog) - not a hand-built "half
    the raster is flat zeros" simulation. reproject_to_4326's now-mandatory
    north-up warp (Wave: geometric padding fix) necessarily introduces real
    corner fill outside this rotated footprint, and writes a REAL internal
    mask marking exactly that - which rio-cogeo then carries into the COG
    unchanged. Big enough (512px, 10m pixels) that z=15 covers it with
    multiple distinct tiles, so a "real data" tile and a "genuine fill"
    tile can be requested independently."""
    h = w = 512
    rng = np.random.default_rng(11)
    bands = np.zeros((3, h, w), dtype="uint16")
    for b in range(3):
        bands[b] = (np.linspace(500, 3500, w) + rng.normal(0, 100, (h, w))).clip(0, 4000)
    angle = np.radians(20)
    transform = Affine(
        10 * np.cos(angle), -10 * np.sin(angle), 640000,
        10 * np.sin(angle), 10 * np.cos(angle), 1445000,
    )
    src = tmp_path / "rotated_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:32643", transform=transform, nodata=None,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(bands)

    reproj = tmp_path / "reproj.tif"
    reproject_to_4326(str(src), str(reproj))
    dst = tmp_path / "rotated_cog.tif"
    convert_to_cog(str(reproj), str(dst))
    with rasterio.open(dst) as d:
        return str(dst), d.bounds


def test_all_padding_tile_renders_fully_transparent_not_solid_black(rotated_multiband_cog):
    """The COG's real internal mask - written by reproject_to_4326 from
    this rotated source's genuine corner fill (Wave: geometric padding fix),
    carried through COG conversion unchanged - must render fully
    transparent, not opaque black, for a tile landing entirely in that
    fill. A corner of the axis-aligned bounding box is, by construction,
    outside the rotated real footprint."""
    path, bounds = rotated_multiband_cog
    px = bounds.left + (bounds.right - bounds.left) * 0.02
    py = bounds.top - (bounds.top - bounds.bottom) * 0.02
    t = _TMS.tile(px, py, 15)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert img.shape[-1] == 4
    assert (img[:, :, 3] == 0).all(), "genuine warp-fill corner must render fully transparent"


def test_real_data_tile_is_not_corrupted_by_neighboring_padding(rotated_multiband_cog):
    """The real-data centre must render with genuine visible structure and
    correct exposure, unskewed by the surrounding corner fill dragging down
    the percentile stretch."""
    path, bounds = rotated_multiband_cog
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(cx, cy, 15)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    visible = img[img[:, :, 3] > 0]
    assert visible.size > 0, "real-data tile must have visible (non-transparent) pixels"
    assert visible[:, :3].std() > 5, "visible pixels must show real contrast, not a flat fill"


def test_real_varied_multiband_data_has_no_false_positive_masking(rotated_multiband_cog):
    """Regression guard for `_mask_uninitialized_fill` (Wave: production
    render audit): this fixture's real-data tile is genuine gaussian-noise-
    on-a-gradient data starting at a 500 baseline - it must never land on an
    exact 0 in all 3 bands at once, so the new value-based fallback must not
    introduce any transparency an already-correctly-masked layer didn't
    already have."""
    path, bounds = rotated_multiband_cog
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(cx, cy, 15)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert (img[:, :, 3] > 0).all(), "no new transparency on data with no baked-in fill"


def test_deep_water_and_shadow_are_not_falsely_masked(tmp_path):
    """The risky case for `_mask_uninitialized_fill`, not the easy one: a
    legitimately very dark real feature (deep water, heavy cloud shadow)
    with LOW per-band sensor noise (1-15 DN, not one fixed value) rather
    than uniform mid-range noise - closer to what a real dark region
    actually looks like than the other fixtures' brighter random data.
    Random per-pixel noise means it essentially never lands on an exact 0
    in all 3 bands at the SAME pixel, so it must render fully opaque with
    real (if low) contrast - not get swept up as fake fill just because
    it's dark.

    Known residual limitation, stated plainly (same category as
    reproject_to_4326's own "Known limitation" for the geometric mask): a
    real feature that some upstream tool already hard-clipped to a literal
    integer 0 in every band (not just very low values) - e.g. an 8-bit
    product that saturates a dark region to flat black - would still be
    indistinguishable from fill. Nothing short of a real per-file nodata
    tag can resolve that; this test proves the ordinary case (real sensor
    noise, however dark) is safe, not that every conceivable dark encoding is."""
    h = w = 512
    rng = np.random.default_rng(23)
    bands = rng.integers(1, 15, size=(3, h, w)).astype("uint16")  # deep water/shadow DN range
    # a bright feature elsewhere so the tile isn't uniformly near-black
    bands[:, h // 2 :, w // 2 :] = rng.integers(1500, 2500, size=(h // 2, w // 2))
    src = tmp_path / "dark_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=None,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(bands)
    reproj = tmp_path / "reproj_dark.tif"
    reproject_to_4326(str(src), str(reproj))
    dst = tmp_path / "dark_cog.tif"
    convert_to_cog(str(reproj), str(dst))

    z, x, y = _tile_covering_center(str(dst))
    png = render_tile(str(dst), z, x, y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert (img[:, :, 3] > 0).all(), "real dark/noisy water or shadow must not render as fake fill"


@pytest.fixture
def baked_in_padding_multiband_cog(tmp_path):
    """A REAL, NOT rotated, 3-band source (no nodata tag) whose own pixel
    array already has a solid block where every band is an exact 0 at once
    - fill baked into ordinary values before this ever reached the ingest
    pipeline, mirroring a real ad-hoc upload this bug was found against.
    Because there is no rotation/reprojection mismatch, `reproject_to_4326`'s
    warp is a lossless passthrough (see its "Known limitation" docstring):
    its real geometric mask has nothing to reveal here and reports the
    whole file as 100% valid - `_mask_uninitialized_fill` is the only thing
    that can still catch this block."""
    h = w = 512
    rng = np.random.default_rng(13)
    bands = rng.integers(100, 2000, size=(3, h, w)).astype("uint16")
    bands[:, : h // 2, : w // 2] = 0  # baked-in fill, all 3 bands simultaneously
    src = tmp_path / "baked_padding_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=None,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(bands)

    reproj = tmp_path / "reproj_baked.tif"
    reproject_to_4326(str(src), str(reproj))
    with rasterio.open(reproj) as d:
        assert d.dataset_mask().min() == 255, (
            "sanity check: the geometric mask alone must report this file as "
            "fully valid - proves the gap this fixture targets actually exists"
        )
    dst = tmp_path / "baked_padding_cog.tif"
    convert_to_cog(str(reproj), str(dst))
    with rasterio.open(dst) as d:
        return str(dst), d.bounds


def test_baked_in_zero_padding_renders_transparent_despite_a_valid_geometric_mask(
    baked_in_padding_multiband_cog,
):
    """The bug this fixes: a geometric mask that reports 100% valid must not
    be the only thing consulted - `_mask_uninitialized_fill` must still
    exclude the baked-in fill block."""
    path, bounds = baked_in_padding_multiband_cog
    px = bounds.left + (bounds.right - bounds.left) * 0.25
    py = bounds.top - (bounds.top - bounds.bottom) * 0.25
    t = _TMS.tile(px, py, 15)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert (img[:, :, 3] == 0).any(), "baked-in fill block must render transparent"


def test_real_data_tile_is_not_washed_out_by_baked_in_padding_elsewhere(
    baked_in_padding_multiband_cog,
):
    """The bug as it actually appeared: a tile's own real data, with no
    padding of its own, still got its percentile stretch skewed toward
    white by whatever padding fraction landed in the SAME tile - here the
    real-data quadrant must render with genuine contrast, not washed out."""
    path, bounds = baked_in_padding_multiband_cog
    px = bounds.left + (bounds.right - bounds.left) * 0.75
    py = bounds.top - (bounds.top - bounds.bottom) * 0.75
    t = _TMS.tile(px, py, 15)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    visible = img[img[:, :, 3] > 0]
    assert visible.size > 0
    assert visible[:, :3].std() > 5, "real data must show genuine contrast, not near-white flatness"


# ---------------------------------------------------------- symbology (Wave F)


@pytest.fixture
def distinct_bands_cog(tmp_path):
    """4 bands, each a DISTINCT deterministic pattern - so picking a different
    band-to-channel assignment for the same tile must produce a demonstrably
    different image, not just 'plausibly different'."""
    h = w = 256
    row = np.arange(w)
    band1 = np.tile(row, (h, 1))
    band2 = np.tile(row[::-1], (h, 1))
    band3 = np.tile(np.arange(h).reshape(-1, 1), (1, w))
    band4 = np.full((h, w), 2000)
    bands = (np.stack([band1, band2, band3, band4]) * 10).astype("uint16")
    src = tmp_path / "distinct_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=4, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=0,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(bands)
    dst = tmp_path / "distinct_cog.tif"
    convert_to_cog(str(src), str(dst))
    return str(dst)


def test_explicit_band_selection_changes_rendered_pixels(distinct_bands_cog):
    """The core symbology verification: choosing a DIFFERENT band-to-channel
    assignment for the identical tile must produce a demonstrably different
    image - an actual pixel diff, not just distinct-looking output."""
    z, x, y = _tile_covering_center(distinct_bands_cog)
    png_123 = render_tile(distinct_bands_cog, z, x, y, bands=(1, 2, 3))
    png_432 = render_tile(distinct_bands_cog, z, x, y, bands=(4, 3, 2))
    assert png_123 != png_432, "different band assignments produced byte-identical tiles"

    import io

    from PIL import Image

    img_123 = np.array(Image.open(io.BytesIO(png_123)))[:, :, :3].astype(int)
    img_432 = np.array(Image.open(io.BytesIO(png_432)))[:, :, :3].astype(int)
    assert np.abs(img_123 - img_432).mean() > 10, "pixel output barely moved despite a full band reassignment"


def test_out_of_range_band_raises_validation_error_not_a_crash(distinct_bands_cog):
    z, x, y = _tile_covering_center(distinct_bands_cog)
    with pytest.raises(ValidationError):
        render_tile(distinct_bands_cog, z, x, y, bands=(1, 2, 9))  # only 4 bands exist


def test_custom_stretch_changes_output(distinct_bands_cog):
    z, x, y = _tile_covering_center(distinct_bands_cog)
    png_default = render_tile(distinct_bands_cog, z, x, y, bands=(1, 1, 1))
    png_narrow = render_tile(distinct_bands_cog, z, x, y, bands=(1, 1, 1), stretch=(40, 60))
    assert png_default != png_narrow


@pytest.fixture
def classified_cog_with_legend(tmp_path):
    h = w = 256
    arr = np.zeros((h, w), dtype="uint8")
    arr[: h // 2, :] = 1
    arr[h // 2 :, :] = 2
    src = tmp_path / "cls_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=0,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(arr, 1)
    dst = tmp_path / "cls_cog.tif"
    convert_to_cog(str(src), str(dst))
    return str(dst)


def test_classified_mode_uses_the_persisted_legend_color(classified_cog_with_legend):
    z, x, y = _tile_covering_center(classified_cog_with_legend)
    legend = {"1": {"label": "Forest", "color": "#123456"}, "2": {"label": "Water", "color": "#abcdef"}}
    png = render_tile(classified_cog_with_legend, z, x, y, legend=legend)

    import io

    from PIL import Image

    colors = {tuple(px) for px in np.array(Image.open(io.BytesIO(png))).reshape(-1, 4)}
    assert (0x12, 0x34, 0x56, 235) in colors or (0xAB, 0xCD, 0xEF, 235) in colors


def test_color_override_replaces_the_legend_color(classified_cog_with_legend):
    z, x, y = _tile_covering_center(classified_cog_with_legend)
    legend = {"1": {"label": "Forest", "color": "#123456"}}
    png_default = render_tile(classified_cog_with_legend, z, x, y, legend=legend)
    png_overridden = render_tile(
        classified_cog_with_legend, z, x, y, legend=legend, color_overrides={"1": "#ff0000"}
    )
    assert png_default != png_overridden

    import io

    from PIL import Image

    colors = {tuple(px) for px in np.array(Image.open(io.BytesIO(png_overridden))).reshape(-1, 4)}
    assert (0xFF, 0x00, 0x00, 235) in colors


@pytest.fixture
def rotated_classified_cog_with_class_zero(tmp_path):
    """A single-band classified raster with a REAL rotated geotransform
    (Wave: geometric padding fix) - top half is class 0 (Water, a real
    LEGEND-DEFINED class - the exact ambiguity this wave fixes), bottom
    half is class 1 (Forest), NO `nodata` tag, routed through the ACTUAL
    ingestion pipeline (reproject_to_4326 -> convert_to_cog). The rotation
    forces genuine corner fill on reproject_to_4326's north-up output,
    which gets a real internal mask - independent of pixel value, so
    Water=0 is never confused with that fill."""
    h = w = 512
    arr = np.zeros((h, w), dtype="uint16")
    arr[: h // 2, :] = 0  # Water - legend-defined real class 0
    arr[h // 2 :, :] = 1  # Forest
    angle = np.radians(20)
    transform = Affine(
        10 * np.cos(angle), -10 * np.sin(angle), 640000,
        10 * np.sin(angle), 10 * np.cos(angle), 1445000,
    )
    src = tmp_path / "cls_rotated_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:32643", transform=transform, nodata=None,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(arr, 1)

    reproj = tmp_path / "cls_reproj.tif"
    reproject_to_4326(str(src), str(reproj))
    dst = tmp_path / "cls_rotated_cog.tif"
    convert_to_cog(str(reproj), str(dst))
    with rasterio.open(dst) as d:
        return str(dst), d.bounds


def test_classified_padding_renders_transparent_not_fake_class_color(
    rotated_classified_cog_with_class_zero,
):
    """The COG's real internal mask (written by reproject_to_4326 from this
    rotated source's genuine corner fill) must render fully transparent,
    not an opaque fake class color, for a tile landing entirely in that
    fill - a corner of the axis-aligned bounding box, outside the rotated
    real footprint."""
    path, bounds = rotated_classified_cog_with_class_zero
    legend = {"0": {"label": "Water", "color": "#abcdef"}, "1": {"label": "Forest", "color": "#123456"}}
    px = bounds.left + (bounds.right - bounds.left) * 0.02
    py = bounds.top - (bounds.top - bounds.bottom) * 0.02
    t = _TMS.tile(px, py, 15)
    png = render_tile(path, t.z, t.x, t.y, legend=legend)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert img.shape[-1] == 4
    assert (img[:, :, 3] == 0).all(), "genuine warp-fill corner must render fully transparent"


def test_classified_real_data_still_renders_with_legend_color(rotated_classified_cog_with_class_zero):
    """The real classified data (including the legend-defined class 0,
    Water) must still render normally with its legend color - only the
    genuine corner fill should be affected, never a real class value."""
    path, bounds = rotated_classified_cog_with_class_zero
    legend = {"0": {"label": "Water", "color": "#abcdef"}, "1": {"label": "Forest", "color": "#123456"}}
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(cx, cy, 15)
    png = render_tile(path, t.z, t.x, t.y, legend=legend)

    import io

    from PIL import Image

    colors = {tuple(px) for px in np.array(Image.open(io.BytesIO(png))).reshape(-1, 4)}
    assert (0x12, 0x34, 0x56, 235) in colors, "real class 1 must still render with its legend color"


def test_explicit_bands_forces_raw_mode_even_when_a_legend_exists(classified_cog_with_legend):
    """A classified layer's user can still ask to see its raw band(s) -
    presence of an explicit `bands` param must bypass classification, even for
    a 1-band COG that has a legend."""
    z, x, y = _tile_covering_center(classified_cog_with_legend)
    legend = {"1": {"label": "Forest", "color": "#123456"}}
    png_classified = render_tile(classified_cog_with_legend, z, x, y, legend=legend)
    png_raw = render_tile(classified_cog_with_legend, z, x, y, legend=legend, bands=(1,))
    assert png_classified != png_raw

    import io

    from PIL import Image

    colors = {tuple(px) for px in np.array(Image.open(io.BytesIO(png_raw))).reshape(-1, 4)}
    assert (0x12, 0x34, 0x56, 235) not in colors, "raw mode must not use the classified legend color"
