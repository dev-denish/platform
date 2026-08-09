"""Live-GEE regression test for the reduceRegion key-naming bug that made
every vegetation-index result (NDVI/EVI/SAVI/MNDWI/NBR) silently look like
"no cloud-free coverage inside the boundary" for every project, for every
year, since commit 88c0900.

The bug: `_annual_index_series` read `values.get("index_mean")`,
`("index_stdDev")`, `("index_min")`, `("index_max")`, `("index_histogram")`,
`("index_raw_count")` - i.e. it assumed GEE prefixes every combined-reducer
output key with the input band name ("index"). That assumption is false for
this specific reducer tree: GEE only adds a "<band>_" prefix to disambiguate
an output name that would otherwise collide across more than one band, and
every sub-reducer in `_index_stats_reducer_with_raw_count()` already has a
distinct output name (mean/stdDev/min/max/histogram/count), so nothing
collides and GEE returns them bare. Every one of those `.get("index_...")`
calls was therefore always `None`, regardless of real cloud cover, quota, or
boundary - real GEE data was computed correctly and then silently discarded
before it ever reached `series`/`distribution`.

This is exactly the class of bug `test_index_histogram_shaping.py`'s
`local_ee_session` fixture CANNOT catch: that fixture builds the reducer's
expression graph against a local, offline algorithm list and asserts on its
*shape* (which sub-reducer combines with which) - it never issues a real
`reduceRegion().getInfo()` round trip, so it never sees what key names GEE
actually hands back. Only a live session does. Hence this file.

Deliberately NOT reusing `_s2_reflectance_composite`'s full pre-monsoon
per-year composite - a single small rectangle with known Sentinel-2 coverage
is enough to prove the key-naming contract; a full multi-year series
(`_annual_index_series` itself) is exercised at the API/E2E layer
(frontend/tests/e2e/analysis-enriched.spec.js) which was ALSO fooled by this
bug (its "averages -?\\d+\\.\\d{2}" regex only proved the summary sentence
was well-formed prose, never that its number was non-null-derived - see that
file's own follow-up notes).

Run locally with, e.g.:
    DMRV_TEST_GEE=1 pytest -m gee_live
Needs the same live, credentialed GEE session
app/services/gee_client.py's init_ee() uses in production/dev (a mounted
`earthengine authenticate` token, see deploy/docker-compose.yml) - real
network calls, real (small) GEE quota cost, so this does NOT run by default
in the fast unit suite or in DMRV_TEST_DATABASE-gated CI.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.gee_live

if not os.getenv("DMRV_TEST_GEE"):
    pytest.skip("DMRV_TEST_GEE not set; skipping live-GEE tests", allow_module_level=True)

import ee  # noqa: E402

from app.services.gee_analysis_service import (  # noqa: E402
    AOI_CRS,
    _annual_index_series,
    _index_stats_reducer_with_raw_count,
    _VEG_INDEX_HISTOGRAM_RANGE,
    init_ee,
)

# A small (~1.1km x 1.1km) real-world square over Bengaluru, Karnataka with
# well-known, reliable Sentinel-2 coverage - big enough to have real pixels,
# small enough that a reduceRegion call here is fast and cheap, unlike
# _annual_index_series' full-boundary, multi-year, 10-image-composite series.
_TINY_BOUNDARY_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[[77.0, 12.0], [77.01, 12.0], [77.01, 12.01], [77.0, 12.01], [77.0, 12.0]]],
}


@pytest.fixture(scope="module", autouse=True)
def _live_ee_session():
    init_ee()


def _real_ndvi_reduce_region(boundary: ee.Geometry, year: int) -> dict:
    """Builds the SAME 2-band ("index", "index_raw") stack + the SAME
    `_index_stats_reducer_with_raw_count()` reducer `_annual_index_series`
    uses for every index - reusing the real reducer function rather than a
    hand-rolled stand-in is the whole point: this must fail if that
    reducer's shape (or GEE's key-naming behavior for it) ever changes."""
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(f"{year}-02-01", f"{year}-05-31")
        .filterBounds(boundary)
    )
    cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    linked = s2.linkCollection(cs_plus, ["cs_cdf"])
    masked = linked.map(lambda img: img.updateMask(img.select("cs_cdf").gte(0.60)))
    refl = masked.median().clip(boundary).divide(10000)

    idx_raw = refl.normalizedDifference(["B8", "B4"]).rename("index")
    range_lo, range_hi = _VEG_INDEX_HISTOGRAM_RANGE
    idx = idx_raw.updateMask(idx_raw.gte(range_lo).And(idx_raw.lte(range_hi)))
    stack = idx.addBands(idx_raw.rename("index_raw"))

    return stack.reduceRegion(
        reducer=_index_stats_reducer_with_raw_count(), geometry=boundary, scale=10,
        crs=AOI_CRS, maxPixels=1e10, bestEffort=True,
    ).getInfo()


def test_index_stats_reducer_with_raw_count_returns_bare_keys_against_real_gee():
    """The actual regression, isolated to the exact reduceRegion() call
    _annual_index_series makes: asserts the REAL key names a live GEE
    backend returns for this reducer tree are bare (mean/stdDev/min/max/
    histogram/count), not band-prefixed (index_mean/index_stdDev/...) - the
    wrong assumption the pre-fix code baked in as `.get("index_mean")` etc.
    A future change to this reducer's shape, or a GEE API change to its
    key-naming convention, fails loudly here instead of silently producing
    None for every stat in production."""
    boundary = ee.Geometry(_TINY_BOUNDARY_GEOJSON)
    values = _real_ndvi_reduce_region(boundary, 2024)

    assert set(values.keys()) == {"mean", "stdDev", "min", "max", "histogram", "count"}, (
        f"got {sorted(values.keys())} - if GEE ever starts band-prefixing this reducer's "
        "output, _annual_index_series's key access must change to match"
    )
    for prefixed in ("index_mean", "index_stdDev", "index_min", "index_max",
                     "index_histogram", "index_raw_count"):
        assert prefixed not in values, (
            f"{prefixed!r} unexpectedly present - the OLD (buggy) code's key access would "
            "have silently worked again for the wrong reason"
        )


def test_index_stats_reducer_with_raw_count_values_are_real_non_null_numbers():
    """Guards the actual symptom, not just the key names: a real boundary
    with real cloud-free Sentinel-2 coverage must produce a real numeric
    mean/count, not None. This is what the bug actually broke end-to-end -
    key names could be "fixed" to some other wrong-but-present name and this
    test would still catch it, whereas the key-name test above could not."""
    boundary = ee.Geometry(_TINY_BOUNDARY_GEOJSON)
    values = _real_ndvi_reduce_region(boundary, 2024)

    assert values["count"] is not None and values["count"] > 0
    assert values["mean"] is not None
    assert -1.0 <= values["mean"] <= 1.0
    assert values["histogram"] is not None
    assert sum(row[1] for row in values["histogram"]) > 0


def test_annual_index_series_end_to_end_returns_real_non_null_series_and_distribution():
    """The full public function, against real GEE, for a real small
    boundary - the end-to-end version of the regression: before the fix,
    THIS returned `series == {year: None for every year}` and a summary of
    "no cloud-free coverage inside the boundary" for every project on
    earth, not because of quota, boundary geometry, or actual cloud cover,
    but because of the key-naming bug isolated in the tests above. A single
    year is enough here (not the full 2017-present range) - this is an
    end-to-end sanity check on the real function's return shape, not a
    re-verification of _current_veg_index_years' own year-range logic
    (see test_veg_index_years.py for that, offline)."""
    boundary = ee.Geometry(_TINY_BOUNDARY_GEOJSON)

    stats, legend, tile_url_template = _annual_index_series(boundary, "ndvi")

    assert legend is None
    assert isinstance(tile_url_template, str) and tile_url_template  # a real getMapId() tile URL
    real_years = {y: v for y, v in stats["series"].items() if v is not None}
    assert real_years, f"every year came back None - series was {stats['series']}"
    assert all(-1.0 <= v <= 1.0 for v in real_years.values())
    assert "no cloud-free coverage inside the boundary" not in stats["summary"]
