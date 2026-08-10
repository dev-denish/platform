"""Unit tests for report_content.py - the layer whose whole job is proving
the PDF's prose is REUSED, never re-derived. No DB/GEE/PDF library import
needed; every input here is a plain dict shaped exactly like a real
`analysis_result.stats` row (see gee_analysis_service.py's own module
docstring for these shapes)."""
from __future__ import annotations

from datetime import UTC, datetime

from app.domain.analysis_catalog import CATALOG, get_catalog_entry
from app.services.index_summary import DESCRIPTIVE_ONLY_TRAILER
from app.services.report_content import (
    MULTI_YEAR_INDEX_IDS,
    ClassRow,
    build_section_content,
    is_multi_year_index,
)

_NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_multi_year_index_ids_match_the_catalog_exactly():
    """A future 6th vegetation index added to the catalog must also be added
    here - this test fails loudly instead of silently rendering a single-
    snapshot section (no trend chart) for a genuinely multi-year index."""
    catalog_veg_ids = {e["id"] for e in CATALOG if e["category"] == "Vegetation Indices"}
    assert catalog_veg_ids == MULTI_YEAR_INDEX_IDS
    for analysis_id in catalog_veg_ids:
        assert is_multi_year_index(analysis_id)
    assert not is_multi_year_index("hansen_gfc")


def test_description_is_the_exact_catalog_string_not_reworded():
    entry = get_catalog_entry("hansen_gfc")
    stats = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
        "gain_area_ha_2000_2012": 2.0, "loss_area_ha_by_year": {"2005": 1.0},
        "coverage_pct": 100.0, "note": "Gain is a whole-period 2000-2012 figure only.",
    }
    section = build_section_content(entry, "hansen_gfc", _NOW, stats, None)
    # Identity, not just equality - proves no string was built/concatenated,
    # the exact object the catalog already holds was passed straight through.
    assert section.description is entry["description"]


def test_note_and_summary_are_the_exact_stats_strings_not_reworded():
    entry = get_catalog_entry("ndvi")
    note_text = "Boundary-mean of a cloud-masked, pre-monsoon (Feb-May) Sentinel-2 composite per year."
    summary_text = "2026: NDVI averages 0.55 across the boundary - moderate. " + DESCRIPTIVE_ONLY_TRAILER
    stats = {
        "series": {"2025": 0.5, "2026": 0.55},
        "distribution": {
            "2026": {"mean": 0.55, "std_dev": 0.1, "min": -0.1, "max": 0.9,
                      "histogram": {"bin_edges": [], "counts": []}, "out_of_range_pixel_count": 0},
        },
        "summary": summary_text, "coverage_pct": 98.0, "note": note_text,
    }
    section = build_section_content(entry, "ndvi", _NOW, stats, None)
    assert section.note is note_text
    assert section.summary is summary_text


def test_disclaimer_is_the_same_constant_every_analysis_type_gets():
    """Every section gets DESCRIPTIVE_ONLY_TRAILER regardless of whether its
    own stats["summary"]/["note"] happen to already end with it - Hansen has
    no `summary` at all, so this is the ONLY place its disclaimer comes from."""
    hansen_entry = get_catalog_entry("hansen_gfc")
    hansen_stats = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 1.0,
        "gain_area_ha_2000_2012": 0.0, "loss_area_ha_by_year": {}, "coverage_pct": 100.0,
    }
    ndvi_entry = get_catalog_entry("ndvi")
    ndvi_stats = {"series": {}, "distribution": {}, "summary": "x", "coverage_pct": 100.0}

    hansen_section = build_section_content(hansen_entry, "hansen_gfc", _NOW, hansen_stats, None)
    ndvi_section = build_section_content(ndvi_entry, "ndvi", _NOW, ndvi_stats, None)

    assert hansen_section.disclaimer is DESCRIPTIVE_ONLY_TRAILER
    assert ndvi_section.disclaimer is DESCRIPTIVE_ONLY_TRAILER


def test_stats_grid_uses_the_same_four_labels_the_ui_renders():
    """Must match IndexDistribution's own labels exactly (AnalysisPanel.jsx:
    "Mean ({year})"/"Variability ({year})"/"Min ({year})"/"Max ({year})") -
    "Variability" is the UI's label for the backend's std_dev field, kept
    identical here so a reader sees the same word in both places."""
    entry = get_catalog_entry("evi")
    stats = {
        "series": {"2026": 0.3},
        "distribution": {
            "2026": {"mean": 0.3, "std_dev": 0.05, "min": -0.2, "max": 0.8,
                      "histogram": {"bin_edges": [], "counts": []}, "out_of_range_pixel_count": 0},
        },
        "summary": "x", "coverage_pct": 100.0,
    }
    section = build_section_content(entry, "evi", _NOW, stats, None)
    assert [row.label for row in section.stats_grid] == ["Mean", "Variability", "Min", "Max"]
    assert [row.value for row in section.stats_grid] == [0.3, 0.05, -0.2, 0.8]
    assert section.stats_grid_year == "2026"


def test_stats_grid_uses_the_latest_year_when_multiple_are_present():
    entry = get_catalog_entry("ndvi")
    stats = {
        "series": {"2024": 0.4, "2025": 0.5, "2026": 0.6},
        "distribution": {
            "2024": {"mean": 0.4, "std_dev": 0.1, "min": 0.0, "max": 0.7,
                      "histogram": None, "out_of_range_pixel_count": 0},
            "2026": {"mean": 0.6, "std_dev": 0.2, "min": 0.1, "max": 0.9,
                      "histogram": None, "out_of_range_pixel_count": 0},
        },
        "summary": "x", "coverage_pct": 100.0,
    }
    section = build_section_content(entry, "ndvi", _NOW, stats, None)
    assert section.stats_grid_year == "2026"
    assert section.series == stats["series"]


def test_single_year_classified_breakdown():
    entry = get_catalog_entry("esa_worldcover")
    legend = [{"code": 10, "name": "Tree cover", "color": "#006400"}]
    stats = {"class_area_ha": {"Tree cover": 42.0}, "coverage_pct": 100.0}
    section = build_section_content(entry, "esa_worldcover", _NOW, stats, legend)
    assert section.class_breakdown == [ClassRow("Tree cover", 42.0, "#006400")]
    assert section.class_breakdown_year is None


def test_multi_year_classified_breakdown_uses_the_latest_year():
    entry = get_catalog_entry("io_lulc")
    stats = {
        "class_area_ha_by_year": {
            "2020": {"Trees": 10.0},
            "2023": {"Trees": 12.0},
        },
        "coverage_pct": 100.0,
    }
    section = build_section_content(entry, "io_lulc", _NOW, stats, None)
    assert section.class_breakdown_year == "2023"
    assert [row.area_ha for row in section.class_breakdown] == [12.0]


def test_hansen_reshapes_its_own_fields_into_the_same_row_shape():
    entry = get_catalog_entry("hansen_gfc")
    stats = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
        "gain_area_ha_2000_2012": 5.0, "loss_area_ha_by_year": {"2003": 1.0, "2010": 2.0},
        "coverage_pct": 100.0,
    }
    section = build_section_content(entry, "hansen_gfc", _NOW, stats, None)
    names = [row.name for row in section.class_breakdown]
    assert names[0].startswith("Baseline forest area")
    assert "Gain, 2000-2012" in names
    assert "Loss, 2003" in names
    assert "Loss, 2010" in names


def test_coverage_pct_passes_through_unmodified():
    entry = get_catalog_entry("dynamic_world")
    stats = {"class_area_ha": {}, "coverage_pct": 87.3}
    section = build_section_content(entry, "dynamic_world", _NOW, stats, None)
    assert section.coverage_pct == 87.3
