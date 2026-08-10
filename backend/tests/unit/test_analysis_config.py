"""Unit tests for app/domain/analysis_config.py's resolve_and_validate()/
params_key()/decode_params_key() - pure Python, no ee/DB/redis needed.

Spec fixtures here are hand-built AnalysisConfigSpec dicts, not imported from
analysis_catalog.py - this module's contract should hold for any spec shape,
independent of what the catalog happens to declare."""
from __future__ import annotations

import json

import pytest

from app.core.errors import ValidationError
from app.domain.analysis_config import (
    LEGACY_FULL_RANGE_PARAMS_KEY,
    decode_params_key,
    params_key,
    resolve_and_validate,
)

_LAND_COVER_SPEC = {"year_mode_default": "single", "year_min": 2017, "year_max": 2023}

_INDEX_SPEC = {
    "year_mode_default": "single",
    "year_min": 2017,
    "year_max": None,
    "season_editable": True,
    "season_start_default": "02-01",
    "season_end_default": "05-31",
    "imagery_sources": ["sentinel2", "landsat8", "landsat9"],
    "cloud_masking_methods": ["cloud_score_plus", "qa_pixel", "none"],
    "supported_combos": [("sentinel2", "cloud_score_plus")],
}


# ---------------------------------------------------------------- land cover


def test_land_cover_defaults_to_single_latest_year_with_no_raw_params():
    resolved = resolve_and_validate("io_lulc", _LAND_COVER_SPEC, None)
    assert resolved == {"year_mode": "single", "year": 2023}


def test_land_cover_accepts_an_explicit_single_year_in_range():
    resolved = resolve_and_validate("io_lulc", _LAND_COVER_SPEC, {"year": 2019})
    assert resolved == {"year_mode": "single", "year": 2019}


def test_land_cover_rejects_a_year_below_the_domain_floor():
    with pytest.raises(ValidationError, match="between 2017 and 2023"):
        resolve_and_validate("modis_lulc", _LAND_COVER_SPEC, {"year": 2000})


def test_land_cover_rejects_a_year_above_the_domain_ceiling():
    with pytest.raises(ValidationError, match="between 2017 and 2023"):
        resolve_and_validate("io_lulc", _LAND_COVER_SPEC, {"year": 2030})


def test_land_cover_range_mode_defaults_to_the_full_domain():
    resolved = resolve_and_validate("modis_lulc", _LAND_COVER_SPEC, {"year_mode": "range"})
    assert resolved == {"year_mode": "range", "year_start": 2017, "year_end": 2023}


def test_land_cover_range_mode_accepts_a_subset():
    resolved = resolve_and_validate(
        "io_lulc", _LAND_COVER_SPEC, {"year_mode": "range", "year_start": 2018, "year_end": 2020}
    )
    assert resolved == {"year_mode": "range", "year_start": 2018, "year_end": 2020}


def test_land_cover_rejects_a_start_year_after_the_end_year():
    with pytest.raises(ValidationError, match="start year no later than"):
        resolve_and_validate(
            "io_lulc", _LAND_COVER_SPEC, {"year_mode": "range", "year_start": 2020, "year_end": 2018}
        )


def test_land_cover_rejects_an_unknown_year_mode():
    with pytest.raises(ValidationError, match="year_mode"):
        resolve_and_validate("io_lulc", _LAND_COVER_SPEC, {"year_mode": "bogus"})


# --------------------------------------------------------------------- index


def test_index_defaults_fill_in_every_field_with_no_raw_params():
    from app.domain.analysis_config import current_veg_index_years

    resolved = resolve_and_validate("ndvi", _INDEX_SPEC, None)
    assert resolved == {
        "year_mode": "single",
        "year": max(current_veg_index_years()),
        "season_start": "02-01",
        "season_end": "05-31",
        "imagery_source": "sentinel2",
        "cloud_masking": "cloud_score_plus",
    }


def test_index_range_mode_upper_bound_cannot_exceed_the_live_ceiling():
    from app.domain.analysis_config import current_veg_index_years

    live_max = max(current_veg_index_years())
    with pytest.raises(ValidationError, match=f"2017-{live_max}"):
        resolve_and_validate(
            "evi", _INDEX_SPEC, {"year_mode": "range", "year_end": live_max + 5}
        )


def test_index_accepts_a_custom_season_window():
    resolved = resolve_and_validate("savi", _INDEX_SPEC, {"season_start": "03-01", "season_end": "06-15"})
    assert resolved["season_start"] == "03-01"
    assert resolved["season_end"] == "06-15"


def test_index_rejects_a_malformed_season_date():
    with pytest.raises(ValidationError, match="MM-DD"):
        resolve_and_validate("mndwi", _INDEX_SPEC, {"season_start": "not-a-date"})


def test_index_rejects_season_start_on_or_after_season_end():
    with pytest.raises(ValidationError, match="before season end"):
        resolve_and_validate("nbr", _INDEX_SPEC, {"season_start": "06-01", "season_end": "02-01"})


def test_index_accepts_the_one_supported_combo():
    resolved = resolve_and_validate(
        "ndvi", _INDEX_SPEC, {"imagery_source": "sentinel2", "cloud_masking": "cloud_score_plus"}
    )
    assert resolved["imagery_source"] == "sentinel2"
    assert resolved["cloud_masking"] == "cloud_score_plus"


@pytest.mark.parametrize(
    ("source", "masking"),
    [
        ("landsat8", "cloud_score_plus"),
        ("landsat9", "cloud_score_plus"),
        ("sentinel2", "qa_pixel"),
        ("sentinel2", "none"),
        ("landsat8", "qa_pixel"),
    ],
)
def test_index_rejects_every_unsupported_combo_with_a_specific_reason(source, masking):
    with pytest.raises(ValidationError) as exc_info:
        resolve_and_validate(
            "evi", _INDEX_SPEC, {"imagery_source": source, "cloud_masking": masking}
        )
    message = str(exc_info.value)
    assert "Sentinel-2 imagery with Cloud Score+ cloud masking" in message
    assert "isn't implemented yet" in message


# --------------------------------------------------------------- params_key


def test_params_key_is_default_for_ids_with_no_storage_scoping_regardless_of_input():
    for analysis_id in ("hansen_gfc", "dynamic_world", "esa_worldcover", "s2_browse"):
        assert params_key(analysis_id, {"year": 2020}) == "default"
        assert params_key(analysis_id, None) == "default"


def test_params_key_never_produces_the_reserved_legacy_sentinel():
    resolved = resolve_and_validate("ndvi", _INDEX_SPEC, None)
    assert params_key("ndvi", resolved) != LEGACY_FULL_RANGE_PARAMS_KEY


def test_params_key_round_trips_through_decode_params_key():
    resolved = resolve_and_validate("io_lulc", _LAND_COVER_SPEC, {"year": 2021})
    key = params_key("io_lulc", resolved)
    assert json.loads(key) == resolved
    assert decode_params_key(key) == resolved


def test_params_key_varies_by_resolved_content_not_just_presence():
    a = params_key("ndvi", resolve_and_validate("ndvi", _INDEX_SPEC, {"year": 2020}))
    b = params_key("ndvi", resolve_and_validate("ndvi", _INDEX_SPEC, {"year": 2021}))
    assert a != b


def test_decode_params_key_returns_none_for_both_sentinels():
    assert decode_params_key("default") is None
    assert decode_params_key(LEGACY_FULL_RANGE_PARAMS_KEY) is None
