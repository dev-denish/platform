"""Unit tests for report_content.py - the layer whose whole job is proving
the PDF's prose is REUSED, never re-derived. No DB/GEE/PDF library import
needed; every input here is a plain dict shaped exactly like a real
`analysis_result.stats` row (see gee_analysis_service.py's own module
docstring for these shapes)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.analysis_catalog import CATALOG, REAL_ANALYSIS_IDS, get_catalog_entry
from app.services.index_summary import DESCRIPTIVE_ONLY_TRAILER
from app.services.report_content import (
    MULTI_YEAR_INDEX_IDS,
    ClassRow,
    StatRow,
    build_section_content,
    has_change_data,
    has_temporal_data,
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


def test_note_is_the_exact_stats_string_not_reworded():
    entry = get_catalog_entry("ndvi")
    note_text = "Boundary-mean of a cloud-masked, pre-monsoon (Feb-May) Sentinel-2 composite per year."
    stats = {
        "series": {"2025": 0.5, "2026": 0.55},
        "distribution": {
            "2026": {"mean": 0.55, "std_dev": 0.1, "min": -0.1, "max": 0.9,
                      "histogram": {"bin_edges": [], "counts": []}, "out_of_range_pixel_count": 0},
        },
        "coverage_pct": 98.0, "note": note_text,
    }
    section = build_section_content(entry, "ndvi", _NOW, stats, None)
    assert section.note is note_text


def test_narrative_executive_summary_is_built_from_the_real_numbers_not_a_stored_string():
    """Wave: 11-section report restructure. Unlike the old `summary` field
    (a verbatim passthrough of `stats["summary"]`), `narrative["executive_summary"]`
    is RECOMPUTED here from the same underlying mean/coverage figures via
    `report_deterministic_narrative.build_system_narrative` - it must reflect
    the real mean (0.55), not whatever unrelated string `stats["summary"]`
    happens to hold."""
    entry = get_catalog_entry("ndvi")
    stats = {
        "series": {"2025": 0.5, "2026": 0.55},
        "distribution": {
            "2026": {"mean": 0.55, "std_dev": 0.1, "min": -0.1, "max": 0.9,
                      "histogram": {"bin_edges": [], "counts": []}, "out_of_range_pixel_count": 0},
        },
        "coverage_pct": 98.0,
    }
    section = build_section_content(entry, "ndvi", _NOW, stats, None)
    assert "0.55" in section.narrative["executive_summary"]
    assert section.narrative["executive_summary"] != "unrelated stored string"


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


# --------------------------------------------------------------------------
# Wave: 11-section report restructure - has_temporal_data/has_change_data,
# and the 6 always-populated deterministic sections.
# --------------------------------------------------------------------------


def test_has_temporal_data_requires_at_least_two_real_data_points():
    assert has_temporal_data({"series": {"2025": 0.4, "2026": 0.5}})
    assert not has_temporal_data({"series": {"2026": 0.5}})
    assert not has_temporal_data({"series": {"2025": None, "2026": 0.5}})
    assert has_temporal_data({"class_area_ha_by_year": {"2020": {}, "2023": {}}})
    assert not has_temporal_data({"class_area_ha_by_year": {"2023": {}}})
    assert not has_temporal_data({"class_area_ha": {}})


def test_has_change_data_is_true_only_for_a_real_non_empty_loss_by_year():
    assert has_change_data({"loss_area_ha_by_year": {"2010": 2.0}})
    assert not has_change_data({"loss_area_ha_by_year": {}})
    assert not has_change_data({})


def test_temporal_analysis_omitted_for_a_single_year_io_lulc_row_present_for_two_years():
    """The same catalog id (io_lulc) must produce a `temporal_analysis` key
    when its stored row actually has >=2 years, and omit it when the row is
    single-year - this is a per-ROW capability, not a per-id one (see
    `has_temporal_data`'s own docstring)."""
    entry = get_catalog_entry("io_lulc")
    single_year_stats = {
        "class_area_ha_by_year": {"2023": {"Trees": 12.0}}, "coverage_pct": 100.0,
    }
    multi_year_stats = {
        "class_area_ha_by_year": {"2020": {"Trees": 10.0}, "2023": {"Trees": 12.0}},
        "coverage_pct": 100.0,
    }
    single = build_section_content(entry, "io_lulc", _NOW, single_year_stats, None)
    multi = build_section_content(entry, "io_lulc", _NOW, multi_year_stats, None)
    assert "temporal_analysis" not in single.narrative
    assert "temporal_analysis" in multi.narrative


def test_change_analysis_present_only_for_hansen():
    hansen_entry = get_catalog_entry("hansen_gfc")
    hansen_stats = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
        "gain_area_ha_2000_2012": 5.0, "loss_area_ha_by_year": {"2010": 2.0},
        "coverage_pct": 100.0,
    }
    hansen_section = build_section_content(hansen_entry, "hansen_gfc", _NOW, hansen_stats, None)
    assert "change_analysis" in hansen_section.narrative
    assert "temporal_analysis" not in hansen_section.narrative

    esa_entry = get_catalog_entry("esa_worldcover")
    esa_stats = {"class_area_ha": {"Tree cover": 42.0}, "coverage_pct": 100.0}
    esa_section = build_section_content(esa_entry, "esa_worldcover", _NOW, esa_stats, None)
    assert "change_analysis" not in esa_section.narrative
    assert "temporal_analysis" not in esa_section.narrative


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 report-generation fix: VNV band-index results
# (run_vnv_band_index_analysis's real stats shape) must populate the stat
# grid and disclose cloud masking, the same way a GEE vegetation index does.
# --------------------------------------------------------------------------


def _vnv_ndvi_stats(**overrides):
    stats = {
        "index": "vnv_ndvi",
        "min": 0.1, "max": 0.9, "mean": 0.55, "std_dev": 0.12,
        "valid_pixel_count": 9000, "total_pixel_count": 10000, "coverage_pct": 90.0,
        "scene_count": 3,
        "distribution": {"latest": {"mean": 0.55, "std_dev": 0.12, "min": 0.1, "max": 0.9}},
        "note": "Computed from a 90-day trailing Sentinel-2 composite.",
    }
    stats.update(overrides)
    return stats


def test_vnv_band_index_stats_grid_is_populated_not_empty():
    """Before this fix, `stats_grid` stayed [] for every VNV band-index
    result (its stats never had a `"distribution"` key keyed by year), which
    silently skipped the entire Statistics section in the PDF - see
    report_pdf.py's `if section.stats_grid:` gate."""
    entry = get_catalog_entry("vnv_ndvi")
    stats = _vnv_ndvi_stats()
    section = build_section_content(entry, "vnv_ndvi", _NOW, stats, None)
    assert section.stats_grid == [
        StatRow("Mean", 0.55), StatRow("Variability", 0.12), StatRow("Min", 0.1), StatRow("Max", 0.9),
    ]
    assert section.stats_grid_year is None


def test_vnv_band_index_narrative_does_not_crash_or_mislabel_as_a_single_scene():
    entry = get_catalog_entry("vnv_ndvi")
    stats = _vnv_ndvi_stats()
    section = build_section_content(entry, "vnv_ndvi", _NOW, stats, None)
    assert "single raw scene" not in section.narrative["executive_summary"]
    assert "0.55" in section.narrative["executive_summary"]


def test_vnv_band_index_data_quality_text_discloses_cloud_masking():
    """Before this fix, `has_cloud_masking` read the raw
    `stats.get("methodology")` (always None for VNV) instead of resolving
    through the fallback dict `METHODOLOGY_FALLBACK` now carries a
    `cloud_masking` entry for - so this sentence never appeared for any VNV
    analysis even after the fallback text existed."""
    entry = get_catalog_entry("vnv_ndvi")
    stats = _vnv_ndvi_stats()
    section = build_section_content(entry, "vnv_ndvi", _NOW, stats, None)
    assert "90.0%" in section.data_quality_text
    assert "masked before computing statistics" in section.data_quality_text


def test_vnv_band_index_data_processing_text_discloses_cloud_masking_too():
    """`data_processing_text` already resolves through the fallback (it did
    before this fix too) - confirms the same fallback entry backs both
    sections consistently."""
    entry = get_catalog_entry("vnv_evi")
    stats = _vnv_ndvi_stats(index="vnv_evi")
    section = build_section_content(entry, "vnv_evi", _NOW, stats, None)
    assert "Cloud masking:" in section.data_processing_text
    assert "SCL" in section.data_processing_text


def test_vnv_ndfi_data_quality_text_discloses_cloud_masking_despite_no_coverage_pct():
    """carbon-mrv-vm0047 review (M1): vnv_ndfi's real stats dict (built from
    the R sidecar's own payload, see vnv_analysis_jobs.py) has no
    `coverage_pct` key at all - before this fix, `data_quality_text`'s early
    `if coverage_pct is None: return "Coverage could not be determined..."`
    silently dropped the masking sentence too, even though `has_cloud_masking`
    already resolves True via `METHODOLOGY_FALLBACK["vnv_ndfi"]`."""
    entry = get_catalog_entry("vnv_ndfi")
    stats = {
        "min": 0.0, "max": 1.0, "mean": 0.02, "valid_pixel_count": 10,
        "total_pixel_count": 4200, "masked_fraction": 0.9976,
    }
    section = build_section_content(entry, "vnv_ndfi", _NOW, stats, None)
    assert "masked before computing statistics" in section.data_quality_text
    assert section.data_quality_text != "Coverage could not be determined for this analysis."


@pytest.mark.parametrize("analysis_id", sorted(REAL_ANALYSIS_IDS))
def test_the_6_deterministic_sections_are_always_populated_for_every_real_analysis_type(
    analysis_id,
):
    """Carbon Project Relevance/Methodology/Data & Processing/Data Quality/
    Limitations must never be empty for any of the 13 real catalog ids -
    these are never AI-eligible and must always render something."""
    entry = get_catalog_entry(analysis_id)
    minimal_stats_by_shape = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 1.0,
        "gain_area_ha_2000_2012": 0.0, "loss_area_ha_by_year": {}, "coverage_pct": 90.0,
    } if analysis_id == "hansen_gfc" else (
        {"class_area_ha_by_year": {"2023": {"Trees": 1.0}}, "coverage_pct": 90.0}
        if analysis_id in ("io_lulc", "modis_lulc") else (
            {"series": {"2026": 0.4}, "distribution": {
                "2026": {"mean": 0.4, "std_dev": 0.1, "min": 0.1, "max": 0.7,
                         "histogram": {"bin_edges": [-1, 1], "counts": [1]},
                         "out_of_range_pixel_count": 0},
            }, "coverage_pct": 90.0}
            if entry["category"] == "Vegetation Indices" else (
                {"scene_date": "2026-01-01", "coverage_pct": 90.0}
                if entry["category"] == "Raw Imagery" else
                {"class_area_ha": {"Tree cover": 1.0}, "coverage_pct": 90.0}
            )
        )
    )
    section = build_section_content(entry, analysis_id, _NOW, minimal_stats_by_shape, None)
    assert section.methodology_text.strip()
    assert section.data_processing_text.strip()
    assert section.data_quality_text.strip()
    assert section.carbon_project_relevance.strip()
    assert section.limitations_text.strip()
    assert section.narrative["executive_summary"].strip()
    assert section.narrative["spatial_distribution"].strip()
    assert section.narrative["key_findings"].strip()
