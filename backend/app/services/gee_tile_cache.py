"""Cache-key and TTL policy for GEE tile/stats caching (Wave: GEE tile
caching). Pure, no `redis`/`ee` import here - unit-testable without either
(the actual Redis access lives in gee_analysis_service.py's `_cache_get`/
`_cache_set`, its own monkeypatchable seam, same convention as `_compute`).

Why cache at all: every `refresh()`/async-job call hits GEE fresh today -
`.getMapId()` and the underlying `reduceRegion`/compositing are real,
measurable cost (the vegetation indices alone take 5-59s, see
gee_analysis_service.py's own module docstring). A repeat view of the same
(project, analysis, params) combination within a short window - re-opening
a project, a page refresh, another team member looking at the same result -
should not re-pay that cost or re-mint a fresh GEE tile token.

TTL design has two independently-computed pieces, `min()`'d together:

1. A CEILING (`_TILE_URL_SAFETY_CEILING_SECONDS`), because the cached
   value includes a real `tile_url_template` string - a GEE `getMapId()`
   tile-fetcher URL - and that URL's own expiry is UNDOCUMENTED anywhere
   findable (checked: neither the API reference nor the image-visualization
   guide state a lifetime for it). The only related, DOCUMENTED figure is
   `getThumbURL()`'s 2-hour token lifetime - a different but related GEE
   tile-serving mechanism. Using that as the ceiling is an explicit,
   flagged ANALOGY, not a proven fact for `getMapId()` specifically: if a
   cached tile_url_template outlives the real (unknown) token lifetime, a
   stale-but-still-cached entry would serve a URL whose tiles start
   404ing/401ing before the cache entry itself expires. Capping at the one
   number GEE does document keeps that risk bounded to something concrete
   rather than an arbitrary guess.

2. Real per-source CADENCE, computed independently of the ceiling so a
   future relaxation of the ceiling (e.g. once real `getMapId()` expiry is
   confirmed by Google or by direct measurement) is a one-constant change,
   not a redesign:
   - s2_browse (and every "current", non-year-selectable analysis, which
     is Sentinel-2-based for the 5 vegetation indices and effectively
     "whatever's newest" for the 5 land-cover ones): Sentinel-2's own
     revisit is ~1-3 days (two/three satellites in constellation) - 1 day.
   - s1_browse: Sentinel-1's GRD revisit is ~6-12 days depending on
     constellation availability - 6 days (the faster end, so a cache never
     holds noticeably stale radar longer than the best case would justify).
   - landsat_browse: Landsat 8+9 combined (this module's own browse
     function merges both) achieve ~8-16 days effective revisit - 8 days.
   - A "historical" request (an explicit year strictly before the current
     calendar year) gets a much longer TTL (30 days): closed-year imagery
     essentially never changes, but GEE dataset reprocessing/corrections
     are rare-but-real, so it is never cached as if permanent.

   The ceiling DOMINATES every one of these today (all real cadences, even
   the fastest, exceed 2 hours), so the current-vs-historical distinction
   has NO numeric effect yet - kept anyway, tested anyway (see
   tests/unit/test_gee_tile_cache.py's own "ceiling wins today" assertion),
   so this isn't a redesign later, just a constant change.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.domain import analysis_config

_TILE_URL_SAFETY_CEILING_SECONDS = 2 * 60 * 60  # 2h - see module docstring

_CURRENT_PERIOD_CADENCE_SECONDS: dict[str, int] = {
    "s2_browse": 1 * 86_400,
    "s1_browse": 6 * 86_400,
    "landsat_browse": 8 * 86_400,
}
_DEFAULT_CURRENT_CADENCE_SECONDS = 1 * 86_400  # the original 10 analyses - S2-class cadence
_HISTORICAL_TTL_SECONDS = 30 * 86_400

# The 3 browse ids whose request accepts a `year` param (Wave: raw-imagery
# browsing) - their own key suffix logic below, untouched by Wave: analysis
# config and methodology (their pre-existing "DB row doesn't vary by year"
# gap predates that wave and stays out of its scope - see analysis_config.py's
# own `_STORAGE_SCOPED_IDS` comment).
_YEAR_SELECTABLE_IDS = frozenset(_CURRENT_PERIOD_CADENCE_SECONDS)


def cache_key(
    project_id: str, analysis_id: str, request_params: dict[str, Any] | None = None
) -> str:
    """`gee_analysis:{project}:{analysis}` for every analysis with nothing
    configurable at all (hansen_gfc/dynamic_world/esa_worldcover) - one
    cached entry per project+analysis. `gee_analysis:{project}:{analysis}:
    {year|"latest"}` for the 3 year-selectable browse ids - a different year
    is deliberately a cache MISS, never treated as interchangeable with any
    other year. `gee_analysis:{project}:{analysis}:{resolved params, as
    canonical JSON}` for the 7 ids Wave: analysis config and methodology made
    configurable (io_lulc/modis_lulc/the 5 vegetation indices) - delegates to
    `analysis_config.params_key()`, the SAME canonicalization the
    analysis_result DB row's params_key column uses, so there is exactly one
    idea of "what makes two requests the same variant", not two."""
    base = f"gee_analysis:{project_id}:{analysis_id}"
    if analysis_id in _YEAR_SELECTABLE_IDS:
        year = (request_params or {}).get("year")
        return f"{base}:{year if year is not None else 'latest'}"
    suffix = analysis_config.params_key(analysis_id, request_params)
    return base if suffix == "default" else f"{base}:{suffix}"


def ttl_seconds(
    analysis_id: str, request_params: dict[str, Any] | None = None, today: date | None = None
) -> int:
    """Real per-source-cadence TTL, capped by _TILE_URL_SAFETY_CEILING_SECONDS
    (see module docstring for why both numbers exist independently).
    `today` is injectable purely for deterministic unit testing (same
    convention as `_current_veg_index_years`'s own `today` parameter in
    gee_analysis_service.py), not a real per-request override."""
    if today is None:
        today = date.today()
    year = (request_params or {}).get("year")
    is_historical = year is not None and year < today.year
    cadence = (
        _HISTORICAL_TTL_SECONDS
        if is_historical
        else _CURRENT_PERIOD_CADENCE_SECONDS.get(analysis_id, _DEFAULT_CURRENT_CADENCE_SECONDS)
    )
    return min(cadence, _TILE_URL_SAFETY_CEILING_SECONDS)
