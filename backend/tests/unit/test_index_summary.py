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


def test_index_profiles_keyed_by_all_five_index_ids():
    assert set(INDEX_PROFILES) == {"ndvi", "evi", "savi", "mndwi", "nbr"}


def test_ndvi_uses_the_usgs_descriptive_bands_not_mndwis_water_threshold():
    assert INDEX_PROFILES["ndvi"].edges == (0.1, 0.2, 0.5)


def test_mndwi_uses_the_xu_2006_open_water_threshold_not_ndvis_bands():
    assert INDEX_PROFILES["mndwi"].edges == (0.0,)


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
        "Pixel values are uniform across the boundary (std dev 0.02, under half a histogram "
        "bin), so the mean is a fair description of the whole area."
    )


def test_describe_variability_fairly_uniform_band():
    assert describe_variability(0.07) == (
        "Pixel values are fairly uniform across the boundary (std dev 0.07, under one "
        "histogram bin wide)."
    )


def test_describe_variability_moderate_band():
    assert describe_variability(0.15) == (
        "Pixel values are moderately variable across the boundary (std dev 0.15, one to two "
        "histogram bins), so the boundary covers noticeably different surfaces."
    )


def test_describe_variability_wide_band():
    assert describe_variability(0.30) == (
        "Pixel values vary widely across the boundary (std dev 0.30, more than two histogram "
        "bins), so the boundary mean averages over quite different surfaces and should not be "
        "read as one uniform condition."
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
        "The pixel histogram is multi-modal (peaks near -0.65 and 0.55), so the boundary "
        "contains two or more distinct surface types and the single NDVI mean is averaging "
        "across them."
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


# ------------------------------------------------------------- describe_trend


def test_describe_trend_single_year_returns_none():
    assert describe_trend("ndvi", {"2020": 0.5}) is None


def test_describe_trend_all_none_returns_none():
    assert describe_trend("ndvi", {"2020": None, "2021": None}) is None


def test_describe_trend_flat_case_exact_text():
    # change = 0.02, under _TREND_MIN_CHANGE (half a 0.1 bin = 0.05) -> flat.
    assert describe_trend("ndvi", {"2020": 0.40, "2021": 0.42}) == (
        "Across 2020-2021 the boundary mean is essentially flat (0.40 to 0.42, change +0.02 - "
        "under half a histogram bin)."
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
        "canopy. Pixel values vary widely across the boundary (std dev 0.30, more than two "
        "histogram bins), so the boundary mean averages over quite different surfaces and "
        "should not be read as one uniform condition. The pixel histogram is multi-modal "
        "(peaks near -0.65 and 0.55), so the boundary contains two or more distinct surface "
        "types and the single NDVI mean is averaging across them. The sample is small (216 "
        "valid pixels, about 2.2 ha at 10 m after cloud masking) - fine for a visual read, "
        "thin for a trend claim. Across 2021-2022 the boundary mean has risen from 0.30 to "
        "0.55 (+0.25). A rising index is consistent with vegetation gain but is not a "
        "measurement of it - VM0047 removals come from plot-based biomass sampling (s9.2); "
        f"this series is supporting evidence, not the number. {DESCRIPTIVE_ONLY_TRAILER}"
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
        "canopy. Pixel values are uniform across the boundary (std dev 0.03, under half a "
        "histogram bin), so the mean is a fair description of the whole area. Across "
        "2020-2021 the boundary mean has risen from 0.30 to 0.50 (+0.20). A rising index is "
        "consistent with vegetation gain but is not a measurement of it - VM0047 removals come "
        "from plot-based biomass sampling (s9.2); this series is supporting evidence, not the "
        f"number. {DESCRIPTIVE_ONLY_TRAILER}"
    )
