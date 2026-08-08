"""Pure-Python tests for `_current_veg_index_years()` (gee_analysis_service.py,
Wave: enriched vegetation-index results, bug fix).

The bug this guards: the module used to freeze `_VEG_INDEX_YEARS` as a
module-level constant (`range(2017, datetime.utcnow().year + 1)`) computed
once at import time. Two problems with that:

  1. From Jan 1 until the pre-monsoon season window (Feb-May) actually
     closes, the current year was included with an empty/partial
     Sentinel-2 ImageCollection for that window -
     `_s2_reflectance_composite`'s `.median()` over zero images returns a
     bandless `ee.Image`, and the `_INDEX_FORMULAS` band math
     (`.select("B8")`/`.normalizedDifference(...)`) errors on that.
     Because `_annual_index_series` batches every year into ONE
     `ee.Dictionary(...).getInfo()` call, one bad year took down the whole
     multi-year series for all 5 indices at once.
  2. A module-level constant, in a long-running worker process, freezes
     "the current year" at whatever it was when the container started and
     never picks up a new year becoming eligible without a restart.

`_current_veg_index_years(today)` fixes both: it is a function (evaluated
fresh on every call, not frozen at import), and it excludes the current
year until `today >= <that year's Feb-May window closing (May 31)>`.

No `ee` import anywhere in this file - this is pure Python, boundary-date
logic only."""
from __future__ import annotations

from datetime import date

from app.services.gee_analysis_service import (
    _VEG_INDEX_FIRST_YEAR,
    _VEG_SEASON_END_MD,
    _current_veg_index_years,
)


def test_season_end_constant_is_may_31_as_assumed_by_this_test_file():
    # Sanity check the module constant this whole file's boundary dates are
    # keyed to - if that ever changes, these tests should visibly fail
    # rather than silently test the wrong boundary.
    assert _VEG_SEASON_END_MD == "05-31"


def test_jan_1_excludes_the_current_year_entirely():
    years = _current_veg_index_years(date(2026, 1, 1))
    assert 2026 not in years
    assert max(years) == 2025


def test_may_30_still_excludes_the_current_year_one_day_before_season_close():
    years = _current_veg_index_years(date(2026, 5, 30))
    assert 2026 not in years
    assert max(years) == 2025


def test_may_31_includes_the_current_year_the_day_the_season_window_closes():
    years = _current_veg_index_years(date(2026, 5, 31))
    assert 2026 in years
    assert max(years) == 2026


def test_june_1_includes_the_current_year_well_after_season_close():
    years = _current_veg_index_years(date(2026, 6, 1))
    assert 2026 in years
    assert max(years) == 2026


def test_december_31_includes_the_current_year():
    years = _current_veg_index_years(date(2026, 12, 31))
    assert 2026 in years
    assert max(years) == 2026


def test_lower_bound_is_always_the_2017_sentinel_2_floor():
    for today in (date(2018, 1, 1), date(2026, 5, 31), date(2030, 12, 31)):
        assert min(_current_veg_index_years(today)) == _VEG_INDEX_FIRST_YEAR


def test_returns_a_contiguous_range_not_a_list_with_gaps():
    years = _current_veg_index_years(date(2026, 8, 8))
    assert list(years) == list(range(_VEG_INDEX_FIRST_YEAR, 2027))


def test_default_today_uses_the_real_current_date_when_not_injected():
    # No `today` argument passed at all - exercises the `today = date.today()`
    # fallback branch, not just the injectable path every other test here
    # uses. Assert against a fresh independently-computed expectation rather
    # than calling the function twice (that would be a tautology).
    real_today = date.today()
    expected_last_year = (
        real_today.year if (real_today.month, real_today.day) >= (5, 31) else real_today.year - 1
    )
    years = _current_veg_index_years()
    assert max(years) == expected_last_year
