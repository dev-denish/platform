"""Per-analysis configuration domain data (Wave: analysis config and
methodology): year/season constants and eligibility logic shared by the
catalog (what to offer/validate) and the compute path
(app/services/gee_analysis_service.py, what to actually run).

Pure Python, no `ee`/DB/redis import here - same "small, independently
unit-testable seam" convention as app/services/gee_tile_cache.py.
"""
from __future__ import annotations

from datetime import date

# ponytail: fixed year lists rather than discovering each collection's actual
# latest year server-side (an extra getInfo() per request for a number that
# only changes once a year). Bump these annually, or add real "latest
# available year" discovery if that becomes painful.
_ESRI_LULC_YEARS = range(2017, 2024)
_MODIS_YEARS = range(2001, 2024)

_VEG_SEASON_START_MD = "02-01"  # pre-monsoon window - same convention as
_VEG_SEASON_END_MD = "05-31"  # scripts/gee_phase1_agb_proxy.py's usage example
_VEG_CLOUD_BAND = "cs_cdf"
_VEG_CLOUD_THRESHOLD = 0.60

_VEG_INDEX_FIRST_YEAR = 2017  # Sentinel-2 available since 2017


def current_veg_index_years(today: date | None = None) -> range:
    """Which calendar years get a composite this call, evaluated FRESH every
    time (a function, not a module-level constant frozen at import time) -
    this service runs inside a long-lived worker process, so a constant
    computed once at container start would freeze the "current year" at
    whatever it was on startup and never pick up a new year becoming
    eligible without a restart.

    Excludes the current year until ITS OWN pre-monsoon window
    (_VEG_SEASON_START_MD..._VEG_SEASON_END_MD, Feb-May) has fully closed:
    from Jan 1 up to May 31 the current year's Feb-May ImageCollection is
    either empty or only partially populated, and _s2_reflectance_composite's
    `.median()` over zero images returns a bandless ee.Image - the
    `_INDEX_FORMULAS` band math (`.select("B8")`/`.normalizedDifference(...)`)
    then errors on that bandless image. Because `_annual_index_series`
    batches every requested year into ONE `ee.Dictionary(...).getInfo()`
    call, a single bad (not-yet-closed) year would fail the ENTIRE multi-year
    series for all 5 indices at once, not just that year - so this excludes
    it before it ever reaches the compute graph rather than trying to handle
    a bandless image gracefully at reduce time.

    `today` is an injectable parameter (defaults to `date.today()`) purely so
    this is unit-testable against fixed boundary dates (Jan 1, May 31, Jun 1)
    without patching the system clock - see
    tests/unit/test_veg_index_years.py."""
    if today is None:
        today = date.today()
    season_end_md = tuple(int(p) for p in _VEG_SEASON_END_MD.split("-"))  # (month, day)
    last_eligible_year = today.year
    if (today.month, today.day) < season_end_md:
        last_eligible_year -= 1
    return range(_VEG_INDEX_FIRST_YEAR, last_eligible_year + 1)
