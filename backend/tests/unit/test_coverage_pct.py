"""Pure-logic tests for `_coverage_pct` (Wave: partial coverage) - no DB, no
GEE. The real "is this actually computed, not estimated" guarantee lives in
each analysis function's own reduceRegion/boundary.area() call (verified
live in test_gee_clip_and_browse_live_gee.py); this file only tests the
pure arithmetic those real numbers feed into."""
from __future__ import annotations

from app.services.gee_analysis_service import _coverage_pct


def test_normal_partial_coverage():
    assert _coverage_pct(covered_area_m2=680.0, boundary_area_m2=1000.0) == 68.0


def test_full_coverage_rounds_to_one_hundred():
    assert _coverage_pct(covered_area_m2=1000.0, boundary_area_m2=1000.0) == 100.0


def test_zero_coverage():
    assert _coverage_pct(covered_area_m2=0.0, boundary_area_m2=1000.0) == 0.0


def test_over_coverage_from_edge_pixel_overcounting_is_clamped_to_one_hundred():
    # A boundary polygon rasterized at 10-500m resolution can produce a
    # covered-pixel sum fractionally ABOVE the true polygon area at the
    # edge - this must read as ~100% coverage, never e.g. 104.2%.
    assert _coverage_pct(covered_area_m2=1042.0, boundary_area_m2=1000.0) == 100.0


def test_zero_area_boundary_returns_zero_not_a_division_error():
    assert _coverage_pct(covered_area_m2=0.0, boundary_area_m2=0.0) == 0.0


def test_result_is_rounded_to_one_decimal():
    assert _coverage_pct(covered_area_m2=683.33, boundary_area_m2=1000.0) == 68.3
