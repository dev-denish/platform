"""Pure-logic tests for the Identify-tool GEE point-query helpers
(app/services/gee_analysis_service.py's _legend_lookup/_hansen_detail) - no
DB, no GEE network calls. These are the two functions _compute_point's
per-analysis branches funnel their raw GEE sample through before returning
an AnalysisPointValue, split out specifically so this translation logic is
testable without a live or faked `ee` session (see their own docstrings).

The second half of this file (_compute_point dispatch tests) goes one level
deeper than _legend_lookup/_hansen_detail alone: real `ee.Image`/
`ee.ImageCollection`/`ee.Reducer` construction requires a live, authenticated
`ee.Initialize()` session (verified directly - bare `ee.Reducer.first()`
raises "Earth Engine client library not initialized" with no session at
all), so those tests fake only the INNERMOST leaf each analysis touches (the
per-analysis image builder, or `ee.Image` itself for Hansen, which has no
separate builder) via `_FakeSample`, and run the REAL `_compute_point`
dispatcher - `_classified_point`/`_hansen_point`/`_legend_lookup` - on top of
that fake. This is what actually proves the dispatcher wires "dynamic_world"
to DYNAMIC_WORLD_LEGEND (not some other legend, or the wrong band name) -
service-layer tests that mock `_compute_point` itself wholesale (see
tests/integration/test_gee_analysis_service.py) only prove the SERVICE
passes through whatever `_compute_point` returns, never that `_compute_point`
computed the right thing."""
from __future__ import annotations

import pytest

from app.domain.gee_class_legends import (
    DYNAMIC_WORLD_LEGEND,
    ESA_WORLDCOVER_LEGEND,
    ESRI_LULC_LEGEND,
    MODIS_IGBP_LEGEND,
)
from app.services import gee_analysis_service as svc_module
from app.services.gee_analysis_service import _compute_point, _hansen_detail, _legend_lookup

# --------------------------------------------------------------- _legend_lookup


@pytest.mark.parametrize(
    ("legend", "code", "expected_name"),
    [
        (DYNAMIC_WORLD_LEGEND, 4, "crops"),
        (DYNAMIC_WORLD_LEGEND, 6, "built"),
        (ESA_WORLDCOVER_LEGEND, 40, "Cropland"),
        (ESA_WORLDCOVER_LEGEND, 50, "Built-up"),
        (ESRI_LULC_LEGEND, 5, "Crops"),
        (ESRI_LULC_LEGEND, 7, "Built area"),
        (MODIS_IGBP_LEGEND, 12, "Croplands"),
        (MODIS_IGBP_LEGEND, 13, "Urban and Built-up"),
    ],
)
def test_legend_lookup_returns_the_real_class_name_not_the_raw_code(legend, code, expected_name):
    result_code, name, color = _legend_lookup(code, legend)
    assert result_code == code
    assert name == expected_name
    assert color == legend[code][1]


def test_legend_lookup_none_code_is_no_coverage_at_that_pixel():
    # img.select(band).reduceRegion(...).get(band) comes back None when the
    # clicked point falls on a masked/nodata pixel - must not raise or
    # fabricate a class.
    assert _legend_lookup(None, DYNAMIC_WORLD_LEGEND) == (None, None, None)


def test_legend_lookup_unrecognized_code_still_labels_it_rather_than_dropping_the_value():
    code, name, color = _legend_lookup(99, ESA_WORLDCOVER_LEGEND)  # not a real WorldCover code
    assert code == 99
    assert name == "Unrecognized class 99"
    assert color == "#999999"


# --------------------------------------------------------------- _hansen_detail


def test_hansen_detail_no_coverage_returns_none():
    assert _hansen_detail(None, False, None, False, canopy_cover_pct=30.0) == (None, None)


def test_hansen_detail_forest_no_loss_no_gain():
    value, detail = _hansen_detail(85.0, False, None, False, canopy_cover_pct=30.0)
    assert value == 85.0
    assert detail == "forest"


def test_hansen_detail_below_threshold_is_non_forest():
    value, detail = _hansen_detail(10.0, False, None, False, canopy_cover_pct=30.0)
    assert value == 10.0
    assert detail == "non-forest (below the 30% threshold)"


def test_hansen_detail_loss_year_is_offset_from_2000():
    value, detail = _hansen_detail(85.0, True, 19, False, canopy_cover_pct=30.0)
    assert value == 85.0
    assert detail == "forest, loss detected in 2019"


def test_hansen_detail_gain_flag_is_reported():
    value, detail = _hansen_detail(85.0, False, None, True, canopy_cover_pct=30.0)
    assert detail == "forest, gain detected (2000-2012)"


def test_hansen_detail_loss_and_gain_both_reported():
    value, detail = _hansen_detail(85.0, True, 5, True, canopy_cover_pct=30.0)
    assert detail == "forest, loss detected in 2005, gain detected (2000-2012)"


# --------------------------------------------------------------- _compute_point dispatch


class _FakeSample:
    """Stands in for the real ee.Image/ee.ComputedObject chain
    _classified_point/_hansen_point build - .select/.reduceRegion/.get all
    return self (so any chain length works), .getInfo() returns whatever
    this was constructed with: a raw int code for a classified point, or a
    dict of band values for Hansen's multi-band sample."""

    def __init__(self, info):
        self._info = info

    def select(self, *a, **k):
        return self

    def reduceRegion(self, *a, **k):
        return self

    def get(self, *a, **k):
        return self

    def getInfo(self):
        return self._info


@pytest.fixture
def fake_ee_leaves(monkeypatch):
    """Patches only what _compute_point touches before reaching an
    analysis-specific image builder: init_ee, ee.Geometry/.Point (never
    inspected by _FakeSample, just constructed), and ee.Reducer.first
    (evaluated eagerly as a kwarg VALUE at every _classified_point/
    _hansen_point call site, so it must not raise even though its result is
    discarded by _FakeSample.reduceRegion)."""
    monkeypatch.setattr(svc_module, "init_ee", lambda: None)
    monkeypatch.setattr(svc_module.ee, "Geometry", lambda geojson: geojson)
    monkeypatch.setattr(svc_module.ee.Geometry, "Point", lambda coords: coords, raising=False)
    monkeypatch.setattr(svc_module.ee.Reducer, "first", lambda: None)


def test_compute_point_dynamic_world_dispatches_to_the_real_dynamic_world_legend(
    fake_ee_leaves, monkeypatch
):
    # Code 4 in DYNAMIC_WORLD_LEGEND is "crops" - a DIFFERENT code (e.g. 6,
    # "built") would catch a dispatch bug (wrong legend, wrong band) that a
    # service-layer test mocking _compute_point wholesale never could.
    monkeypatch.setattr(svc_module, "_dynamic_world_image", lambda boundary: _FakeSample(4))

    result = _compute_point("dynamic_world", {"type": "Polygon", "coordinates": []}, 30.0, 77.5, 12.9)

    assert result == {"value": 4, "class_name": "crops", "class_color": "#E49635"}
    assert DYNAMIC_WORLD_LEGEND[4] == ("crops", "#E49635")  # the real legend, not a hardcoded guess


def test_compute_point_esa_worldcover_dispatches_to_the_real_worldcover_legend(
    fake_ee_leaves, monkeypatch
):
    monkeypatch.setattr(svc_module, "_esa_worldcover_image", lambda: _FakeSample(40))

    result = _compute_point("esa_worldcover", {"type": "Polygon", "coordinates": []}, 30.0, 77.5, 12.9)

    assert result == {"value": 40, "class_name": "Cropland", "class_color": "#F096FF"}
    assert ESA_WORLDCOVER_LEGEND[40] == ("Cropland", "#F096FF")


def test_compute_point_hansen_returns_real_tree_cover_pct_and_detail_sentence(
    fake_ee_leaves, monkeypatch
):
    # gfc.select([...]).reduceRegion(...).getInfo() - Hansen has no separate
    # _hansen_gfc_image() builder (unlike the land-cover analyses), so the
    # innermost fake here is ee.Image itself.
    sample = {"treecover2000": 82.5, "loss": 1, "lossyear": 19, "gain": 0}
    monkeypatch.setattr(svc_module.ee, "Image", lambda name: _FakeSample(sample))

    result = _compute_point("hansen_gfc", {"type": "Polygon", "coordinates": []}, 50.0, 77.5, 12.9)

    assert result["value"] == 82.5
    assert result["unit"] == "% tree cover (2000)"
    assert result["detail"] == "forest, loss detected in 2019"


def test_compute_point_hansen_below_threshold_and_no_loss_or_gain(fake_ee_leaves, monkeypatch):
    sample = {"treecover2000": 12.0, "loss": 0, "lossyear": None, "gain": 0}
    monkeypatch.setattr(svc_module.ee, "Image", lambda name: _FakeSample(sample))

    result = _compute_point("hansen_gfc", {"type": "Polygon", "coordinates": []}, 30.0, 77.5, 12.9)

    assert result["value"] == 12.0
    assert result["detail"] == "non-forest (below the 30% threshold)"
