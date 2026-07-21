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

from app.core.errors import ValidationError
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
def half_padded_cog(tmp_path):
    """A genuine multi-band scene where the LEFT HALF is real data and the
    RIGHT HALF is warp-fill padding (exactly 0 in every band) - simulating a
    rotated/irregular real scene reprojected onto an axis-aligned grid, from a
    source raster that had NO `nodata` value set (nodata=None below - matches
    a real ingested "Satellite / Raw Imagery" layer confirmed to have nodata
    None at every pipeline stage). Big enough (2048px) that z=14 covers it
    with multiple distinct tiles, so a "real data" tile and a "padding" tile
    can be requested independently."""
    h = w = 2048
    rng = np.random.default_rng(11)
    bands = np.zeros((3, h, w), dtype="uint16")
    real_data = (np.linspace(500, 3500, w // 2) + rng.normal(0, 100, (h, w // 2))).clip(0, 4000)
    for b in range(3):
        bands[b, :, : w // 2] = real_data
    src = tmp_path / "half_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=3, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(76.20, 13.10, 0.00002, 0.00002), nodata=None,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(bands)
    dst = tmp_path / "half_cog.tif"
    convert_to_cog(str(src), str(dst))
    with rasterio.open(dst) as d:
        return str(dst), d.bounds


def test_all_padding_tile_renders_fully_transparent_not_solid_black(half_padded_cog):
    """Regression test: without a `nodata` value, a tile that lands entirely
    in warp-fill padding (0 in every band) used to get percentile-stretched
    from an all-zero sample - a degenerate stretch that painted solid OPAQUE
    black, indistinguishable on the map from "no tile at all". It must render
    fully transparent instead, so the basemap shows through."""
    path, bounds = half_padded_cog
    # right quarter of the bbox is deep in the zero-padding half
    px = bounds.left + (bounds.right - bounds.left) * 0.85
    py = (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(px, py, 16)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert img.shape[-1] == 4
    assert (img[:, :, 3] == 0).all(), "padding tile must be fully transparent, not opaque black"


def test_real_data_tile_is_not_corrupted_by_neighboring_padding(half_padded_cog):
    """The real-data half must render with genuine visible structure and
    correct exposure, unskewed by the padding half's zeros dragging down the
    percentile stretch."""
    path, bounds = half_padded_cog
    px = bounds.left + (bounds.right - bounds.left) * 0.15
    py = (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(px, py, 16)
    png = render_tile(path, t.z, t.x, t.y)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    visible = img[img[:, :, 3] > 0]
    assert visible.size > 0, "real-data tile must have visible (non-transparent) pixels"
    assert visible[:, :3].std() > 5, "visible pixels must show real contrast, not a flat fill"


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
def classified_cog_no_nodata_with_padding(tmp_path):
    """A single-band classified raster where the LEFT HALF is real classified
    data (values 1-2) and the RIGHT HALF is warp-fill padding (0), with NO
    `nodata` value set - matches a real ingested classified layer confirmed
    to have nodata=None, whose padding rendered as an opaque fake class color
    (DEFAULT_PALETTE[0]) instead of transparent."""
    h = w = 2048
    arr = np.zeros((h, w), dtype="uint16")
    arr[:, : w // 4] = 1
    arr[:, w // 4 : w // 2] = 2
    src = tmp_path / "cls_padded_src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(76.20, 13.10, 0.00002, 0.00002), nodata=None,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(arr, 1)
    dst = tmp_path / "cls_padded_cog.tif"
    convert_to_cog(str(src), str(dst))
    with rasterio.open(dst) as d:
        return str(dst), d.bounds


def test_classified_padding_renders_transparent_not_fake_class_color(
    classified_cog_no_nodata_with_padding,
):
    """Regression test: classified rendering never got the same warp-fill
    treatment as the multi-band raw-imagery path - a raster with no real
    nodata tag rendered its padding as an opaque DEFAULT_PALETTE[0] color
    (indistinguishable from a real class) instead of transparent."""
    path, bounds = classified_cog_no_nodata_with_padding
    legend = {"1": {"label": "Forest", "color": "#123456"}, "2": {"label": "Water", "color": "#abcdef"}}
    # deep in the right (padding) half
    px = bounds.left + (bounds.right - bounds.left) * 0.85
    py = (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(px, py, 16)
    png = render_tile(path, t.z, t.x, t.y, legend=legend)

    import io

    from PIL import Image

    img = np.array(Image.open(io.BytesIO(png)))
    assert img.shape[-1] == 4
    assert (img[:, :, 3] == 0).all(), "padding must render fully transparent, not a fake class color"


def test_classified_real_data_still_renders_with_legend_color(classified_cog_no_nodata_with_padding):
    """The real classified data next to that same padding must still render
    normally with its legend color - only the padding should be affected."""
    path, bounds = classified_cog_no_nodata_with_padding
    legend = {"1": {"label": "Forest", "color": "#123456"}, "2": {"label": "Water", "color": "#abcdef"}}
    # deep in the left (real-data) half
    px = bounds.left + (bounds.right - bounds.left) * 0.05
    py = (bounds.bottom + bounds.top) / 2
    t = _TMS.tile(px, py, 16)
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
