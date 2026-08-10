"""Unit tests for the pure (no `ee`) methodology-building pieces added by
Wave: analysis config and methodology - _years_from_resolved/_years_label/
_land_cover_methodology/_veg_index_methodology. These are factored out of
_esri_lulc/_modis_lulc/_annual_index_series specifically so they're
unit-testable without a live, authenticated GEE session (same constraint
tests/unit/test_gee_point_query.py's own docstring documents for
_compute_point's leaf functions) - this file proves the methodology content
a user sees actually reflects whatever params were resolved, independent of
the real GEE compute path."""
from __future__ import annotations

from app.services.gee_analysis_service import (
    _land_cover_methodology,
    _veg_index_methodology,
    _years_from_resolved,
    _years_label,
)

# --------------------------------------------------------- _years_from_resolved


def test_years_from_resolved_falls_back_to_a_single_year_with_no_params():
    assert _years_from_resolved(None, 2023) == [2023]
    assert _years_from_resolved({}, 2023) == [2023]


def test_years_from_resolved_single_mode_uses_the_resolved_year():
    assert _years_from_resolved({"year_mode": "single", "year": 2019}, 2023) == [2019]


def test_years_from_resolved_single_mode_with_no_year_key_falls_back():
    assert _years_from_resolved({"year_mode": "single"}, 2023) == [2023]


def test_years_from_resolved_range_mode_expands_inclusively():
    resolved = {"year_mode": "range", "year_start": 2018, "year_end": 2020}
    assert _years_from_resolved(resolved, 2023) == [2018, 2019, 2020]


def test_years_from_resolved_range_mode_single_year_span():
    resolved = {"year_mode": "range", "year_start": 2020, "year_end": 2020}
    assert _years_from_resolved(resolved, 2023) == [2020]


# ----------------------------------------------------------------- _years_label


def test_years_label_single_year():
    assert _years_label([2023]) == "2023"


def test_years_label_range():
    assert _years_label([2018, 2019, 2020]) == "2018-2020"


def test_years_label_unsorted_input_still_uses_min_max():
    assert _years_label([2020, 2018, 2019]) == "2018-2020"


# --------------------------------------------------------- _land_cover_methodology


def test_land_cover_methodology_reflects_the_requested_years_not_the_full_domain():
    methodology = _land_cover_methodology(
        "10m Annual Land Cover (Esri / Impact Observatory)", [2019], (2017, 2023), 10
    )
    assert methodology["years_computed"] == [2019]
    assert methodology["years_available"] == "2017-2023"
    assert methodology["dataset"] == "10m Annual Land Cover (Esri / Impact Observatory)"
    assert methodology["resolution_m"] == 10


def test_land_cover_methodology_has_no_internal_identifiers():
    methodology = _land_cover_methodology(
        "MODIS Land Cover Type (MCD12Q1, IGBP classification)", [2020, 2021], (2001, 2023), 500
    )
    text = " ".join(str(v) for v in methodology.values())
    assert ".py" not in text
    assert "gee_analysis_service" not in text
    assert "_esri_lulc" not in text and "_modis_lulc" not in text


# ---------------------------------------------------------- _veg_index_methodology


def test_veg_index_methodology_labels_the_one_supported_combo_in_words():
    methodology = _veg_index_methodology(
        "sentinel2", "cloud_score_plus", "02-01", "05-31", [2023], "NDVI = (NIR - Red) / (NIR + Red)."
    )
    assert methodology["imagery_source"] == "Sentinel-2 (COPERNICUS/S2_SR_HARMONIZED)"
    assert "Cloud Score+" in methodology["cloud_masking"]
    assert methodology["season_window"] == "02-01 to 05-31"
    assert methodology["years_computed"] == [2023]
    assert methodology["formula"] == "NDVI = (NIR - Red) / (NIR + Red)."


def test_veg_index_methodology_reflects_a_custom_season_window():
    methodology = _veg_index_methodology(
        "sentinel2", "cloud_score_plus", "03-01", "06-15", [2020, 2021], "EVI = ..."
    )
    assert methodology["season_window"] == "03-01 to 06-15"


def test_veg_index_methodology_reflects_a_multi_year_range():
    methodology = _veg_index_methodology(
        "sentinel2", "cloud_score_plus", "02-01", "05-31", [2018, 2019, 2020], "NDVI = ..."
    )
    assert methodology["years_computed"] == [2018, 2019, 2020]


def test_veg_index_methodology_passes_through_an_unrecognized_source_unlabeled():
    # Defensive: resolve_and_validate rejects anything but sentinel2/
    # cloud_score_plus before this ever runs, but the labeling logic itself
    # shouldn't fabricate a human name for a value it doesn't recognize.
    methodology = _veg_index_methodology(
        "landsat9", "qa_pixel", "02-01", "05-31", [2023], "NDVI = ..."
    )
    assert methodology["imagery_source"] == "landsat9"
    assert methodology["cloud_masking"] == "qa_pixel"
