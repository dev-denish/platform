"""Pure-logic tests for gee_tile_cache (Wave: GEE tile caching) - no DB, no
GEE, no Redis. Real cache hit/miss/invalidation behavior against a fake
Redis is covered in tests/integration/test_gee_analysis_service.py's
`fake_cache` fixture; this file only tests the key/TTL arithmetic."""
from __future__ import annotations

from datetime import date

from app.services import gee_tile_cache
from app.services.gee_tile_cache import cache_key, ttl_seconds

_PROJECT = "11111111-1111-1111-1111-111111111111"
_TWO_HOURS = 2 * 60 * 60


def test_cache_key_for_an_unconfigurable_analysis_has_no_param_suffix():
    # No request_params at all - hansen_gfc/dynamic_world/esa_worldcover have
    # nothing configurable (Wave: analysis config and methodology left them
    # untouched - see analysis_config.py's own scope comment).
    assert cache_key(_PROJECT, "dynamic_world") == f"gee_analysis:{_PROJECT}:dynamic_world"
    assert cache_key(_PROJECT, "hansen_gfc", None) == f"gee_analysis:{_PROJECT}:hansen_gfc"


def test_cache_key_for_an_unconfigurable_analysis_ignores_a_stray_request_params():
    # Defensive: even if a caller somehow passed request_params for an id
    # with nothing configurable, the key must not vary by it.
    assert cache_key(_PROJECT, "hansen_gfc", {"year": 2020}) == f"gee_analysis:{_PROJECT}:hansen_gfc"


def test_cache_key_for_ndvi_varies_by_resolved_year():
    # ndvi is one of the 7 ids Wave: analysis config and methodology made
    # configurable - its key now varies by the (already-resolved) params
    # dict, same as the browse ids vary by year, just via
    # analysis_config.params_key()'s canonical-JSON suffix instead.
    key_2022 = cache_key(_PROJECT, "ndvi", {"year_mode": "single", "year": 2022})
    key_2023 = cache_key(_PROJECT, "ndvi", {"year_mode": "single", "year": 2023})
    assert key_2022 != key_2023
    assert key_2022 == cache_key(_PROJECT, "ndvi", {"year_mode": "single", "year": 2022})


def test_cache_key_for_ndvi_with_no_params_is_still_default():
    # cache_key() itself doesn't resolve defaults (that's resolve_and_validate's
    # job, called upstream) - called with nothing at all, it degrades to the
    # same bare key every unconfigurable analysis gets.
    assert cache_key(_PROJECT, "ndvi", None) == f"gee_analysis:{_PROJECT}:ndvi"


def test_cache_key_for_io_lulc_varies_by_year_range():
    key_a = cache_key(_PROJECT, "io_lulc", {"year_mode": "range", "year_start": 2018, "year_end": 2020})
    key_b = cache_key(_PROJECT, "io_lulc", {"year_mode": "range", "year_start": 2018, "year_end": 2021})
    assert key_a != key_b


def test_cache_key_for_a_browse_layer_with_no_year_is_latest():
    assert cache_key(_PROJECT, "s2_browse") == f"gee_analysis:{_PROJECT}:s2_browse:latest"


def test_cache_key_for_a_browse_layer_varies_by_year():
    key_2022 = cache_key(_PROJECT, "s2_browse", {"year": 2022})
    key_2023 = cache_key(_PROJECT, "s2_browse", {"year": 2023})
    assert key_2022 == f"gee_analysis:{_PROJECT}:s2_browse:2022"
    assert key_2023 == f"gee_analysis:{_PROJECT}:s2_browse:2023"
    assert key_2022 != key_2023  # a different year MUST be a different cache entry


def test_cache_key_varies_by_project_and_analysis_id_too():
    assert cache_key(_PROJECT, "ndvi") != cache_key(_PROJECT, "evi")
    other_project = "22222222-2222-2222-2222-222222222222"
    assert cache_key(_PROJECT, "ndvi") != cache_key(other_project, "ndvi")


def test_ttl_for_original_analyses_uses_the_default_current_cadence():
    # 1 day (86400s) capped by the 2h ceiling - see module docstring.
    assert ttl_seconds("ndvi") == _TWO_HOURS
    assert ttl_seconds("hansen_gfc") == _TWO_HOURS


def test_ttl_ceiling_dominates_every_real_cadence_today():
    # The whole point of the 2h safety ceiling (see gee_tile_cache.py's own
    # docstring): every real per-source cadence today (1/6/8 days) is far
    # longer than 2h, so the ceiling - not the cadence - is what actually
    # governs every one of these right now. This test exists so a future
    # relaxation of the ceiling is a deliberate, visible change here, not a
    # silent behavior shift.
    today = date(2024, 6, 15)
    for analysis_id in ("s2_browse", "s1_browse", "landsat_browse"):
        assert ttl_seconds(analysis_id, {"year": 2024}, today=today) == _TWO_HOURS


def test_ttl_with_no_year_is_never_historical():
    assert ttl_seconds("s2_browse", None) == _TWO_HOURS
    assert ttl_seconds("s2_browse", {}) == _TWO_HOURS


def test_current_vs_historical_branch_actually_differs_once_unmasked_by_the_ceiling(monkeypatch):
    """The ceiling dominates every case above by design (see module
    docstring), which means none of those tests can actually SEE the
    current-vs-historical branch working - they'd pass identically even if
    that branch were deleted. Raising the ceiling here (monkeypatched, not a
    real config change) unmasks the real cadence numbers so this test can
    prove the branch itself is correct: a past year gets the much longer
    historical TTL, a current-or-future year gets the short per-source
    cadence, and the boundary (exactly `today.year`) falls on the
    NOT-historical side."""
    monkeypatch.setattr(gee_tile_cache, "_TILE_URL_SAFETY_CEILING_SECONDS", 10**9)
    today = date(2024, 6, 15)

    assert ttl_seconds("s2_browse", {"year": 2023}, today=today) == 30 * 86_400  # historical
    assert ttl_seconds("s2_browse", {"year": 2024}, today=today) == 1 * 86_400  # current year, not historical
    assert ttl_seconds("s1_browse", {"year": 2024}, today=today) == 6 * 86_400
    assert ttl_seconds("landsat_browse", {"year": 2024}, today=today) == 8 * 86_400
    assert ttl_seconds("s2_browse", {"year": 2025}, today=today) == 1 * 86_400  # future, not historical
