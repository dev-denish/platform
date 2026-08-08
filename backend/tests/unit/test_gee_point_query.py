"""Pure-logic tests for the Identify-tool GEE point-query helpers
(app/services/gee_analysis_service.py's _legend_lookup/_hansen_detail) - no
DB, no GEE network calls. These are the two functions _compute_point's
per-analysis branches funnel their raw GEE sample through before returning
an AnalysisPointValue, split out specifically so this translation logic is
testable without a live or faked `ee` session (see their own docstrings)."""
from __future__ import annotations

import pytest

from app.domain.gee_class_legends import (
    DYNAMIC_WORLD_LEGEND,
    ESA_WORLDCOVER_LEGEND,
    ESRI_LULC_LEGEND,
    MODIS_IGBP_LEGEND,
)
from app.services.gee_analysis_service import _hansen_detail, _legend_lookup

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
