"""Live-GEE regression test for AOI-clipped rendering (Wave: AOI clip).

The bug: 5 of the 10 "available" analyses (`_hansen_forest_change`,
`_dynamic_world`, `_esa_worldcover`, `_esri_lulc`, `_modis_lulc`) scoped
their `reduceRegion` stats to the project boundary but never clipped the
VISUALIZED image passed to `.getMapId()` - the returned map tile rendered
the whole global dataset, not just the AOI. Only the vegetation-index five
(`_annual_index_series`, via `_s2_reflectance_composite`'s own `.clip()`)
were ever actually clipped.

This can only be proven live: `_compute()` returns a `tile_url_template`
string, not an `ee.Image`, so the only way to verify the real production
code path renders clipped is to fetch a REAL tile from a REAL point clearly
outside the AOI and confirm it comes back fully transparent, versus a tile
covering the AOI itself, which must not be. Matches this module's sibling
`test_veg_index_live_gee.py`'s own reasoning for why a live round trip is
required: no local/offline fixture can see what GEE's tile server actually
renders.

Run locally with, e.g.:
    DMRV_TEST_GEE=1 pytest -m gee_live
Same live, credentialed GEE session as test_veg_index_live_gee.py - real
network calls, real (small) GEE quota cost, does not run in the fast unit
suite or default CI.
"""
from __future__ import annotations

import io
import math
import os

import pytest

pytestmark = pytest.mark.gee_live

if not os.getenv("DMRV_TEST_GEE"):
    pytest.skip("DMRV_TEST_GEE not set; skipping live-GEE tests", allow_module_level=True)

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from app.services.gee_analysis_service import _compute, _compute_point, init_ee  # noqa: E402

# Same tiny (~1.1km x 1.1km) real-world square over Bengaluru, Karnataka used
# by test_veg_index_live_gee.py - known real coverage for all 5 datasets
# under test here (Hansen/Dynamic World/WorldCover/Esri/MODIS all have
# global land coverage, unlike Sentinel-2's cloud-dependent per-scene gaps).
_TINY_BOUNDARY_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[[77.0, 12.0], [77.01, 12.0], [77.01, 12.01], [77.0, 12.01], [77.0, 12.0]]],
}
_BOUNDARY_CENTROID = (77.005, 12.005)  # lon, lat
# ~6.5km from the boundary's centroid - many tiles away from the ~1.1km AOI
# at every zoom used below, so a tile centered here has zero overlap with
# the clip geometry regardless of projection/tile-grid alignment quirks.
_FAR_OUTSIDE_POINT = (77.08, 12.08)  # lon, lat
_ZOOM = 15  # ~1.2km/tile at this latitude - fine enough to tell "covers the
# 1.1km boundary" from "6.5km away" apart, coarse enough that both tiles are
# cheap, single HTTP fetches.


@pytest.fixture(scope="module", autouse=True)
def _live_ee_session():
    init_ee()


def _lonlat_to_tile_xy(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Standard Web Mercator (Leaflet/GEE tile convention) lon/lat -> tile
    x/y at a given zoom - the same {z}/{x}/{y} scheme `tile_fetcher.
    url_format` uses, since the frontend hands this string straight to a
    Leaflet <TileLayer>."""
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _fetch_tile_alpha_stats(tile_url_template: str, lon: float, lat: float) -> tuple[int, int]:
    """Fetches the real tile covering (lon, lat) and returns
    (opaque_pixel_count, total_pixel_count) from its alpha channel - the
    direct, real-tile way to tell "GEE rendered something here" from
    "clipped/masked to fully transparent" without duplicating any clip logic
    in the test itself (this fetches the SAME url _compute() actually
    returned, nothing hand-rolled)."""
    x, y = _lonlat_to_tile_xy(lon, lat, _ZOOM)
    url = tile_url_template.format(z=_ZOOM, x=x, y=y)
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    alpha = img.getchannel("A")
    pixels = list(alpha.getdata())
    opaque = sum(1 for p in pixels if p > 0)
    return opaque, len(pixels)


@pytest.mark.parametrize(
    "analysis_id",
    ["hansen_gfc", "dynamic_world", "esa_worldcover", "io_lulc", "modis_lulc"],
)
def test_previously_unclipped_layers_render_transparent_far_outside_the_aoi(analysis_id):
    """The actual regression, per analysis: before the Wave: AOI clip fix,
    EVERY one of these 5 rendered real (non-transparent) pixels at a point
    6.5km outside a 1.1km AOI, because the visualized image was never
    clipped - only reduceRegion's stats geometry was boundary-scoped. After
    the fix, a tile far outside the AOI must be fully (or almost fully)
    transparent."""
    canopy_cover_pct = 30.0  # only consumed by hansen_gfc; harmless default for the other 4
    _, _, tile_url_template = _compute(analysis_id, _TINY_BOUNDARY_GEOJSON, canopy_cover_pct)
    assert tile_url_template

    outside_opaque, outside_total = _fetch_tile_alpha_stats(tile_url_template, *_FAR_OUTSIDE_POINT)
    assert outside_opaque == 0, (
        f"{analysis_id}: {outside_opaque}/{outside_total} pixels were opaque in a tile "
        "6.5km outside the 1.1km AOI - the map tile is not clipped to the boundary"
    )


@pytest.mark.parametrize(
    "analysis_id",
    ["hansen_gfc", "dynamic_world", "esa_worldcover", "io_lulc", "modis_lulc"],
)
def test_previously_unclipped_layers_still_render_real_pixels_inside_the_aoi(analysis_id):
    """Guards against a too-aggressive clip masking out the AOI itself -
    the tile covering the boundary's own centroid must still have real
    (opaque) pixels after clipping, not an empty grid."""
    canopy_cover_pct = 30.0
    _, _, tile_url_template = _compute(analysis_id, _TINY_BOUNDARY_GEOJSON, canopy_cover_pct)

    inside_opaque, inside_total = _fetch_tile_alpha_stats(tile_url_template, *_BOUNDARY_CENTROID)
    assert inside_opaque > 0, (
        f"{analysis_id}: 0/{inside_total} pixels were opaque in the tile covering the AOI's own "
        "centroid - clipping masked out the AOI itself, not just the outside area"
    )


# ------------------------------------------------------- raw-imagery browsing (Wave)

# 2023 - a real past year with known good Sentinel-1/Sentinel-2/Landsat
# coverage over Bengaluru, used instead of "latest" (None) so these tests
# are deterministic and don't depend on what's been acquired in the last 90
# days relative to whenever the suite happens to run.
_BROWSE_TEST_YEAR = 2023


@pytest.mark.parametrize("analysis_id", ["s2_browse", "s1_browse", "landsat_browse"])
def test_browse_layers_render_transparent_far_outside_the_aoi(analysis_id):
    """Same clip regression as the previously-unclipped 5 above, applied to
    the 3 new raw-imagery browse layers from day one - they must never ship
    unclipped in the first place."""
    canopy_cover_pct = 30.0  # unused by all 3, harmless default
    request_params = {"year": _BROWSE_TEST_YEAR}
    _, _, tile_url_template = _compute(
        analysis_id, _TINY_BOUNDARY_GEOJSON, canopy_cover_pct, request_params
    )
    assert tile_url_template

    outside_opaque, outside_total = _fetch_tile_alpha_stats(tile_url_template, *_FAR_OUTSIDE_POINT)
    assert outside_opaque == 0, (
        f"{analysis_id}: {outside_opaque}/{outside_total} pixels were opaque in a tile "
        "6.5km outside the 1.1km AOI - the browse map tile is not clipped to the boundary"
    )


@pytest.mark.parametrize("analysis_id", ["s2_browse", "s1_browse", "landsat_browse"])
def test_browse_layers_render_real_pixels_inside_the_aoi_for_the_requested_year(analysis_id):
    canopy_cover_pct = 30.0
    request_params = {"year": _BROWSE_TEST_YEAR}
    stats, _, tile_url_template = _compute(
        analysis_id, _TINY_BOUNDARY_GEOJSON, canopy_cover_pct, request_params
    )

    inside_opaque, inside_total = _fetch_tile_alpha_stats(tile_url_template, *_BOUNDARY_CENTROID)
    assert inside_opaque > 0, (
        f"{analysis_id}: 0/{inside_total} pixels were opaque in the tile covering the AOI's own "
        f"centroid for {_BROWSE_TEST_YEAR} - no real scene was found/rendered"
    )
    # Proves request_params actually reached the real GEE query, not just
    # that SOME scene came back - the returned scene must fall within the
    # requested calendar year, not silently default to "latest".
    assert stats["scene_date"].startswith(str(_BROWSE_TEST_YEAR)), stats["scene_date"]


def test_compute_point_for_browse_layers_returns_a_detail_string_for_the_requested_year():
    """Identify's per-pixel counterpart for the 3 browse ids: no single
    scalar value (RGB/dual-pol), so the response is a formatted `detail`
    string, not `value` - and it must sample the SAME year the caller's
    tile was computed with (request_params), not silently "latest"."""
    boundary_center_point = _BOUNDARY_CENTROID
    request_params = {"year": _BROWSE_TEST_YEAR}
    for analysis_id in ("s2_browse", "s1_browse", "landsat_browse"):
        result = _compute_point(
            analysis_id, _TINY_BOUNDARY_GEOJSON, 30.0,
            boundary_center_point[0], boundary_center_point[1], request_params,
        )
        assert result.get("value") is None
        assert result.get("detail"), f"{analysis_id}: expected a real detail string, got {result!r}"
