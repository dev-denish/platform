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
    methodology_text,
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
