"""Unit tests for report_fixed_text.py - the code-controlled Carbon Project
Relevance/Limitations/Methodology/Data & Processing/Data Quality content that
must never be built or altered by AI or user input."""
from __future__ import annotations

import pytest

from app.domain.analysis_catalog import REAL_ANALYSIS_IDS
from app.services.report_fixed_text import (
    _AUTHORED_LIMITATIONS,
    _REUSE_NOTE_AS_LIMITATIONS,
    CARBON_RELEVANCE,
    METHODOLOGY_FALLBACK,
    carbon_project_relevance,
    data_processing_text,
    data_quality_text,
    limitations_text,
    methodology_dict,
    methodology_text,
)

_VNV_IDS = frozenset(
    {"vnv_ndfi"} | {
        f"vnv_{name}" for name in (
            "ndvi", "evi", "savi", "ndwi", "mndwi", "ndmi",
            "nbr", "bsi", "ndbi", "arvi", "gndvi", "psri",
        )
    }
)


@pytest.mark.parametrize("analysis_id", sorted(REAL_ANALYSIS_IDS))
def test_every_real_analysis_id_has_a_carbon_relevance_blurb(analysis_id):
    assert carbon_project_relevance(analysis_id).strip()
    assert analysis_id in CARBON_RELEVANCE


@pytest.mark.parametrize("analysis_id", sorted(REAL_ANALYSIS_IDS))
def test_carbon_relevance_always_carries_the_descriptive_only_caveat(analysis_id):
    """A fixed literal, never composed from any per-call input - every one of
    the 13 blurbs must still carry the same "not a ... determination" caveat
    the rest of the report uses, so this section can never read as an
    eligibility/carbon claim on its own."""
    text = carbon_project_relevance(analysis_id).lower()
    assert "determin" in text  # covers both "determination" and "does not determine"


def test_reuse_and_authored_limitations_partition_every_real_id_exactly_once():
    """Every real id is in exactly one of the two limitations sources - no id
    silently falls through to `_NO_LIMITATIONS_RECORDED`."""
    covered = _REUSE_NOTE_AS_LIMITATIONS | set(_AUTHORED_LIMITATIONS)
    assert covered == REAL_ANALYSIS_IDS
    assert not (_REUSE_NOTE_AS_LIMITATIONS & set(_AUTHORED_LIMITATIONS))


def test_limitations_reuses_the_real_hansen_note_verbatim():
    note = "Gain is a whole-period 2000-2012 figure only."
    assert limitations_text("hansen_gfc", note) == note


def test_limitations_reuses_the_real_esa_worldcover_note_verbatim():
    note = "Single 2021 snapshot, not a time series."
    assert limitations_text("esa_worldcover", note) == note


def test_limitations_falls_back_to_a_message_when_a_reuse_id_has_no_note():
    assert "No dataset-specific limitations" in limitations_text("s2_browse", None)


def test_limitations_uses_authored_text_for_dynamic_world_and_vegetation_indices():
    """These ids' real `note` is methodology prose, not a caveat - passing a
    methodology-shaped note through must NOT surface it here; the authored
    text is used regardless of what `note` says."""
    prose_note = "Boundary-mean of a cloud-masked, pre-monsoon Sentinel-2 composite per year."
    text = limitations_text("ndvi", prose_note)
    assert text != prose_note
    assert text == _AUTHORED_LIMITATIONS["ndvi"]


@pytest.mark.parametrize("analysis_id", sorted(REAL_ANALYSIS_IDS))
def test_methodology_and_data_processing_text_never_empty_with_no_methodology_dict(analysis_id):
    """Every real id must produce non-empty text for both sections even when
    `stats["methodology"]` is entirely absent (legacy row / an id with no
    real methodology dict) - falls back to `METHODOLOGY_FALLBACK` or `{}`,
    never crashes, never renders blank."""
    assert methodology_text(analysis_id, "A description.", None).strip()
    assert data_processing_text(analysis_id, None).strip()


def test_methodology_text_includes_the_formula_for_a_vegetation_index():
    methodology = {
        "imagery_source": "Sentinel-2", "cloud_masking": "Cloud Score+",
        "season_window": "02-01 to 05-31", "years_computed": [2026],
        "formula": "(NIR - RED) / (NIR + RED)", "valid_range": [-1.0, 1.0],
    }
    text = methodology_text("ndvi", "NDVI description.", methodology)
    assert "(NIR - RED) / (NIR + RED)" in text
    assert "-1.0 to 1.0" in text


def test_data_processing_text_includes_imagery_source_and_season_window():
    methodology = {
        "imagery_source": "Sentinel-2", "cloud_masking": "Cloud Score+",
        "season_window": "02-01 to 05-31", "years_computed": [2026],
        "formula": "x", "valid_range": [-1.0, 1.0],
    }
    text = data_processing_text("ndvi", methodology)
    assert "Sentinel-2" in text
    assert "Cloud Score+" in text
    assert "02-01 to 05-31" in text
    # Methodology-section-only fields must not leak into Data & Processing.
    assert "(NIR" not in text


def test_methodology_fallback_covers_every_id_with_no_real_methodology_dict():
    for analysis_id in (
        "hansen_gfc", "dynamic_world", "esa_worldcover",
        "s2_browse", "s1_browse", "landsat_browse",
    ):
        assert analysis_id in METHODOLOGY_FALLBACK
        assert "dataset" in METHODOLOGY_FALLBACK[analysis_id]


def test_data_quality_text_reports_coverage_masking_and_out_of_range_pixels():
    text = data_quality_text(98.5, out_of_range_pixel_count=42, has_cloud_masking=True)
    assert "98.5%" in text
    assert "42" in text
    assert "masked" in text.lower()


def test_data_quality_text_handles_missing_coverage_gracefully():
    text = data_quality_text(None)
    assert text.strip()
    assert "98" not in text


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 review, M1: the cloud-masking sentence must not be gated
# behind coverage_pct being known - vnv_ndfi's real stats dict has no
# coverage_pct key at all, but genuinely does get SCL-based masking applied
# upstream (cdse_ingestion.py), and METHODOLOGY_FALLBACK["vnv_ndfi"] does
# carry a cloud_masking entry - the disclosure must still render.
# --------------------------------------------------------------------------


def test_data_quality_text_discloses_cloud_masking_even_with_unknown_coverage():
    text = data_quality_text(None, has_cloud_masking=True)
    assert "masked before computing statistics" in text
    assert "not recorded" in text.lower() or "not determined" in text.lower()


def test_data_quality_text_missing_coverage_no_longer_uses_the_old_dead_end_string():
    """The old "Coverage could not be determined for this analysis." early
    return unconditionally discarded masking/out-of-range info too - this
    fix replaces it with a sentence that still allows the other two facts to
    follow it."""
    text = data_quality_text(None)
    assert text != "Coverage could not be determined for this analysis."


# --------------------------------------------------------------------------
# carbon-mrv-vm0047 report-generation fix: every VNV entry in
# METHODOLOGY_FALLBACK must disclose real SCL-based cloud masking, and
# methodology_dict (the public wrapper report_content.py's has_cloud_masking
# now resolves through) must actually surface it.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("analysis_id", sorted(_VNV_IDS))
def test_every_vnv_methodology_fallback_entry_discloses_cloud_masking(analysis_id):
    assert analysis_id in METHODOLOGY_FALLBACK
    m = METHODOLOGY_FALLBACK[analysis_id]
    assert "cloud_masking" in m
    assert m["cloud_masking"].strip()
    # Names the real excluded SCL classes (cdse_ingestion.py's
    # _INVALID_SCL_CLASSES), not a vague restatement.
    assert "cloud shadow" in m["cloud_masking"].lower()
    assert "cirrus" in m["cloud_masking"].lower()


@pytest.mark.parametrize("analysis_id", sorted(_VNV_IDS))
def test_vnv_cloud_masking_text_names_every_excluded_and_retained_scl_class_by_id(analysis_id):
    """carbon-mrv-vm0047 review, M2: an auditor must be able to verify
    exactly which SCL classes are meant (names alone are ambiguous across
    ESA product baselines) - every excluded id (0,1,3,8,9,10) and every
    retained id (2,4,5,6,7,11) must appear by number, matching
    cdse_ingestion.py's _INVALID_SCL_CLASSES exactly. Class 7 (unclassified)
    in particular was previously undisclosed as retained."""
    text = METHODOLOGY_FALLBACK[analysis_id]["cloud_masking"]
    for excluded_id in (
        "SCL 0", "1 (saturated", "3 (cloud shadow", "8 (cloud medium", "9 (cloud high", "10 (thin cirrus",
    ):
        assert excluded_id in text
    for retained_id in (
        "SCL 2", "4 (vegetation", "5 (bare soil", "6 (water", "7 (unclassified", "11 (snow",
    ):
        assert retained_id in text


def test_methodology_dict_resolves_the_fallback_for_a_vnv_id_with_no_real_methodology():
    m = methodology_dict("vnv_ndvi", None)
    assert "cloud_masking" in m


def test_methodology_dict_prefers_a_real_methodology_dict_over_the_fallback():
    real = {"dataset": "real dataset", "cloud_masking": "real masking text"}
    assert methodology_dict("vnv_ndvi", real) == real


def test_data_processing_text_discloses_cloud_masking_for_every_vnv_id():
    """Before this fix, NO vnv_* id had a `cloud_masking` fallback entry at
    all, so this sentence never appeared for any of them."""
    for analysis_id in sorted(_VNV_IDS):
        text = data_processing_text(analysis_id, None)
        assert "Cloud masking:" in text
