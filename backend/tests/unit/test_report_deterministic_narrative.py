"""Unit tests for report_deterministic_narrative.py - the system-report
equivalent of ai_narrative.py's 5 AI-eligible fields, for all 13 analysis
types. Pure Python, no LLM/GEE/DB."""
from __future__ import annotations

from app.services.report_deterministic_narrative import build_system_narrative

_ALWAYS_KEYS = {"executive_summary", "spatial_distribution", "key_findings"}


def test_hansen_narrative_has_change_analysis_not_temporal_analysis():
    stats = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
        "gain_area_ha_2000_2012": 5.0,
        "loss_area_ha_by_year": {"2003": 1.0, "2010": 4.0},
        "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("hansen_gfc", "Hansen", "Forest Change", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "change_analysis" in narrative
    assert "temporal_analysis" not in narrative
    assert "100.00 ha" in narrative["executive_summary"]
    assert "2010" in narrative["change_analysis"]  # the real peak-loss year


def test_hansen_narrative_change_analysis_omitted_when_no_loss_recorded():
    stats = {
        "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
        "gain_area_ha_2000_2012": 0.0, "loss_area_ha_by_year": {}, "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("hansen_gfc", "Hansen", "Forest Change", stats)
    assert "change_analysis" not in narrative


def test_single_year_classified_narrative_has_no_temporal_or_change():
    stats = {
        "class_area_ha": {"Tree cover": 80.0, "Cropland": 20.0}, "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("esa_worldcover", "ESA WorldCover", "Land Cover", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "temporal_analysis" not in narrative
    assert "change_analysis" not in narrative
    assert "Tree cover" in narrative["spatial_distribution"]


def test_multi_year_classified_narrative_gets_temporal_analysis():
    stats = {
        "class_area_ha_by_year": {
            "2020": {"Trees": 10.0, "Cropland": 5.0},
            "2023": {"Trees": 12.0, "Cropland": 3.0},
        },
        "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("io_lulc", "IO LULC", "Land Cover", stats)
    assert "temporal_analysis" in narrative
    assert "change_analysis" not in narrative
    assert "2020" in narrative["temporal_analysis"]
    assert "2023" in narrative["temporal_analysis"]


def test_single_year_multi_year_capable_analysis_omits_temporal_analysis():
    stats = {
        "class_area_ha_by_year": {"2023": {"Trees": 12.0}}, "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("io_lulc", "IO LULC", "Land Cover", stats)
    assert "temporal_analysis" not in narrative


def test_browse_narrative_has_no_spatial_data_and_no_temporal_or_change():
    stats = {"scene_date": "2026-03-01", "cloud_pct": 2.5, "coverage_pct": 99.0}
    narrative = build_system_narrative("s2_browse", "Sentinel-2 True Color", "Raw Imagery", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "temporal_analysis" not in narrative
    assert "change_analysis" not in narrative
    assert "2026-03-01" in narrative["executive_summary"]
    assert "single raw scene" in narrative["spatial_distribution"]


def test_veg_index_narrative_gets_temporal_analysis_with_a_real_trend():
    stats = {
        "series": {"2024": 0.4, "2025": 0.5, "2026": 0.6},
        "distribution": {
            "2024": {"mean": 0.4, "std_dev": 0.1, "min": 0.0, "max": 0.7,
                      "histogram": {"bin_edges": [-1, -0.5, 1], "counts": [1, 1]},
                      "out_of_range_pixel_count": 0},
            "2026": {"mean": 0.6, "std_dev": 0.1, "min": 0.1, "max": 0.8,
                      "histogram": {"bin_edges": [-1, -0.5, 1], "counts": [1, 1]},
                      "out_of_range_pixel_count": 0},
        },
        "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("ndvi", "NDVI", "Vegetation Indices", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "temporal_analysis" in narrative
    assert "change_analysis" not in narrative
    assert "0.60" in narrative["executive_summary"]


def test_veg_index_narrative_omits_temporal_analysis_for_a_single_year():
    stats = {
        "series": {"2026": 0.6},
        "distribution": {
            "2026": {"mean": 0.6, "std_dev": 0.1, "min": 0.1, "max": 0.8,
                      "histogram": {"bin_edges": [-1, 1], "counts": [1]},
                      "out_of_range_pixel_count": 0},
        },
        "coverage_pct": 100.0,
    }
    narrative = build_system_narrative("ndvi", "NDVI", "Vegetation Indices", stats)
    assert "temporal_analysis" not in narrative


def test_veg_index_narrative_handles_a_year_with_no_usable_pixels():
    stats = {"series": {"2026": None}, "distribution": {"2026": {"mean": None}}, "coverage_pct": 0.0}
    narrative = build_system_narrative("ndvi", "NDVI", "Vegetation Indices", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "no usable pixel statistics" in narrative["executive_summary"]
