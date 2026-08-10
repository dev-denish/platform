"""Per-analysis configuration domain data (Wave: analysis config and
methodology): year/season constants and eligibility logic shared by the
catalog (what to offer/validate) and the compute path
(app/services/gee_analysis_service.py, what to actually run).

Pure Python, no `ee`/DB/redis import here - same "small, independently
unit-testable seam" convention as app/services/gee_tile_cache.py.

`resolve_and_validate()`/`params_key()` are the two functions the rest of
this wave hangs off: the former turns whatever a caller sent (possibly
nothing at all) into a fully-resolved, already-validated params dict or
raises a specific `ValidationError`, hand-written per analysis family - not
a generic constraint solver, matching this codebase's existing convention of
one hand-written branch per analysis rather than a general-purpose engine
for a fixed, small, fully-enumerated set of cases. The latter is the ONE
canonicalization of "what makes two requests the same stored/cached variant"
- shared by the Redis cache key (gee_tile_cache.py) and the analysis_result
DB row's params_key column, so there is exactly one idea of variant identity,
not two independently-maintained ones.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal, TypedDict

from app.core.errors import ValidationError

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


# ------------------------------------------------------- configurable inputs
#
# Wave: analysis config and methodology. Only 7 of the 13 real analysis ids
# have anything configurable at all - the other 6 (hansen_gfc, dynamic_world,
# esa_worldcover, and the 3 raw-imagery browse ids) either already return a
# full time series in one call, use a rolling non-calendar window, or are a
# single fixed snapshot; none of those has a year/season/source knob that
# would change anything real. See this wave's own plan notes for why each of
# the 6 was excluded, not just left out silently.

YearMode = Literal["single", "range"]


class AnalysisConfigSpec(TypedDict, total=False):
    """Declarative capability data for ONE catalog entry - mirrors the
    existing `year_selectable: NotRequired[bool]` precedent in
    analysis_catalog.py, but as its own dict since this shape has real
    structure. Absent key = "this axis isn't configurable for this
    analysis" (checked via `"key" in spec`, not a falsy value), same
    convention as `AnalysisCatalogEntry`'s own `NotRequired` fields."""

    year_mode_default: YearMode
    year_min: int
    # None = ask current_veg_index_years() live at request/serve time (the 5
    # indices, whose ceiling moves every year); a static int for the 2
    # fixed-catalog-year analyses (io_lulc/modis_lulc).
    year_max: int | None
    season_editable: bool
    season_start_default: str  # "MM-DD"
    season_end_default: str
    imagery_sources: list[str]
    cloud_masking_methods: list[str]
    supported_combos: list[tuple[str, str]]


# Human-readable labels for error messages only - the frontend owns its own
# display labels for the option lists themselves (these ids are just wire
# values), but a rejection message read directly by a user should say
# "Sentinel-2", not "sentinel2".
_SOURCE_LABELS = {"sentinel2": "Sentinel-2", "landsat8": "Landsat 8", "landsat9": "Landsat 9"}
_MASKING_LABELS = {
    "cloud_score_plus": "Cloud Score+", "qa_pixel": "QA_PIXEL", "none": "no cloud masking",
}

_INDEX_IDS = frozenset({"ndvi", "evi", "savi", "mndwi", "nbr"})
_ANNUAL_LAND_COVER_IDS = frozenset({"io_lulc", "modis_lulc"})

# The ids whose analysis_result DB row must be scoped by resolved params
# (extends the composite primary key) rather than the single fixed
# `"default"` row every other analysis_id still uses. Deliberately excludes
# the 3 browse ids: their existing "DB row doesn't vary by year" gap
# predates this wave and stays out of scope - see the wave's plan notes.
_STORAGE_SCOPED_IDS = frozenset(_ANNUAL_LAND_COVER_IDS | _INDEX_IDS)

# Reserved sentinel only ever written by migration 0018, backfilled onto the
# 7 storage-scoped ids' pre-existing rows (computed under the old hardcoded
# "always full range" behavior). `resolve_and_validate` below always returns
# a concrete, JSON-shaped dict for these ids - it can never naturally
# produce this literal string - so a fresh default (single-latest-year) run
# gets its own distinct key and can never collide with/overwrite the legacy
# row it's meant to never destroy.
LEGACY_FULL_RANGE_PARAMS_KEY = "legacy_full_range"


def _resolve_year_bounds(
    raw: dict[str, Any], year_min: int, year_max: int, default_mode: YearMode
) -> dict[str, Any]:
    """Shared by io_lulc/modis_lulc/the 5 indices - identical single-vs-range
    shape once each caller has worked out its own `year_max` (a static
    catalog constant for the land-cover pair, a live
    `max(current_veg_index_years())` for the indices)."""
    mode = raw.get("year_mode") or default_mode
    if mode not in ("single", "range"):
        raise ValidationError(f"year_mode must be 'single' or 'range', not {mode!r}.")
    if mode == "single":
        year = raw.get("year")
        year = year_max if year is None else int(year)
        if not (year_min <= year <= year_max):
            raise ValidationError(f"Year must be between {year_min} and {year_max}.")
        return {"year_mode": "single", "year": year}
    year_start = raw.get("year_start")
    year_start = year_min if year_start is None else int(year_start)
    year_end = raw.get("year_end")
    year_end = year_max if year_end is None else int(year_end)
    if not (year_min <= year_start <= year_end <= year_max):
        raise ValidationError(
            f"Year range must be within {year_min}-{year_max}, with a start year no "
            "later than the end year."
        )
    return {"year_mode": "range", "year_start": year_start, "year_end": year_end}


def resolve_and_validate(
    analysis_id: str, spec: AnalysisConfigSpec, raw: dict[str, Any] | None
) -> dict[str, Any]:
    """Turns `raw` (whatever a caller sent - possibly nothing) into a fully-
    resolved params dict, or raises `ValidationError` with a specific,
    user-facing reason. Called once, at request time, before any GEE work -
    same "validate synchronously, fail fast" contract
    gee_analysis_service.py's `_prepare_refresh` already uses elsewhere.

    Every branch below always returns a complete, concrete dict - never a
    partial or empty one - for every id this function is ever called for
    (`_prepare_refresh` only calls this when `"config" in entry`, i.e. never
    for the 6 non-configurable ids). That completeness is what makes
    `params_key()` safe to always JSON-encode the result without a separate
    "is this actually the default" check."""
    raw = raw or {}
    if analysis_id in _ANNUAL_LAND_COVER_IDS:
        year_min, year_max = spec["year_min"], spec["year_max"]
        assert year_max is not None  # noqa: S101 - static for both land-cover ids, never live
        return _resolve_year_bounds(raw, year_min, year_max, spec["year_mode_default"])

    if analysis_id in _INDEX_IDS:
        live_max = max(current_veg_index_years())
        resolved = _resolve_year_bounds(raw, spec["year_min"], live_max, spec["year_mode_default"])

        season_start = raw.get("season_start") or spec["season_start_default"]
        season_end = raw.get("season_end") or spec["season_end_default"]
        if not (_is_valid_month_day(season_start) and _is_valid_month_day(season_end)):
            raise ValidationError("Season start/end must be 'MM-DD' dates, e.g. '02-01'.")
        if season_start >= season_end:
            raise ValidationError("Season start must be before season end.")
        resolved["season_start"] = season_start
        resolved["season_end"] = season_end

        imagery_source = raw.get("imagery_source") or spec["imagery_sources"][0]
        cloud_masking = raw.get("cloud_masking") or spec["cloud_masking_methods"][0]
        if (imagery_source, cloud_masking) not in spec["supported_combos"]:
            supported = spec["supported_combos"][0]
            supported_label = (
                f"{_SOURCE_LABELS.get(supported[0], supported[0])} imagery with "
                f"{_MASKING_LABELS.get(supported[1], supported[1])} cloud masking"
            )
            picked_label = (
                f"{_SOURCE_LABELS.get(imagery_source, imagery_source)} + "
                f"{_MASKING_LABELS.get(cloud_masking, cloud_masking)}"
            )
            raise ValidationError(
                f"This analysis only supports {supported_label} today. {picked_label!r} "
                "isn't implemented yet."
            )
        resolved["imagery_source"] = imagery_source
        resolved["cloud_masking"] = cloud_masking
        return resolved

    # Called for an id with no `config` at all - shouldn't happen given the
    # `"config" in entry` gate at the call site, but returning `{}` (ignored,
    # same as any other unconfigured id) rather than raising keeps this
    # function total instead of partial.
    return {}


def _is_valid_month_day(value: str) -> bool:
    parts = value.split("-")
    if len(parts) != 2:
        return False
    month_s, day_s = parts
    if not (month_s.isdigit() and day_s.isdigit()):
        return False
    month, day = int(month_s), int(day_s)
    return 1 <= month <= 12 and 1 <= day <= 31


def params_key(analysis_id: str, resolved_params: dict[str, Any] | None) -> str:
    """The ONE canonicalization of "what makes two requests the same stored/
    cached variant" - shared by the Redis cache key (gee_tile_cache.py) and
    the analysis_result DB row's params_key column. Because this is always
    `json.dumps` of the fully-resolved params (never an abbreviated/aliased
    form), decoding is `decode_params_key()` below - the key itself IS the
    payload, no separate "what params produced this row" column is needed.

    Deliberately excludes the 3 browse ids from `_STORAGE_SCOPED_IDS` -
    their existing "DB row doesn't vary by year" gap predates this wave and
    stays out of scope, so their Redis key logic (gee_tile_cache.py's own
    `_YEAR_SELECTABLE_IDS`) is untouched by this function entirely."""
    if analysis_id not in _STORAGE_SCOPED_IDS or not resolved_params:
        return "default"
    return json.dumps(resolved_params, sort_keys=True, separators=(",", ":"))


def decode_params_key(key: str) -> dict[str, Any] | None:
    """Inverse of `params_key()`. `None` for the two sentinels that don't
    encode real params (`"default"` - nothing was ever configured;
    `LEGACY_FULL_RANGE_PARAMS_KEY` - a pre-this-wave row, migrated in place,
    whose exact original request shape was never recorded) - both mean "no
    resolved params to report or replay," not an error."""
    if key in ("default", LEGACY_FULL_RANGE_PARAMS_KEY):
        return None
    return json.loads(key)
