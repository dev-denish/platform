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


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 report-generation fix: VNV Pipeline band-index results
# (real shape from vnv_analysis_jobs.py's run_vnv_band_index_analysis) must
# NOT fall through to _browse_narrative's "single raw scene" wording - that
# shape describes a genuinely single-scene browse id, not a per-pixel,
# multi-scene band-math composite.
# --------------------------------------------------------------------------


def _vnv_band_index_stats(**overrides):
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


def test_vnv_band_index_narrative_does_not_claim_a_single_raw_scene():
    stats = _vnv_band_index_stats()
    narrative = build_system_narrative("vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "single raw scene" not in narrative["executive_summary"]
    assert "single raw scene" not in narrative["spatial_distribution"]
    assert "temporal_analysis" not in narrative
    assert "change_analysis" not in narrative


def test_vnv_band_index_narrative_reflects_the_real_mean_and_scene_count():
    stats = _vnv_band_index_stats()
    narrative = build_system_narrative("vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats)
    assert "0.55" in narrative["executive_summary"]
    assert "3 scene(s)" in narrative["executive_summary"]


def test_vnv_band_index_narrative_handles_no_usable_pixels():
    stats = _vnv_band_index_stats(
        mean=None, distribution={"latest": {"mean": None, "std_dev": None, "min": None, "max": None}},
    )
    narrative = build_system_narrative("vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "no usable pixel statistics" in narrative["executive_summary"]


def test_vnv_band_index_narrative_works_for_every_real_vnv_index_id():
    """All 12 vnv_* band-index ids (vnv_ndfi excluded - a different stats
    shape entirely, see its own module docstring in vnv_analysis_jobs.py)
    must resolve to a real index_summary.py profile once the vnv_ prefix is
    stripped."""
    for name in (
        "ndvi", "evi", "savi", "ndwi", "mndwi", "ndmi",
        "nbr", "bsi", "ndbi", "arvi", "gndvi", "psri",
    ):
        analysis_id = f"vnv_{name}"
        stats = _vnv_band_index_stats(index=analysis_id)
        narrative = build_system_narrative(
            analysis_id, f"{name.upper()} — Band Math", "VM0047 Compute — Band Indices", stats
        )
        assert set(narrative) >= _ALWAYS_KEYS


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 review (M3): VNV's PSRI uses a genuinely different band
# (NIR) than GEE's PSRI (red-edge B6) - describe_level("psri", ...) still
# pulls the red-edge-anchored Merzlyak et al. (1999) reading text, so this
# must be qualified rather than presented as directly comparable.
# --------------------------------------------------------------------------


def test_vnv_psri_narrative_is_qualified_as_not_comparable_to_gee_psri():
    stats = _vnv_band_index_stats(index="vnv_psri")
    narrative = build_system_narrative(
        "vnv_psri", "PSRI — Band Math", "VM0047 Compute — Band Indices", stats
    )
    assert "not comparable with the Earth Engine PSRI" in narrative["executive_summary"]
    assert "not comparable with the Earth Engine PSRI" in narrative["key_findings"]


def test_vnv_non_psri_narrative_has_no_psri_qualifier():
    stats = _vnv_band_index_stats(index="vnv_ndvi")
    narrative = build_system_narrative(
        "vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats
    )
    assert "not comparable with the Earth Engine PSRI" not in narrative["executive_summary"]


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 review (M4): the executive summary/key findings must
# disclose the real sample size/coverage behind the mean, not just the mean
# itself - a mean resting on a thin sliver of the boundary must not read the
# same as one resting on the whole thing.
# --------------------------------------------------------------------------


def test_vnv_band_index_narrative_discloses_a_thin_sample():
    stats = _vnv_band_index_stats(valid_pixel_count=42, total_pixel_count=4200, coverage_pct=1.0)
    narrative = build_system_narrative(
        "vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats
    )
    assert "42" in narrative["executive_summary"]
    assert "too thin a sample" in narrative["executive_summary"]
    assert "too thin a sample" in narrative["key_findings"]


def test_vnv_band_index_narrative_discloses_a_modest_sample():
    stats = _vnv_band_index_stats(valid_pixel_count=500, total_pixel_count=10000, coverage_pct=5.0)
    narrative = build_system_narrative(
        "vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats
    )
    assert "500" in narrative["executive_summary"]
    assert "fine for a visual read, thin for any claim" in narrative["executive_summary"]


def test_vnv_band_index_narrative_ample_sample_has_no_thin_sample_caveat():
    stats = _vnv_band_index_stats(valid_pixel_count=9000, total_pixel_count=10000, coverage_pct=90.0)
    narrative = build_system_narrative(
        "vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats
    )
    assert "9,000" in narrative["executive_summary"]
    assert "too thin a sample" not in narrative["executive_summary"]
    assert "fine for a visual read" not in narrative["executive_summary"]


def test_vnv_band_index_narrative_discloses_zero_valid_pixels():
    stats = _vnv_band_index_stats(valid_pixel_count=0, coverage_pct=0.0)
    narrative = build_system_narrative(
        "vnv_ndvi", "NDVI — Band Math", "VM0047 Compute — Band Indices", stats
    )
    assert "No valid pixels remained" in narrative["executive_summary"]


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 review (S7): "index" in stats is presumed unique to
# run_vnv_band_index_analysis's own stats dict, but a future NDFI sidecar
# response could coincidentally add its own "index" key - get_profile would
# then raise ValueError on an id like "ndfi" (no such profile exists), which
# must degrade gracefully rather than crash report generation entirely.
# --------------------------------------------------------------------------


def test_vnv_band_index_narrative_degrades_gracefully_for_an_unknown_index_id():
    stats = {"index": "vnv_not_a_real_index", "distribution": {"latest": {"mean": 0.5}}}
    narrative = build_system_narrative(
        "vnv_not_a_real_index", "Not A Real Index", "VM0047 Compute — Band Indices", stats
    )
    assert set(narrative) >= _ALWAYS_KEYS
    assert "No system narrative is available" in narrative["executive_summary"]


def test_unrecognized_stats_shape_gets_a_neutral_narrative_not_a_wrong_one():
    """_browse_narrative is now gated on `scene_date` - a stats shape that
    matches nothing (e.g. vnv_ndfi's real flat min/max/mean/valid_pixel_count/
    total_pixel_count/masked_fraction shape, which has no scene_date and no
    index key) must get a neutral message, not be silently mislabeled "a
    single raw scene"."""
    stats = {
        "min": 0.0, "max": 1.0, "mean": 0.02, "valid_pixel_count": 10,
        "total_pixel_count": 4200, "masked_fraction": 0.9976,
    }
    narrative = build_system_narrative("vnv_ndfi", "NDFI — Spectral Unmixing", "VM0047 Compute — ForesToolboxRS", stats)
    assert set(narrative) >= _ALWAYS_KEYS
    assert "single raw scene" not in narrative["executive_summary"]
    assert "No system narrative is available" in narrative["executive_summary"]


def test_browse_narrative_still_fires_for_a_real_scene_date_shape():
    """Positive gating on `_browse_narrative` must not regress the real
    browse ids (s2_browse/s1_browse/landsat_browse) it was written for."""
    stats = {"scene_date": "2026-03-01", "cloud_pct": 2.5, "coverage_pct": 99.0}
    narrative = build_system_narrative("s1_browse", "Sentinel-1 Radar", "Raw Imagery", stats)
    assert "single raw scene" in narrative["spatial_distribution"]
