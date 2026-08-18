"""Pure-logic tests for app/services/index_summary.py's clause generators
(Wave: enriched vegetation-index results) - no ee, no DB, no LLM, no network.
Every function under test takes plain floats/dicts/lists and returns a plain
string or None, so every assertion below is EXACT text, not "is not None":
a wording change (deliberate or accidental) in a VVB-facing sentence should
fail a test here, not surface for the first time in a report review.

Inputs are hand-picked so the arithmetic each clause does (band selection,
mean formatting, bin-centre averaging, tail-gap comparison, endpoint delta)
can be checked by a human reading this file, not just round-tripped through
the function under test."""
from __future__ import annotations

import pytest

from app.services.index_summary import (
    DESCRIPTIVE_ONLY_TRAILER,
    INDEX_PROFILES,
    compose_summary,
    describe_level,
    describe_out_of_range,
    describe_sample,
    describe_spatial_outliers,
    describe_trend,
    describe_variability,
    get_profile,
    summarize_index_result,
)

# A realistic canned histogram: 20 bins, 0.1 wide, spanning the fixed [-1, 1]
# range _index_stats_reducer() always uses (see gee_analysis_service.py) -
# shared by every outliers/sample test below so the bin-edge arithmetic only
# has to be worked out once.
_EDGES = [round(-1.0 + 0.1 * i, 10) for i in range(21)]  # 21 edges, 20 bins


# ------------------------------------------------------------- get_profile


def test_get_profile_raises_value_error_for_unknown_index_id():
    with pytest.raises(ValueError, match="bogus"):
        get_profile("bogus")


def test_index_profiles_keyed_by_all_fifteen_index_ids():
    assert set(INDEX_PROFILES) == {
        "ndvi", "evi", "savi", "mndwi", "nbr", "ndwi", "gndvi", "ndbi",
        "arvi", "ndmi", "lswi", "bsi", "nddi", "cmri", "psri",
    }


def test_ndvi_uses_the_usgs_descriptive_bands_not_mndwis_water_threshold():
    assert INDEX_PROFILES["ndvi"].edges == (0.1, 0.2, 0.5)


def test_mndwi_uses_the_xu_2006_open_water_threshold_not_ndvis_bands():
    assert INDEX_PROFILES["mndwi"].edges == (0.0,)


def test_ndwi_uses_the_mcfeeters_1996_open_water_threshold_distinct_from_mndwis():
    # Same 0.0 split as MNDWI (both are literature open-water cut points) but
    # a genuinely different formula/paper - proves the two water profiles
    # were NOT accidentally aliased to the same object.
    assert INDEX_PROFILES["ndwi"].edges == (0.0,)
    assert INDEX_PROFILES["ndwi"].edge_source != INDEX_PROFILES["mndwi"].edge_source


def test_ndbi_uses_the_zha_2003_built_up_threshold_not_ndvis_bands():
    assert INDEX_PROFILES["ndbi"].edges == (0.0,)


def test_gndvi_reuses_ndvis_bands_like_evi_and_savi_do():
    assert INDEX_PROFILES["gndvi"].edges == (0.1, 0.2, 0.5)
    assert INDEX_PROFILES["gndvi"].caveat is not None


def test_arvi_reuses_ndvis_bands_with_a_similar_dynamic_range_caveat():
    assert INDEX_PROFILES["arvi"].edges == (0.1, 0.2, 0.5)
    assert INDEX_PROFILES["arvi"].caveat is not None


def test_ndmi_and_lswi_share_ndvis_edges_but_have_distinct_moisture_vs_flood_wording():
    # LSWI is mathematically identical to NDMI (same band pair) - proves the two
    # profiles carry genuinely different reading text, not the same object reused.
    assert INDEX_PROFILES["ndmi"].edges == (0.1, 0.2, 0.5)
    assert INDEX_PROFILES["lswi"].edges == (0.1, 0.2, 0.5)
    assert INDEX_PROFILES["ndmi"].readings != INDEX_PROFILES["lswi"].readings


def test_bsi_has_no_literature_threshold_and_says_so_in_its_own_caveat():
    assert INDEX_PROFILES["bsi"].edges == (0.0,)
    assert "no literature-standard" in INDEX_PROFILES["bsi"].caveat


def test_nddi_uses_gu_2007s_published_three_class_drought_classification():
    assert INDEX_PROFILES["nddi"].edges == (0.1, 0.3)


def test_cmri_has_no_universal_threshold_and_flags_its_wider_than_shared_range():
    assert INDEX_PROFILES["cmri"].edges == (0.0,)
    assert "[-2, 2]" in INDEX_PROFILES["cmri"].caveat
    assert "its own [-2, 2] range" in INDEX_PROFILES["cmri"].caveat


def test_psri_has_no_threshold_at_all_and_uses_an_empty_edges_tuple():
    # Unlike every other profile, PSRI has NO published numeric threshold
    # of any kind (not even BSI's inconsistent study-specific ranges) - the
    # empty edges tuple means every mean gets the same flat, one-band
    # reading, never an adjective implying a validated cut point.
    assert INDEX_PROFILES["psri"].edges == ()
    assert len(INDEX_PROFILES["psri"].words) == 1
    assert len(INDEX_PROFILES["psri"].readings) == 1


# ------------------------------------------------------------- describe_level


def test_describe_level_none_mean_returns_none():
    assert describe_level("ndvi", None) is None


def test_describe_level_ndvi_high_mean_exact_text():
    assert describe_level("ndvi", 0.65) == (
        "NDVI averages 0.65 across the boundary - high, consistent with dense green canopy."
    )


def test_describe_level_evi_reuses_ndvi_bands_but_appends_its_own_caveat():
    # Same band (0.65 -> "high") as NDVI, but EVI's profile carries a caveat
    # NDVI's does not - proves the caveat is appended per-profile, not global.
    assert describe_level("evi", 0.65) == (
        "EVI averages 0.65 across the boundary - high, consistent with dense green canopy. "
        "EVI reads lower than NDVI over identical vegetation, so this wording is deliberately "
        "conservative and is not comparable word-for-word with NDVI's."
    )


def test_describe_level_mndwi_below_xu_threshold_reads_as_land():
    # -0.30 is below the single Xu-2006 cut point (0.0) - band 0.
    assert describe_level("mndwi", -0.30) == (
        "MNDWI averages -0.30 across the boundary - below the 0 open-water cut point (Xu 2006), "
        "consistent with land - vegetation, soil or built-up surface - rather than open water."
    )


def test_describe_level_mndwi_mean_crossing_zero_reads_as_open_water():
    # 0.05 sits on the open-water side of the Xu-2006 split (>= 0.0) - band 1,
    # the "check the boundary" wording, not the land wording above. This is
    # the real mean-crosses-the-literature-threshold case, distinct from
    # NDVI/EVI/SAVI/NBR's purely descriptive band edges.
    assert describe_level("mndwi", 0.05) == (
        "MNDWI averages 0.05 across the boundary - above the 0 open-water cut point (Xu 2006), "
        "the boundary mean itself reads as open water, which for a land-based project boundary "
        "usually means a tank, reservoir or river covers a large share of the area - check the "
        "boundary before using the mean."
    )


def test_describe_level_ndwi_below_mcfeeters_threshold_reads_as_land():
    assert describe_level("ndwi", -0.30) == (
        "NDWI averages -0.30 across the boundary - below the 0 open-water cut point "
        "(McFeeters 1996), consistent with land - vegetation, soil or built-up surface - "
        "rather than open water."
    )


def test_describe_level_ndwi_mean_crossing_zero_reads_as_open_water():
    assert describe_level("ndwi", 0.05) == (
        "NDWI averages 0.05 across the boundary - above the 0 open-water cut point "
        "(McFeeters 1996), the boundary mean itself reads as open water, which for a "
        "land-based project boundary usually means a tank, reservoir or river covers a "
        "large share of the area - check the boundary before using the mean."
    )


def test_describe_level_ndbi_below_zha_threshold_reads_as_non_built_up():
    assert describe_level("ndbi", -0.30) == (
        "NDBI averages -0.30 across the boundary - below the 0 built-up cut point (Zha et "
        "al. 2003), consistent with vegetation, soil or water rather than built-up surface."
    )


def test_describe_level_ndbi_mean_crossing_zero_reads_as_built_up():
    assert describe_level("ndbi", 0.05) == (
        "NDBI averages 0.05 across the boundary - above the 0 built-up cut point (Zha et "
        "al. 2003), the boundary mean itself reads as built-up surface, which for a "
        "land-based project boundary is worth checking - a real settlement/infrastructure "
        "inside the boundary, or a misdrawn boundary edge."
    )


def test_describe_level_gndvi_reuses_ndvi_bands_but_appends_its_own_caveat():
    assert describe_level("gndvi", 0.65) == (
        "GNDVI averages 0.65 across the boundary - high, consistent with dense green "
        "canopy. GNDVI substitutes the green band for red and is more sensitive to "
        "chlorophyll concentration than NDVI over identical vegetation, so this wording is "
        "not comparable word-for-word with NDVI's."
    )


def test_describe_level_arvi_reuses_ndvi_bands_but_appends_its_own_caveat():
    assert describe_level("arvi", 0.65) == (
        "ARVI averages 0.65 across the boundary - high, consistent with dense green "
        "canopy. ARVI is NDVI corrected for atmospheric (aerosol/haze) scattering using "
        "the blue band, not a differently-scaled signal - readings are broadly comparable "
        "to NDVI's, but the two can diverge under real haze/aerosol conditions, which is "
        "the point of the correction."
    )


def test_describe_level_ndmi_high_band_uses_moisture_specific_wording():
    assert describe_level("ndmi", 0.65) == (
        "NDMI averages 0.65 across the boundary - high, consistent with high canopy "
        "moisture content - dense, well-watered vegetation. NDMI measures canopy/soil "
        "moisture (NIR vs. SWIR1 reflectance), not chlorophyll or biomass - a high "
        "reading here is not the same claim as high NDVI, and this wording is not "
        "comparable word-for-word with NDVI's."
    )


def test_describe_level_lswi_high_band_uses_flood_specific_wording_not_ndmis():
    # Same 0.65 input as the NDMI test above, but LSWI's reading text is about
    # standing water/saturation, not canopy moisture - proves the two profiles
    # diverge in wording despite sharing identical band math.
    assert describe_level("lswi", 0.65) == (
        "LSWI averages 0.65 across the boundary - high, consistent with high surface "
        "water content - standing water or saturated ground. LSWI's published "
        "flood-detection use compares LSWI against EVI/NDVI on the same pixel (LSWI + T "
        ">= EVI/NDVI), not an absolute LSWI value alone - this wording describes the raw "
        "value only, not a flood/no-flood classification."
    )


def test_describe_level_bsi_below_zero_reads_as_vegetated_not_bare_soil():
    assert describe_level("bsi", -0.30) == (
        "BSI averages -0.30 across the boundary - below the ratio's zero sign boundary "
        "(no literature-standard bare-soil threshold exists), consistent with vegetated "
        "or moist surface rather than exposed bare soil. BSI has no literature-standard "
        "absolute threshold the way MNDWI/NDWI/NDBI do - the reading above only reports "
        "which side of zero the ratio falls, not a validated bare-soil classification. "
        "'Degraded land' and 'recently cleared/tilled' name plausible causes of a "
        "soil-leaning spectral signature, not a degradation, clearing or land-use-change "
        "determination - VM0047 degradation/reversal findings run through the monitored "
        "disturbance record and plot data, not a single-date spectral mean."
    )


def test_describe_level_bsi_above_zero_reads_as_bare_soil():
    assert describe_level("bsi", 0.05) == (
        "BSI averages 0.05 across the boundary - above the ratio's zero sign boundary "
        "(no literature-standard bare-soil threshold exists), consistent with exposed "
        "bare soil, degraded land, or a recently cleared/tilled surface rather than dense "
        "vegetation. BSI has no literature-standard absolute threshold the way MNDWI/NDWI/"
        "NDBI do - the reading above only reports which side of zero the ratio falls, not "
        "a validated bare-soil classification. 'Degraded land' and 'recently cleared/"
        "tilled' name plausible causes of a soil-leaning spectral signature, not a "
        "degradation, clearing or land-use-change determination - VM0047 degradation/"
        "reversal findings run through the monitored disturbance record and plot data, "
        "not a single-date spectral mean."
    )


def test_describe_level_nddi_below_point_one_reads_as_non_drought():
    assert describe_level("nddi", 0.05) == (
        "NDDI averages 0.05 across the boundary - below the 0.1 non-drought cut point "
        "(Gu et al. 2007), consistent with a non-drought vegetation-water balance. NDDI "
        "here uses Gao (1996)'s NIR-SWIR1 NDWI (this platform's NDMI, mathematically "
        "identical), the same input Gu et al. used - not McFeeters' Green-NIR NDWI. The "
        "published 0.1/0.3 classification was calibrated on MODIS grassland scenes, not "
        "Sentinel-2 forestry/agroforestry boundaries, so the class label is indicative, "
        "not re-validated for this platform's use case. This class label describes the "
        "spectral vegetation-water balance only, not a VM0047 degradation, reversal or "
        "disturbance finding - those run through the monitored disturbance record and "
        "plot-based data (s9.2), not a single-date spectral snapshot."
    )


def test_describe_level_nddi_middle_band_reads_as_moderate_drought():
    assert describe_level("nddi", 0.20) == (
        "NDDI averages 0.20 across the boundary - in the 0.1-0.3 moderate-drought band "
        "(Gu et al. 2007), consistent with moderate drought stress relative to the local "
        "vegetation-water balance. NDDI here uses Gao (1996)'s NIR-SWIR1 NDWI (this "
        "platform's NDMI, mathematically identical), the same input Gu et al. used - not "
        "McFeeters' Green-NIR NDWI. The published 0.1/0.3 classification was calibrated "
        "on MODIS grassland scenes, not Sentinel-2 forestry/agroforestry boundaries, so "
        "the class label is indicative, not re-validated for this platform's use case. "
        "This class label describes the spectral vegetation-water balance only, not a "
        "VM0047 degradation, reversal or disturbance finding - those run through the "
        "monitored disturbance record and plot-based data (s9.2), not a single-date "
        "spectral snapshot."
    )


def test_describe_level_nddi_top_band_reads_as_severe_drought():
    assert describe_level("nddi", 0.35) == (
        "NDDI averages 0.35 across the boundary - at or above the 0.3 severe-drought cut "
        "point (Gu et al. 2007), consistent with severe drought stress relative to the "
        "local vegetation-water balance. NDDI here uses Gao (1996)'s NIR-SWIR1 NDWI "
        "(this platform's NDMI, mathematically identical), the same input Gu et al. used "
        "- not McFeeters' Green-NIR NDWI. The published 0.1/0.3 classification was "
        "calibrated on MODIS grassland scenes, not Sentinel-2 forestry/agroforestry "
        "boundaries, so the class label is indicative, not re-validated for this "
        "platform's use case. This class label describes the spectral vegetation-water "
        "balance only, not a VM0047 degradation, reversal or disturbance finding - those "
        "run through the monitored disturbance record and plot-based data (s9.2), not a "
        "single-date spectral snapshot."
    )


def test_describe_level_cmri_below_zero_reads_as_water_like():
    assert describe_level("cmri", -0.30) == (
        "CMRI averages -0.30 across the boundary - below the ratio's zero sign boundary "
        "(no universal validated mangrove-masking threshold exists), leaning water-like "
        "rather than vegetation-like in this NDVI-vs-NDWI comparison. CMRI = NDVI - NDWI "
        "is a plain difference, not a ratio, so its true range is [-2, 2], wider than "
        "the [-1, 1] every other index here shares - CMRI is computed and masked on its "
        "own [-2, 2] range (not the shared [-1, 1]) for exactly this reason: live "
        "verification showed ordinary vegetated (non-water) boundaries commonly land "
        "above 1.0, so a shared [-1, 1] mask would exclude most pixels routinely, not "
        "rarely. This is not a mangrove/non-mangrove classification - no validated "
        "universal threshold exists."
    )


def test_describe_level_cmri_above_zero_reads_as_vegetation_like():
    assert describe_level("cmri", 0.05) == (
        "CMRI averages 0.05 across the boundary - above the ratio's zero sign boundary "
        "(no universal validated mangrove-masking threshold exists), leaning "
        "vegetation-like rather than water-like in this NDVI-vs-NDWI comparison, the "
        "direction associated with mangrove/dense-vegetation pixels in Gupta et al. "
        "(2018). CMRI = NDVI - NDWI is a plain difference, not a ratio, so its true "
        "range is [-2, 2], wider than the [-1, 1] every other index here shares - CMRI "
        "is computed and masked on its own [-2, 2] range (not the shared [-1, 1]) for "
        "exactly this reason: live verification showed ordinary vegetated (non-water) "
        "boundaries commonly land above 1.0, so a shared [-1, 1] mask would exclude "
        "most pixels routinely, not rarely. This is not a mangrove/non-mangrove "
        "classification - no validated universal threshold exists."
    )


def test_describe_level_psri_exact_text():
    assert describe_level("psri", 0.08) == (
        "PSRI averages 0.08 across the boundary - not benchmarked against a literature "
        "threshold, the raw carotenoid-to-chlorophyll reflectance ratio - higher is "
        "consistent with more senescent or ripening vegetation, lower (or negative) with "
        "actively growing green vegetation (Merzlyak et al. 1999), though no published "
        "cut point marks the boundary between them. PSRI has no literature-standard "
        "threshold at all - only a direction is established, so no adjective band is "
        "assigned here, just the direction and the raw number. PSRI's natural range is "
        "also much narrower than every other index here (typically a fraction of "
        "[-1, 1]), so small absolute changes can be a larger relative shift than the "
        "same change would be for NDVI. 'Senescence' and 'ripening' name plausible "
        "physiological readings of a rising carotenoid:chlorophyll ratio, not a VM0047 "
        "degradation or reversal determination - those run through the monitored "
        "disturbance record and plot-based data (s9.2), not a single-date spectral mean."
    )


def test_describe_level_psri_same_band_regardless_of_sign_or_magnitude():
    # edges=() means EVERY mean lands in band 0 - proves this isn't
    # accidentally working only for the one value tested above.
    low = describe_level("psri", -0.20)
    high = describe_level("psri", 0.45)
    assert low.startswith(
        "PSRI averages -0.20 across the boundary - not benchmarked against a literature "
        "threshold, the raw carotenoid-to-chlorophyll reflectance ratio"
    )
    assert high.startswith(
        "PSRI averages 0.45 across the boundary - not benchmarked against a literature "
        "threshold, the raw carotenoid-to-chlorophyll reflectance ratio"
    )


def test_describe_level_nbr_low_band_wording_is_burn_specific_not_green_specific():
    # NBR reuses NDVI's edges but NOT its readings - low NBR is burn/bare/built,
    # not "sparse vegetation".
    assert describe_level("nbr", 0.05) == (
        "NBR averages 0.05 across the boundary - very low, high SWIR relative to NIR - "
        "consistent with recently burned, bare or built-up surface. Burn-severity classes "
        "(Key & Benson 2006, FIREMON) are defined on dNBR - a pre-fire minus post-fire "
        "difference - not on the single-date NBR shown here, so no severity class is asserted."
    )


# ------------------------------------------------------- describe_variability


def test_describe_variability_none_returns_none():
    assert describe_variability(None) is None


def test_describe_variability_uniform_high_mean_case():
    # std_dev 0.02 < half a 0.1 bin (0.05) -> "uniform" band.
    assert describe_variability(0.02) == (
        "Pixel values are uniform across the boundary (a spread of about 0.02), so the mean "
        "is a fair description of the whole area."
    )


def test_describe_variability_fairly_uniform_band():
    assert describe_variability(0.07) == (
        "Pixel values are fairly uniform across the boundary (a spread of about 0.07)."
    )


def test_describe_variability_moderate_band():
    assert describe_variability(0.15) == (
        "Pixel values are moderately variable across the boundary (a spread of about 0.15), "
        "so the boundary covers noticeably different surfaces."
    )


def test_describe_variability_wide_band():
    assert describe_variability(0.30) == (
        "Pixel values vary widely across the boundary (a spread of about 0.30), so the "
        "boundary mean averages over quite different surfaces and should not be read as one "
        "uniform condition."
    )


# --------------------------------------------------- describe_spatial_outliers


def _unimodal_counts() -> list[int]:
    """One real mode at bin index 14 (edge 0.4-0.5), tapering smoothly on
    both sides to 0 - a single cluster, no second mode, no thin tail. 20
    values for 20 bins."""
    return [0, 0, 0, 0, 0, 0, 0, 0, 1, 3, 10, 30, 80, 150, 220, 150, 80, 30, 10, 3]


def test_describe_spatial_outliers_none_when_missing_inputs():
    assert describe_spatial_outliers("ndvi", 0.1, 0.5, None, None) is None
    assert describe_spatial_outliers("ndvi", 0.1, 0.5, _EDGES, None) is None


def test_describe_spatial_outliers_single_unimodal_cluster_returns_none_not_filler_text():
    """THE MOST IMPORTANT ASSERTION IN THIS SUITE.

    A histogram that is one real cluster with min/max sitting right at the
    edge of where the bulk of the pixels actually are (no gap at all here)
    must NOT get a "worth checking" sentence bolted on. Forcing filler text
    when the data is genuinely uniform was explicitly disallowed by the task
    spec that added this clause - a summary that always finds something to
    flag trains the reader to ignore the flag. bin_edges[10]=0.0 and
    bin_edges[19]=0.9 are the true bulk span (see _bulk_span) for
    _unimodal_counts(), so passing them as min/max means "the extremes ARE
    the bulk", the strongest possible no-tail case."""
    counts = _unimodal_counts()
    result = describe_spatial_outliers("ndvi", 0.0, 0.9, _EDGES, counts)
    assert result is None


def test_describe_spatial_outliers_bimodal_case_exact_text():
    # Two clear peaks (bin 3, centre -0.65; bin 15, centre 0.55), a near-zero
    # valley between them, min/max chosen to sit INSIDE the two peak bins
    # (no tail) so only the multi-modal clause fires, nothing else.
    counts = [2, 2, 2, 100, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 80, 2, 2, 2, 2]
    result = describe_spatial_outliers("ndvi", -0.65, 0.65, _EDGES, counts)
    assert result == (
        "Values cluster in two or more separate groups (around -0.65 and 0.55), so the "
        "boundary contains two or more distinct surface types and the single NDVI mean is "
        "averaging across them."
    )


def test_describe_spatial_outliers_thin_low_tail_exact_text():
    # Same unimodal cluster as the "no filler" test above, but minimum is now
    # 3 bins (0.3) below where the bulk starts (0.0) - well past the
    # _TAIL_BIN_GAP=2-bin (0.2) threshold, so the low-tail clause must fire.
    counts = _unimodal_counts()
    result = describe_spatial_outliers("ndvi", -0.90, 0.90, _EDGES, counts)
    assert result == (
        "A thin low tail runs down to -0.90, well below the bulk of the pixels (which start "
        "around 0.00) - typically water, bare ground, cloud shadow or a sliver of neighbouring "
        "land caught by the boundary edge; worth checking before reading the mean as "
        "representative."
    )


# ------------------------------------------------------------- describe_sample


def test_describe_sample_none_counts_reads_as_no_coverage():
    assert describe_sample(None) == (
        "No in-range cloud-free pixels were available inside the boundary for this year, so "
        "there is nothing to describe - treat this year as missing, not as zero."
    )


def test_describe_sample_zero_total_reads_as_no_coverage():
    assert describe_sample([0, 0, 0]) == (
        "No in-range cloud-free pixels were available inside the boundary for this year, so "
        "there is nothing to describe - treat this year as missing, not as zero."
    )


def test_describe_sample_thin_sample_case_exact_text():
    # 50 pixels -> under _THIN_SAMPLE_PIXELS=100 -> 50 * 0.01 ha/pixel = 0.50 ha.
    assert describe_sample([50]) == (
        "Only 50 valid pixels (about 0.50 ha at 10 m) survived cloud masking inside the "
        "boundary, so every number above is indicative only - too thin a sample to carry into "
        "any reporting."
    )


def test_describe_sample_modest_sample_case_exact_text():
    # 500 pixels -> between 100 and _MODEST_SAMPLE_PIXELS=1000 -> 5.0 ha.
    assert describe_sample([500]) == (
        "The sample is small (500 valid pixels, about 5.0 ha at 10 m after cloud masking) - "
        "fine for a visual read, thin for a trend claim."
    )


def test_describe_sample_ample_sample_returns_none():
    assert describe_sample([2000]) is None


# ------------------------------------------------------- describe_out_of_range


def test_describe_out_of_range_none_counts_returns_none():
    assert describe_out_of_range(None, 100) is None


def test_describe_out_of_range_none_out_of_range_count_returns_none():
    assert describe_out_of_range([500], None) is None


def test_describe_out_of_range_zero_out_of_range_count_returns_none():
    assert describe_out_of_range([500], 0) is None


def test_describe_out_of_range_below_warn_threshold_returns_none():
    # 50 of 950 (~5.3%) - under the 10% trigger, stays silent.
    assert describe_out_of_range([900], 50) is None


def test_describe_out_of_range_at_warn_threshold_exact_text():
    # 100 of 1000 (10%) - exactly at the trigger, fires the warn wording.
    assert describe_out_of_range([900], 100) == (
        "100 of 1000 pixels (10%) fell outside this index's natural value range and were "
        "excluded from the numbers above - worth noting, not yet enough to distrust the "
        "mean."
    )


def test_describe_out_of_range_at_severe_threshold_exact_text():
    # 300 of 1000 (30%) - exactly at the severe trigger, not the warn band.
    assert describe_out_of_range([700], 300) == (
        "300 of 1000 pixels (30%) fell outside this index's natural value range and were "
        "excluded from the numbers above - a large enough share that the mean may not "
        "represent the whole boundary; worth confirming against the excluded-pixel count "
        "before relying on it."
    )


def test_describe_out_of_range_stays_silent_below_the_thin_sample_floor():
    # 30 of 90 (33%, would be "severe" on its own) but total < _THIN_SAMPLE_
    # PIXELS=100 - one stray pixel at this scale is noise, not a real
    # exclusion pattern, and describe_sample already covers "too thin a
    # sample" for the exact same year without a confusing second callout.
    assert describe_out_of_range([60], 30) is None


def test_describe_out_of_range_well_past_severe_threshold_exact_text():
    # 400 of 1000 (40%) - the CMRI-pre-fix magnitude of problem this clause exists for.
    assert describe_out_of_range([600], 400) == (
        "400 of 1000 pixels (40%) fell outside this index's natural value range and were "
        "excluded from the numbers above - a large enough share that the mean may not "
        "represent the whole boundary; worth confirming against the excluded-pixel count "
        "before relying on it."
    )


# ------------------------------------------------------------- describe_trend


def test_describe_trend_single_year_returns_none():
    assert describe_trend("ndvi", {"2020": 0.5}) is None


def test_describe_trend_all_none_returns_none():
    assert describe_trend("ndvi", {"2020": None, "2021": None}) is None


def test_describe_trend_flat_case_exact_text():
    # change = 0.02, under _TREND_MIN_CHANGE (half a 0.1 bin = 0.05) -> flat.
    assert describe_trend("ndvi", {"2020": 0.40, "2021": 0.42}) == (
        "Across 2020-2021 the boundary mean is essentially flat (0.40 to 0.42, change +0.02)."
    )


def test_describe_trend_rising_case_uses_first_and_last_year_not_a_fitted_slope():
    # A middle year (2021: 0.36) is deliberately NOT the endpoint used - this
    # is a first-vs-last comparison, not a regression, per the function's own
    # docstring.
    assert describe_trend("ndvi", {"2020": 0.30, "2021": 0.36, "2022": 0.55}) == (
        "Across 2020-2022 the boundary mean has risen from 0.30 to 0.55 (+0.25). A rising index "
        "is consistent with vegetation gain but is not a measurement of it - VM0047 removals "
        "come from plot-based biomass sampling (s9.2); this series is supporting evidence, not "
        "the number."
    )


def test_describe_trend_falling_case_exact_text():
    assert describe_trend("ndvi", {"2019": 0.60, "2020": 0.30}) == (
        "Across 2019-2020 the boundary mean has fallen from 0.60 to 0.30 (-0.30). A falling "
        "index is worth investigating before it is reported - harvest, fire, grazing, a drought "
        "year, or simply a drier pre-monsoon compositing window can all produce it."
    )


def test_describe_trend_mndwi_rising_note_is_about_water_not_vegetation():
    # MNDWI's trend notes talk about standing water, not vegetation gain/loss
    # - proves the note is picked per-profile, not the shared NDVI wording.
    assert describe_trend("mndwi", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "MNDWI usually means more standing water (season, tank filling), not vegetation change."
    )


def test_describe_trend_ndwi_rising_note_is_about_water_not_vegetation():
    assert describe_trend("ndwi", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "NDWI usually means more standing water (season, tank filling), not vegetation change."
    )


def test_describe_trend_ndbi_rising_note_is_about_built_up_not_vegetation():
    assert describe_trend("ndbi", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "NDBI is consistent with new construction or bare/impervious surface expanding, not "
        "vegetation change."
    )


def test_describe_trend_ndmi_rising_note_is_about_moisture_not_vegetation_gain():
    assert describe_trend("ndmi", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "NDMI is consistent with increasing canopy/soil moisture (rainfall, irrigation, "
        "recovery), not necessarily vegetation gain - VM0047 removals come from plot-based "
        "biomass sampling (s9.2), not this series."
    )


def test_describe_trend_lswi_rising_note_is_about_flooding_not_vegetation():
    assert describe_trend("lswi", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "LSWI is consistent with increasing surface water or saturation (flooding, "
        "irrigation, monsoon), not vegetation gain."
    )


def test_describe_trend_bsi_rising_note_is_about_soil_exposure_not_vegetation_gain():
    assert describe_trend("bsi", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "BSI is consistent with soil exposure increasing - clearing, tillage, erosion or "
        "vegetation loss."
    )


def test_describe_trend_nddi_rising_note_is_about_drought_intensifying():
    assert describe_trend("nddi", {"2019": -0.10, "2020": 0.20}) == (
        "Across 2019-2020 the boundary mean has risen from -0.10 to 0.20 (+0.30). Rising "
        "NDDI is consistent with drought conditions intensifying (vegetation greenness "
        "falling relative to canopy moisture, or vice versa)."
    )


def test_describe_trend_cmri_rising_note_is_about_vegetation_like_not_mangrove_gain():
    assert describe_trend("cmri", {"2019": -0.40, "2020": -0.10}) == (
        "Across 2019-2020 the boundary mean has risen from -0.40 to -0.10 (+0.30). Rising "
        "CMRI is consistent with the boundary reading more vegetation-like relative to "
        "water, not a measurement of mangrove gain."
    )


def test_describe_trend_psri_rising_note_is_about_senescence_not_a_finding():
    assert describe_trend("psri", {"2019": 0.05, "2020": 0.15}) == (
        "Across 2019-2020 the boundary mean has risen from 0.05 to 0.15 (+0.10). Rising "
        "PSRI is consistent with increasing senescence, ripening or carotenoid dominance "
        "- not itself a forest-definition or degradation finding."
    )


# ----------------------------------------------------------- compose_summary


def test_compose_summary_no_clause_fires_falls_back_to_a_still_valid_sentence():
    """Every individual clause returns None for this input (mean/std_dev are
    None so level/variability are silent; the histogram is one unimodal
    cluster with min/max at the true bulk edges so outliers are silent; total
    pixel count is comfortably over _MODEST_SAMPLE_PIXELS=1000 so the sample
    clause is silent; series=None so trend is silent) - compose_summary must
    still return a valid, non-empty sentence, not an empty/blank body."""
    counts = [c * 3 for c in _unimodal_counts()]  # total 2301, well over 1000
    assert sum(counts) > 1000
    summary = compose_summary(
        "ndvi", "2023",
        mean=None, std_dev=None, minimum=_EDGES[10], maximum=_EDGES[19],
        bin_edges=_EDGES, counts=counts, series=None,
    )
    assert summary == (
        "2023: NDVI produced no usable pixel statistics for this year. "
        f"{DESCRIPTIVE_ONLY_TRAILER}"
    )


def test_compose_summary_every_clause_fires_stays_in_fixed_order():
    # level, variability, outliers, sample, trend - in that order, regardless
    # of call-argument order, so two runs on the same numbers always produce
    # the same string.
    counts = [2, 2, 2, 100, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 80, 2, 2, 2, 2]  # bimodal, total 216
    summary = compose_summary(
        "ndvi", "2022",
        mean=0.55, std_dev=0.30, minimum=-0.65, maximum=0.65,
        bin_edges=_EDGES, counts=counts,
        series={"2021": 0.30, "2022": 0.55},
    )
    assert summary == (
        "2022: NDVI averages 0.55 across the boundary - high, consistent with dense green "
        "canopy. Pixel values vary widely across the boundary (a spread of about 0.30), so "
        "the boundary mean averages over quite different surfaces and should not be read as "
        "one uniform condition. Values cluster in two or more separate groups (around -0.65 "
        "and 0.55), so the boundary contains two or more distinct surface types and the "
        "single NDVI mean is averaging across them. The sample is small (216 valid pixels, "
        "about 2.2 ha at 10 m after cloud masking) - fine for a visual read, thin for a trend "
        "claim. Across 2021-2022 the boundary mean has risen from 0.30 to 0.55 (+0.25). A "
        "rising index is consistent with vegetation gain but is not a measurement of it - "
        "VM0047 removals come from plot-based biomass sampling (s9.2); this series is "
        f"supporting evidence, not the number. {DESCRIPTIVE_ONLY_TRAILER}"
    )


def test_compose_summary_out_of_range_clause_slots_between_sample_and_trend():
    # Same bimodal input as the fixed-order test above, but now also passing
    # out_of_range_pixel_count - proves the new clause is wired into
    # compose_summary at the documented position (after sample, before
    # trend), not merely a standalone function nobody calls.
    counts = [2, 2, 2, 100, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 80, 2, 2, 2, 2]  # bimodal, total 216
    summary = compose_summary(
        "ndvi", "2022",
        mean=0.55, std_dev=0.30, minimum=-0.65, maximum=0.65,
        bin_edges=_EDGES, counts=counts,
        series={"2021": 0.30, "2022": 0.55},
        out_of_range_pixel_count=54,  # 54 of 270 total = 20%, the warn band
    )
    assert summary == (
        "2022: NDVI averages 0.55 across the boundary - high, consistent with dense green "
        "canopy. Pixel values vary widely across the boundary (a spread of about 0.30), so "
        "the boundary mean averages over quite different surfaces and should not be read as "
        "one uniform condition. Values cluster in two or more separate groups (around -0.65 "
        "and 0.55), so the boundary contains two or more distinct surface types and the "
        "single NDVI mean is averaging across them. The sample is small (216 valid pixels, "
        "about 2.2 ha at 10 m after cloud masking) - fine for a visual read, thin for a trend "
        "claim. 54 of 270 pixels (20%) fell outside this index's natural value range and "
        "were excluded from the numbers above - worth noting, not yet enough to distrust "
        "the mean. Across 2021-2022 the boundary mean has risen from 0.30 to 0.55 (+0.25). A "
        "rising index is consistent with vegetation gain but is not a measurement of it - "
        "VM0047 removals come from plot-based biomass sampling (s9.2); this series is "
        f"supporting evidence, not the number. {DESCRIPTIVE_ONLY_TRAILER}"
    )


# ------------------------------------------------------- summarize_index_result


def test_summarize_index_result_all_years_none_mean_falls_back_to_a_still_valid_sentence():
    # The other "no clause fires" edge case named in the task spec: every
    # year in `distribution` has mean=None, so there is no "most recent
    # usable year" to summarize at all - a different fallback branch from
    # compose_summary's own (this one never calls compose_summary).
    series = {"2020": None, "2021": None, "2022": None}
    distribution = {
        year: {"mean": None, "std_dev": None, "min": None, "max": None,
               "histogram": {"bin_edges": [], "counts": []}}
        for year in series
    }
    result = summarize_index_result("ndvi", series, distribution)
    assert result == (
        "NDVI produced no usable pixel statistics for any of the 3 years attempted - no "
        f"cloud-free coverage inside the boundary. {DESCRIPTIVE_ONLY_TRAILER}"
    )


def test_summarize_index_result_picks_most_recent_year_with_a_real_mean_not_simply_the_last_key():
    # 2022 is the last key but has mean=None (e.g. a cloud-blown year) - the
    # function must fall back to 2021, not silently produce an empty/None
    # summary for the nominally "latest" year.
    counts = [c * 3 for c in _unimodal_counts()]
    lo_edge, hi_edge = _EDGES[10], _EDGES[19]
    series = {"2020": 0.30, "2021": 0.50, "2022": None}
    distribution = {
        "2020": {"mean": 0.30, "std_dev": 0.03, "min": lo_edge, "max": hi_edge,
                 "histogram": {"bin_edges": _EDGES, "counts": counts}},
        "2021": {"mean": 0.50, "std_dev": 0.03, "min": lo_edge, "max": hi_edge,
                 "histogram": {"bin_edges": _EDGES, "counts": counts}},
        "2022": {"mean": None, "std_dev": None, "min": None, "max": None,
                 "histogram": {"bin_edges": [], "counts": []}},
    }
    result = summarize_index_result("ndvi", series, distribution)
    assert result == (
        "2021: NDVI averages 0.50 across the boundary - high, consistent with dense green "
        "canopy. Pixel values are uniform across the boundary (a spread of about 0.03), so "
        "the mean is a fair description of the whole area. Across 2020-2021 the boundary "
        "mean has risen from 0.30 to 0.50 (+0.20). A rising index is consistent with "
        "vegetation gain but is not a measurement of it - VM0047 removals come from "
        "plot-based biomass sampling (s9.2); this series is supporting evidence, not the "
        f"number. {DESCRIPTIVE_ONLY_TRAILER}"
    )


def test_summarize_index_result_reads_out_of_range_pixel_count_from_the_distribution_entry():
    # Proves summarize_index_result actually threads distribution[year]
    # ["out_of_range_pixel_count"] through to compose_summary, not just that
    # compose_summary itself accepts the parameter (already covered above).
    counts = [c * 3 for c in _unimodal_counts()]  # in-range total 2301
    lo_edge, hi_edge = _EDGES[10], _EDGES[19]
    series = {"2021": 0.50}
    distribution = {
        "2021": {
            "mean": 0.50, "std_dev": 0.03, "min": lo_edge, "max": hi_edge,
            "histogram": {"bin_edges": _EDGES, "counts": counts},
            "out_of_range_pixel_count": 767,  # 767 of 3068 total = 25%, the warn band
        },
    }
    result = summarize_index_result("ndvi", series, distribution)
    assert result == (
        "2021: NDVI averages 0.50 across the boundary - high, consistent with dense green "
        "canopy. Pixel values are uniform across the boundary (a spread of about 0.03), so "
        "the mean is a fair description of the whole area. 767 of 3068 pixels (25%) fell "
        "outside this index's natural value range and were excluded from the numbers above "
        f"- worth noting, not yet enough to distrust the mean. {DESCRIPTIVE_ONLY_TRAILER}"
    )
