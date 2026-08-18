"""
Deterministic plain-language summary of one vegetation-index result.

Pure Python - no `ee`, DB, network or LLM. Given the per-year numbers
_annual_index_series() already computes (mean/std_dev/min/max/histogram),
this builds ONE English paragraph by string composition. Identical inputs
always produce byte-identical output, which is the point: this text lands in
an auditable analysis result a VVB may read, so it must be reproducible from
the stored numbers, not re-worded by a model on the next run.

WHAT THIS TEXT IS AND IS NOT
It is a DESCRIPTIVE reading of a pixel distribution. It is NOT an
eligibility, forest-definition, additionality or carbon determination, and no
clause here is phrased as one. Forest/non-forest runs through canopy cover
(the `canopy_cover_pct` forest-definition setting, consumed by the Hansen
pathway), NOT through an index mean; removals come from plot-based biomass
sampling (VM0047 s9.2), not from index values. "NDVI 0.55, therefore forest"
would be a VVB finding. Wording stays on "consistent with"/"worth checking",
deliberately, and every summary carries DESCRIPTIVE_ONLY_TRAILER.

WHERE THE NUMBERS BELOW COME FROM
Three indices have a genuine literature-standard cut point, all a binary
0.0 split with the same "check the boundary" framing on the positive side:
  * MNDWI at 0.0 - Xu, H. (2006), "Modification of normalised difference
    water index (NDWI) to enhance open water features in remotely sensed
    imagery", Int. J. Remote Sensing 27(14):3025-3033. Open water is
    positive, vegetation/soil/built-up negative, 0 is the split.
  * NDWI at 0.0 - McFeeters, S.K. (1996), "The use of the Normalized
    Difference Water Index (NDWI) in the delineation of open water
    features", Int. J. Remote Sensing 17(7):1425-1432. Same open-
    water-positive convention as MNDWI, different band pair (Green/NIR
    rather than Green/SWIR1) - kept as its own profile, not merged with
    MNDWI's, since the two are genuinely different formulas.
  * NDBI at 0.0 - Zha, Y., Gao, J., Ni, S. (2003), "Use of normalized
    difference built-up index in automatically mapping urban areas from TM
    imagery", Int. J. Remote Sensing 24(3):583-594. Built-up is positive,
    vegetation/soil/water negative, 0 is the split.
  * NDVI's bands are the conventional DESCRIPTIVE ranges published by USGS
    (Landsat NDVI) and NASA Earthdata: <~0.1 barren rock/sand/snow, ~0.2-0.5
    sparse grass/shrub/senescing, ~0.6-0.9 dense green canopy. A convention
    for reading a picture, not a standard's threshold - used here only to
    pick an adjective.
  * EVI, SAVI, GNDVI and NBR have NO universal absolute cut point. Rather
    than invent per-index numbers they REUSE NDVI's edges, with the
    consequences stated in their own caveat text:
      - EVI and SAVI (L=0.5 here) read systematically LOWER than NDVI over
        identical vegetation, so reusing NDVI's edges biases their adjective
        DOWNWARD (conservative), never upward.
      - GNDVI substitutes green for red and is more sensitive to
        chlorophyll concentration than NDVI over identical vegetation
        (Gitelson, Kaufman & Merzlyak 1996, Remote Sensing of Environment
        58(3):289-298, note the wider dynamic range, but publish no discrete
        cut points), so reusing NDVI's edges is a convenience
        reading, not a validated GNDVI-specific threshold.
      - NBR's severity classes (Key & Benson 2006, FIREMON Landscape
        Assessment) are defined on dNBR - pre-fire minus post-fire - not on
        the single-date absolute NBR this series computes. So NBR wording
        describes NIR-vs-SWIR contrast and names no severity class.
  * ARVI (Kaufman & Tanre 1992, Int. J. Remote Sensing 30) is the one
    NDVI-edge reuse with an actual literature basis for doing so: the
    authors themselves state ARVI has a similar dynamic range to NDVI
    (unlike EVI/SAVI, which read systematically lower) - ARVI is NDVI
    corrected for atmospheric scattering via the blue band, not a
    differently-scaled signal, so this reuse is a closer fit than EVI/
    SAVI/GNDVI's.
  * NDMI has NO literature-standard absolute cut point either - published
    NDMI-based moisture/soil thresholds vary by study (e.g. ResearchGate
    threads on Landsat 8 NDMI soil-moisture extraction report no single
    agreed number) rather than converging on one value the way MNDWI's Xu
    2006 split does. NDVI's edges are reused here too, purely as
    convenience bucket boundaries, with NDMI's own moisture-specific
    reading text (not NDVI's canopy-density text) and an explicit caveat
    that this is not a validated NDMI threshold.
  * LSWI (Xiao et al. 2005/2006) is mathematically IDENTICAL to NDMI - same
    NIR/SWIR1 band pair - but is shipped as its own index for the flood/
    wetland-monitoring naming convention that literature (paddy rice/
    wetland mapping) actually uses. Its own profile deliberately uses
    flood/saturation reading text, not NDMI's moisture-stress text, on the
    SAME underlying numbers - same "same numbers, index-specific wording"
    convention as EVI/SAVI/NBR reusing NDVI's numbers. Xiao et al.'s real
    published flood-detection rule is COMPARATIVE (a pixel is flagged
    flooded when LSWI + T >= EVI or NDVI on the SAME pixel), not an
    absolute LSWI value alone - this module has no access to another
    index's value inside one profile's describe_level, so LSWI's wording
    here describes the raw value only, never a flood/no-flood
    classification, and says so in its own caveat.
  * BSI has no single literature-standard cut point at all (published
    operational bare-soil-detection thresholds range from about 0.02-0.08
    in some agricultural studies to a -0.15..0.15 tested range in others,
    not one agreed number). 0.0 is used here only as the ratio's own
    natural mathematical sign boundary (Rikimaru et al. 2002's own sign
    convention: positive leans soil/mineral, negative leans vegetation/
    moisture), not a validated classification threshold - said explicitly
    in BSI's own words/caveat text, the same honesty NDMI/GNDVI's caveats
    already carry.
  * NDDI at 0.1/0.3 - Gu, Y. et al. (2007), Geophysical Research Letters
    34, L06407, publish an actual 3-class drought classification
    (non-drought <0.1, moderate 0.1-0.3, severe >=0.3) - a genuine
    literature-standard multi-band threshold, not a borrowed convenience
    bucket. Calibrated on MODIS grassland scenes with Gao (1996)'s NIR-
    SWIR1 NDWI (this platform's NDMI), not Sentinel-2 forestry/
    agroforestry boundaries - the class label is carried as indicative,
    not re-validated for this platform's use case, per NDDI's own caveat.
    NDDI's own valid range is ALSO wider than the shared [-1, 1] (its own
    [-3, 3] - see gee_analysis_service.py's `_INDEX_VALID_RANGE`): unlike
    CMRI's exact [-2, 2] bound, NDDI is a ratio whose denominator can
    approach zero whenever NDVI and NDMI have opposite signs with close
    magnitudes - common on ordinary land (positive NDVI over vegetation,
    slightly negative NDMI under dry conditions), not a rare artifact.
    Live verification found ~97% of one real test boundary's pixels
    clustered near a single value just outside [-1, 1], with a genuine
    unbounded tail beyond that (no clean fixed bound exists). [-3, 3] was
    chosen empirically to recover the well-behaved bulk while staying well
    short of where the real outlier tail starts - not a mathematically
    exact bound, and no denominator-near-zero guard was added, so some
    genuine outliers can still occasionally fall inside it on a different
    boundary. See NDDI's own caveat.
  * CMRI has no single universal absolute threshold either - Gupta et al.
    (2018)'s own paper reports 73.43% mangrove/non-mangrove discrimination
    accuracy but publishes no one masking number; a follow-on application
    (Keta Lagoon, Ghana) used a site-specific 0.51-0.70 masking range, not
    a validated universal cut point. 0 is used here only as the formula's
    natural sign boundary (same honest treatment as BSI). CMRI = NDVI -
    NDWI is also the one index in this registry whose natural range is
    wider than [-1, 1] - a plain difference (not a ratio) of two [-1,1]
    indices, its true range is [-2, 2]. This was FIRST implemented by
    keeping CMRI inside the shared [-1, 1] mask, relying on
    `out_of_range_pixel_count` to disclose (not hide) excluded pixels -
    but live-GEE verification against a real, ordinarily-vegetated (no
    water) test boundary showed this excludes ~98% of pixels routinely,
    not as a rare edge case: NDVI-NDWI naturally lands above 1.0 almost
    everywhere there is substantial vegetation on dry land, since NDVI is
    typically positive and NDWI (Green-NIR) is typically strongly negative
    over any non-water surface. That made the shared-range mask
    unrepresentative for CMRI specifically, so CMRI alone gets its own
    [-2, 2] range (`_INDEX_VALID_RANGE` in gee_analysis_service.py) -
    every other index still shares the plain [-1, 1] default.
  * PSRI has NO literature threshold at all - not even the inconsistent,
    study-varying operational ranges BSI/CMRI have. Merzlyak et al. (1999)
    establish only a DIRECTION (PSRI rises with senescence/carotenoid-to-
    chlorophyll ratio), no numeric cut point separating "senescent" from
    "healthy" anywhere found. So PSRI's profile uses an EMPTY edges tuple
    (`edges=()`) rather than any band at all - `_band_index` always returns
    0 for an empty edges tuple, so `words`/`readings` each carry exactly
    one entry and every mean gets the same flat, honestly-caveated
    reading, no adjective/threshold implied. Its natural range is also
    much narrower than every other index here (verified live on two real
    boundaries - a vegetated one, -0.23 to 0.46, and a real lake with open
    water/shoreline, -0.49 to 0.66 - both 100% in-range, comfortably
    inside the shared [-1, 1] but occupying only a fraction of it). PSRI
    still shares the plain [-1, 1] range for masking rather than getting
    its own override the way NDDI/CMRI did - not because the RedEdge
    denominator is mathematically guaranteed never to approach zero (it
    wasn't tested against every land-cover/turbidity/shadow combination),
    but because both tested boundaries stayed fully in-range; the generic
    `describe_out_of_range` clause below remains the disclosure mechanism
    for any untested scenario where it doesn't. Bin resolution is coarser
    relative to PSRI's own narrow dynamic range than for indices whose
    natural range fills [-1, 1].

The variability and outlier rules are keyed to the histogram's own bucket
width (_VEG_INDEX_HISTOGRAM_BINS in gee_analysis_service.py: 20 bins,
0.1-wide over the shared [-1, 1], 0.2-wide over CMRI's own [-2, 2], 0.3-wide
over NDDI's own [-3, 3]) rather than to fresh invented constants -
`compose_summary` derives the real bin_width from the bin_edges it's given
rather than assuming the shared default applies to every index.

OUT-OF-RANGE DISCLOSURE (Wave: composite indices)
`describe_out_of_range` is a distinct clause from `describe_sample`:
`describe_sample` flags too FEW in-range pixels in absolute terms;
`describe_out_of_range` flags a large SHARE of the pixels that existed being
excluded by the natural-range mask (`out_of_range_pixel_count`) - a
boundary with 5000 in-range pixels reads as "ample sample" to
`describe_sample` even if another 5000 were thrown out right alongside
them, and before this clause existed that exclusion could pass through the
summary with zero mention regardless of how large the excluded share was.
Added after live-GEE verification found NDDI's empirical [-3, 3] range
(chosen from ONE test boundary's distribution, not a mathematically exact
bound the way CMRI's [-2, 2] is) is not guaranteed to keep every real,
more heterogeneous project boundary under a low exclusion ratio - this is
the safety net for that case, generic across every index (most, EVI/SAVI
included, simply never cross the 10% trigger and stay silent).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "INDEX_RANGE", "INDEX_HISTOGRAM_BIN_WIDTH", "DESCRIPTIVE_ONLY_TRAILER",
    "IndexProfile", "INDEX_PROFILES", "get_profile",
    "describe_level", "describe_variability", "describe_spatial_outliers",
    "describe_sample", "describe_out_of_range", "describe_trend",
    "compose_summary", "summarize_index_result",
]

# Natural range all 5 indices are read on, and the range the map tile is
# stretched over (visualize(min=-1, max=1) in gee_analysis_service.py).
INDEX_RANGE = (-1.0, 1.0)

# One histogram bucket: _VEG_INDEX_HISTOGRAM_RANGE (-1, 1) over
# _VEG_INDEX_HISTOGRAM_BINS (20). Duplicated as a literal rather than imported
# so this module stays free of the GEE service's `ee` import chain - if the
# service's binning changes, this must change with it.
INDEX_HISTOGRAM_BIN_WIDTH = 0.1

DESCRIPTIVE_ONLY_TRAILER = (
    "Descriptive only - not a forest-definition, eligibility or carbon determination."
)


@dataclass(frozen=True)
class IndexProfile:
    """Everything the clause builders need about one index.

    `edges` ascending, len(words) == len(readings) == len(edges) + 1.
    `edge_source` is carried so the provenance of every number here is
    readable from the object itself (tests assert it is non-empty)."""
    label: str
    edges: tuple[float, ...]
    words: tuple[str, ...]
    readings: tuple[str, ...]
    edge_source: str
    caveat: str | None = None
    trend_up_note: str | None = None
    trend_down_note: str | None = None


# USGS Landsat NDVI / NASA Earthdata descriptive ranges. All three edges land
# on an existing 0.1-wide histogram bin edge, so an adjective change is always
# visible as a bar boundary on the chart the frontend already draws.
_GREEN_EDGES = (0.1, 0.2, 0.5)
_GREEN_WORDS = ("very low", "low", "moderate", "high")
_GREEN_READINGS = (
    "consistent with bare ground, built-up surface, water or masked gaps rather than vegetation",
    "consistent with sparse or stressed vegetation",
    "consistent with grass, shrub, cropland or senescing vegetation rather than closed canopy",
    "consistent with dense green canopy",
)
_GREEN_SOURCE = (
    "USGS Landsat NDVI / NASA Earthdata descriptive ranges (<~0.1 barren, ~0.2-0.5 sparse "
    "grass/shrub/senescing, ~0.6-0.9 dense green canopy) - a descriptive convention for "
    "reading imagery, not a standard's threshold"
)
_TREND_UP_NOTE = (
    "A rising index is consistent with vegetation gain but is not a measurement of it - "
    "VM0047 removals come from plot-based biomass sampling (s9.2); this series is "
    "supporting evidence, not the number."
)
_TREND_DOWN_NOTE = (
    "A falling index is worth investigating before it is reported - harvest, fire, grazing, "
    "a drought year, or simply a drier pre-monsoon compositing window can all produce it."
)

# Shared by MNDWI and NDWI - both a single literature 0.0 open-water/land
# split (see module docstring), differing only in which paper and which band
# pair, not in the reading itself. Kept as one pair of word/reading tuples
# rather than duplicated per index, same "shared constant, per-profile
# edge_source" convention _GREEN_EDGES/_GREEN_WORDS/_GREEN_READINGS already
# use for NDVI/EVI/SAVI/NBR.
_WATER_EDGES = (0.0,)
_WATER_WORDS = ("below the 0 open-water cut point", "above the 0 open-water cut point")
_WATER_READINGS = (
    "consistent with land - vegetation, soil or built-up surface - rather than open water",
    "the boundary mean itself reads as open water, which for a land-based project "
    "boundary usually means a tank, reservoir or river covers a large share of the "
    "area - check the boundary before using the mean",
)
INDEX_PROFILES: dict[str, IndexProfile] = {
    "ndvi": IndexProfile(
        label="NDVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE,
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "evi": IndexProfile(
        label="EVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE + "; reused for EVI, which has no universal absolute cut point",
        caveat=(
            "EVI reads lower than NDVI over identical vegetation, so this wording is "
            "deliberately conservative and is not comparable word-for-word with NDVI's."
        ),
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "savi": IndexProfile(
        label="SAVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE + "; reused for SAVI, which has no universal absolute cut point",
        caveat=(
            "SAVI (L=0.5) reads lower than NDVI over identical vegetation, so this wording is "
            "deliberately conservative and is not comparable word-for-word with NDVI's."
        ),
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "nbr": IndexProfile(
        label="NBR", edges=_GREEN_EDGES, words=_GREEN_WORDS,
        readings=(
            "high SWIR relative to NIR - consistent with recently burned, bare or built-up surface",
            "consistent with sparse or stressed vegetation",
            "consistent with partially vegetated or recovering surface",
            "consistent with dense, unburned, moist vegetation",
        ),
        edge_source=(
            _GREEN_SOURCE + "; reused for NBR, which has no universal absolute cut point "
            "(published severity classes are defined on dNBR, not single-date NBR)"
        ),
        caveat=(
            "Burn-severity classes (Key & Benson 2006, FIREMON) are defined on dNBR - a "
            "pre-fire minus post-fire difference - not on the single-date NBR shown here, so "
            "no severity class is asserted."
        ),
        trend_up_note=(
            "Rising NBR is consistent with vegetation recovery or regrowth after disturbance."
        ),
        trend_down_note=(
            "Falling NBR can indicate fire, clearing or drying - check the disturbance record "
            "for the drop year before reporting it as anything."
        ),
    ),
    "mndwi": IndexProfile(
        label="MNDWI", edges=_WATER_EDGES,
        words=tuple(f"{w} (Xu 2006)" for w in _WATER_WORDS),
        readings=_WATER_READINGS,
        edge_source=(
            "Xu, H. (2006), Int. J. Remote Sensing 27(14):3025-3033 - open water positive, "
            "vegetation/soil/built-up negative, 0 the split"
        ),
        trend_up_note="Rising MNDWI usually means more standing water (season, tank filling), not vegetation change.",
        trend_down_note="Falling MNDWI usually means less standing water, not vegetation change.",
    ),
    "ndwi": IndexProfile(
        label="NDWI", edges=_WATER_EDGES,
        words=tuple(f"{w} (McFeeters 1996)" for w in _WATER_WORDS),
        readings=_WATER_READINGS,
        edge_source=(
            "McFeeters, S.K. (1996), Int. J. Remote Sensing 17(7):1425-1432 - open water "
            "positive, vegetation/soil/built-up negative, 0 the split. Distinct formula from "
            "MNDWI (Green/NIR here vs. Green/SWIR1), not a duplicate reading of it."
        ),
        trend_up_note="Rising NDWI usually means more standing water (season, tank filling), not vegetation change.",
        trend_down_note="Falling NDWI usually means less standing water, not vegetation change.",
    ),
    "gndvi": IndexProfile(
        label="GNDVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE + "; reused for GNDVI, which has no universal absolute cut point",
        caveat=(
            "GNDVI substitutes the green band for red and is more sensitive to chlorophyll "
            "concentration than NDVI over identical vegetation, so this wording is not "
            "comparable word-for-word with NDVI's."
        ),
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "ndbi": IndexProfile(
        label="NDBI", edges=(0.0,),
        words=("below the 0 built-up cut point (Zha et al. 2003)", "above the 0 built-up cut point (Zha et al. 2003)"),
        readings=(
            "consistent with vegetation, soil or water rather than built-up surface",
            "the boundary mean itself reads as built-up surface, which for a land-based "
            "project boundary is worth checking - a real settlement/infrastructure inside "
            "the boundary, or a misdrawn boundary edge",
        ),
        edge_source=(
            "Zha, Y., Gao, J., Ni, S. (2003), Int. J. Remote Sensing 24(3):583-594 - "
            "built-up positive, vegetation/soil/water negative, 0 the split"
        ),
        trend_up_note=(
            "Rising NDBI is consistent with new construction or bare/impervious surface "
            "expanding, not vegetation change."
        ),
        trend_down_note=(
            "Falling NDBI is consistent with vegetation regrowth or a surface change away "
            "from built-up/bare ground."
        ),
    ),
    "arvi": IndexProfile(
        label="ARVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=(
            _GREEN_SOURCE + "; reused for ARVI, which Kaufman & Tanre (1992) note has a "
            "similar dynamic range to NDVI (unlike EVI/SAVI, which read systematically lower)"
        ),
        caveat=(
            "ARVI is NDVI corrected for atmospheric (aerosol/haze) scattering using the blue "
            "band, not a differently-scaled signal - readings are broadly comparable to "
            "NDVI's, but the two can diverge under real haze/aerosol conditions, which is the "
            "point of the correction."
        ),
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "ndmi": IndexProfile(
        label="NDMI", edges=_GREEN_EDGES, words=_GREEN_WORDS,
        readings=(
            "consistent with dry, bare or strongly moisture-stressed canopy",
            "consistent with vegetation under noticeable moisture stress",
            "consistent with vegetation carrying moderate canopy moisture",
            "consistent with high canopy moisture content - dense, well-watered vegetation",
        ),
        edge_source=(
            _GREEN_SOURCE + "; reused for NDMI, which has no universal absolute cut point of "
            "its own - published NDMI-based moisture thresholds vary by study rather than "
            "converging on one number"
        ),
        caveat=(
            "NDMI measures canopy/soil moisture (NIR vs. SWIR1 reflectance), not chlorophyll "
            "or biomass - a high reading here is not the same claim as high NDVI, and this "
            "wording is not comparable word-for-word with NDVI's."
        ),
        trend_up_note=(
            "Rising NDMI is consistent with increasing canopy/soil moisture (rainfall, "
            "irrigation, recovery), not necessarily vegetation gain - VM0047 removals come "
            "from plot-based biomass sampling (s9.2), not this series."
        ),
        trend_down_note=(
            "Falling NDMI is consistent with drying conditions, drought stress or reduced "
            "canopy moisture - worth checking against the season/rainfall record before "
            "reading it as vegetation loss."
        ),
    ),
    "lswi": IndexProfile(
        label="LSWI", edges=_GREEN_EDGES, words=_GREEN_WORDS,
        readings=(
            "consistent with a dry, bare or unflooded low-moisture surface",
            "consistent with drier vegetation or soil, not standing water or saturated ground",
            "consistent with moderate surface moisture - a wetter soil or partially "
            "saturated surface",
            "consistent with high surface water content - standing water or saturated "
            "ground",
        ),
        edge_source=(
            _GREEN_SOURCE + "; reused for LSWI, which has no absolute cut point of its own. "
            "LSWI is mathematically identical to NDMI (same NIR/SWIR1 band pair) but is read "
            "here for the flood/wetland convention (Xiao et al. 2005/2006), not moisture "
            "stress - see this module's own docstring for why the same numbers get distinct "
            "index-specific wording."
        ),
        caveat=(
            "LSWI's published flood-detection use compares LSWI against EVI/NDVI on the same "
            "pixel (LSWI + T >= EVI/NDVI), not an absolute LSWI value alone - this wording "
            "describes the raw value only, not a flood/no-flood classification."
        ),
        trend_up_note=(
            "Rising LSWI is consistent with increasing surface water or saturation "
            "(flooding, irrigation, monsoon), not vegetation gain."
        ),
        trend_down_note=(
            "Falling LSWI is consistent with surface drying or a flood/inundation event "
            "ending, not vegetation loss."
        ),
    ),
    "bsi": IndexProfile(
        label="BSI", edges=(0.0,),
        words=(
            "below the ratio's zero sign boundary (no literature-standard bare-soil "
            "threshold exists)",
            "above the ratio's zero sign boundary (no literature-standard bare-soil "
            "threshold exists)",
        ),
        readings=(
            "consistent with vegetated or moist surface rather than exposed bare soil",
            "consistent with exposed bare soil, degraded land, or a recently cleared/tilled "
            "surface rather than dense vegetation",
        ),
        edge_source=(
            "Rikimaru, Roy & Miyatake (2002), Tropical Ecology 43(1):39-47, defines the "
            "ratio's sign convention (positive leans soil/mineral, negative leans "
            "vegetation/moisture). No single absolute bare-soil detection threshold is "
            "agreed in the literature - published operational thresholds range from about "
            "0.02-0.08 in some agricultural studies to a -0.15..0.15 tested range in "
            "others - so 0 is used here only as the ratio's natural mathematical sign "
            "boundary, not a validated universal cut point."
        ),
        caveat=(
            "BSI has no literature-standard absolute threshold the way MNDWI/NDWI/NDBI do - "
            "the reading above only reports which side of zero the ratio falls, not a "
            "validated bare-soil classification. 'Degraded land' and 'recently cleared/"
            "tilled' name plausible causes of a soil-leaning spectral signature, not a "
            "degradation, clearing or land-use-change determination - VM0047 degradation/"
            "reversal findings run through the monitored disturbance record and plot data, "
            "not a single-date spectral mean."
        ),
        trend_up_note=(
            "Rising BSI is consistent with soil exposure increasing - clearing, tillage, "
            "erosion or vegetation loss."
        ),
        trend_down_note=(
            "Falling BSI is consistent with vegetation or moisture cover increasing over "
            "previously bare/exposed ground."
        ),
    ),
    "nddi": IndexProfile(
        label="NDDI", edges=(0.1, 0.3),
        words=(
            "below the 0.1 non-drought cut point (Gu et al. 2007)",
            "in the 0.1-0.3 moderate-drought band (Gu et al. 2007)",
            "at or above the 0.3 severe-drought cut point (Gu et al. 2007)",
        ),
        readings=(
            "consistent with a non-drought vegetation-water balance",
            "consistent with moderate drought stress relative to the local "
            "vegetation-water balance",
            "consistent with severe drought stress relative to the local "
            "vegetation-water balance",
        ),
        edge_source=(
            "Gu, Y. et al. (2007), Geophysical Research Letters 34, L06407 - a real "
            "published 3-class drought classification (non-drought <0.1, moderate "
            "0.1-0.3, severe >=0.3), not a borrowed convenience bucket, derived for MODIS "
            "grassland drought monitoring over the central Great Plains."
        ),
        caveat=(
            "NDDI here uses Gao (1996)'s NIR-SWIR1 NDWI (this platform's NDMI, "
            "mathematically identical), the same input Gu et al. used - not McFeeters' "
            "Green-NIR NDWI. The published 0.1/0.3 classification was calibrated on "
            "MODIS grassland scenes, not Sentinel-2 forestry/agroforestry boundaries, so "
            "the class label is indicative, not re-validated for this platform's use case. "
            "This class label describes the spectral vegetation-water balance only, not a "
            "VM0047 degradation, reversal or disturbance finding - those run through the "
            "monitored disturbance record and plot-based data (s9.2), not a single-date "
            "spectral snapshot."
        ),
        trend_up_note=(
            "Rising NDDI is consistent with drought conditions intensifying (vegetation "
            "greenness falling relative to canopy moisture, or vice versa)."
        ),
        trend_down_note="Falling NDDI is consistent with drought conditions easing.",
    ),
    "cmri": IndexProfile(
        label="CMRI", edges=(0.0,),
        words=(
            "below the ratio's zero sign boundary (no universal validated "
            "mangrove-masking threshold exists)",
            "above the ratio's zero sign boundary (no universal validated "
            "mangrove-masking threshold exists)",
        ),
        readings=(
            "leaning water-like rather than vegetation-like in this NDVI-vs-NDWI "
            "comparison",
            "leaning vegetation-like rather than water-like in this NDVI-vs-NDWI "
            "comparison, the direction associated with mangrove/dense-vegetation pixels "
            "in Gupta et al. (2018)",
        ),
        edge_source=(
            "Gupta, K. et al. (2018), MethodsX 5:1129-1139 (Combined Mangrove "
            "Recognition Index, CMRI = NDVI - NDWI) report 73.43% mangrove/non-mangrove "
            "discrimination accuracy but publish no one universal absolute masking "
            "threshold; a follow-on application (Keta Lagoon, Ghana) used a "
            "site-specific 0.51-0.70 masking range, not a validated universal cut point "
            "- so 0 is used here only as the formula's natural sign boundary."
        ),
        caveat=(
            "CMRI = NDVI - NDWI is a plain difference, not a ratio, so its true range is "
            "[-2, 2], wider than the [-1, 1] every other index here shares - CMRI is "
            "computed and masked on its own [-2, 2] range (not the shared [-1, 1]) for "
            "exactly this reason: live verification showed ordinary vegetated (non-water) "
            "boundaries commonly land above 1.0, so a shared [-1, 1] mask would exclude "
            "most pixels routinely, not rarely. This is not a mangrove/non-mangrove "
            "classification - no validated universal threshold exists."
        ),
        trend_up_note=(
            "Rising CMRI is consistent with the boundary reading more vegetation-like "
            "relative to water, not a measurement of mangrove gain."
        ),
        trend_down_note=(
            "Falling CMRI is consistent with the boundary reading more water-like "
            "relative to vegetation - worth checking against tide stage/season before "
            "reading it as vegetation loss."
        ),
    ),
    "psri": IndexProfile(
        label="PSRI", edges=(),
        words=("not benchmarked against a literature threshold",),
        readings=(
            "the raw carotenoid-to-chlorophyll reflectance ratio - higher is consistent "
            "with more senescent or ripening vegetation, lower (or negative) with "
            "actively growing green vegetation (Merzlyak et al. 1999), though no "
            "published cut point marks the boundary between them",
        ),
        edge_source=(
            "Merzlyak, M.N. et al. (1999), 'Non-destructive optical detection of pigment "
            "changes during leaf senescence and fruit ripening', Physiologia Plantarum "
            "106(1):135-141 - establishes the DIRECTION (PSRI rises with senescence/"
            "carotenoid:chlorophyll dominance) but publishes no absolute numeric "
            "threshold separating 'senescent' from 'healthy', unlike even BSI's "
            "inconsistent-but-real study-specific operational ranges."
        ),
        caveat=(
            "PSRI has no literature-standard threshold at all - only a direction is "
            "established, so no adjective band is assigned here, just the direction and "
            "the raw number. PSRI's natural range is also much narrower than every other "
            "index here (typically a fraction of [-1, 1]), so small absolute changes can "
            "be a larger relative shift than the same change would be for NDVI. "
            "'Senescence' and 'ripening' name plausible physiological readings of a "
            "rising carotenoid:chlorophyll ratio, not a VM0047 degradation or reversal "
            "determination - those run through the monitored disturbance record and "
            "plot-based data (s9.2), not a single-date spectral mean."
        ),
        trend_up_note=(
            "Rising PSRI is consistent with increasing senescence, ripening or carotenoid "
            "dominance - not itself a forest-definition or degradation finding."
        ),
        trend_down_note=(
            "Falling PSRI is consistent with fresher, more actively photosynthesizing "
            "vegetation."
        ),
    ),
}


def get_profile(index_id: str) -> IndexProfile:
    try:
        return INDEX_PROFILES[index_id]
    except KeyError:
        raise ValueError(
            f"no index summary profile for {index_id!r}; known: {sorted(INDEX_PROFILES)}"
        ) from None


# ------------------------------------------------------------- small helpers


def _band_index(edges: tuple[float, ...], value: float) -> int:
    """How many ascending cut points `value` meets or exceeds -> band index."""
    return sum(1 for edge in edges if value >= edge)


def _and_list(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _bin_centre(bin_edges: list[float], i: int) -> float:
    return (bin_edges[i] + bin_edges[i + 1]) / 2.0


# ------------------------------------------------------------ clause: level


def describe_level(index_id: str, mean: float | None) -> str | None:
    """Adjective for the boundary mean. See module docstring for where each
    index's band edges come from - only MNDWI's is a real literature cut
    point. Prints 2dp for readability; the stored value stays 4dp."""
    profile = get_profile(index_id)
    if mean is None:
        return None
    band = _band_index(profile.edges, mean)
    sentence = (
        f"{profile.label} averages {mean:.2f} across the boundary - "
        f"{profile.words[band]}, {profile.readings[band]}."
    )
    return f"{sentence} {profile.caveat}" if profile.caveat else sentence


# ------------------------------------------------------ clause: variability


# Keyed to the histogram bucket width, not to invented numbers: under half a
# bucket = the mean describes everything; over two buckets = it describes
# nothing in particular. Thirteen of the fourteen indices share the same
# fixed [-1, 1] / 20-bin histogram (0.1-wide buckets); CMRI's own [-2, 2]
# range (see gee_analysis_service.py's `_INDEX_VALID_RANGE`) has 0.2-wide
# buckets instead - `describe_variability`'s `bin_width` parameter carries
# that through rather than assuming the module constant applies universally.
_VAR_UNIFORM = 0.5   # x bin_width
_VAR_FAIRLY_UNIFORM = 1.0  # x bin_width
_VAR_MODERATE = 2.0  # x bin_width


def describe_variability(
    std_dev: float | None, bin_width: float = INDEX_HISTOGRAM_BIN_WIDTH
) -> str | None:
    """Wording is deliberately plain-language (a field-team reviewer, not
    just a VVB auditor, reads this callout) - "a spread of about X" instead
    of "std dev X", no "histogram bin" - while still carrying the real
    number, not just an adjective, so nothing quantitative is lost.

    `bin_width` defaults to the shared 0.1 every index but CMRI uses -
    `compose_summary` derives the real value from the actual histogram
    bin_edges it's given, rather than assuming the module default applies
    to every index (see this module's own docstring)."""
    if std_dev is None:
        return None
    if std_dev < _VAR_UNIFORM * bin_width:
        return (
            f"Pixel values are uniform across the boundary (a spread of about {std_dev:.2f}), "
            "so the mean is a fair description of the whole area."
        )
    if std_dev < _VAR_FAIRLY_UNIFORM * bin_width:
        return (
            f"Pixel values are fairly uniform across the boundary (a spread of about "
            f"{std_dev:.2f})."
        )
    if std_dev < _VAR_MODERATE * bin_width:
        return (
            f"Pixel values are moderately variable across the boundary (a spread of about "
            f"{std_dev:.2f}), so the boundary covers noticeably different surfaces."
        )
    return (
        f"Pixel values vary widely across the boundary (a spread of about {std_dev:.2f}), so "
        "the boundary mean averages over quite different surfaces and should not be read as "
        "one uniform condition."
    )


# --------------------------------------------------------- clause: outliers


_BULK_BIN_MIN_FRACTION = 0.01   # a bin holding <1% of pixels is not "the bulk"
_TAIL_BIN_GAP = 2               # min/max this many bins beyond the bulk = a real tail
_PEAK_MIN_FRACTION = 0.15       # a peak must be >=15% of the tallest bin
_PEAK_MIN_SEPARATION = 2        # peaks closer than this are one plateau
_VALLEY_MAX_FRACTION = 0.50     # two peaks need a valley <=50% of the smaller peak


def _dominant_peaks(counts: list[int]) -> list[int]:
    """Bin indices of real modes. Local maxima >= _PEAK_MIN_FRACTION of the
    tallest bin, plateaus/near-neighbours collapsed to the tallest, and a
    second mode only counted when a genuine valley separates it from the
    previous one - so a single fat skewed peak does not read as bimodal."""
    if not counts:
        return []
    top = max(counts)
    if top <= 0:
        return []
    candidates = [
        i for i, c in enumerate(counts)
        if c >= top * _PEAK_MIN_FRACTION
        and c >= (counts[i - 1] if i > 0 else 0)
        and c >= (counts[i + 1] if i + 1 < len(counts) else 0)
    ]
    kept: list[int] = []
    for i in candidates:
        if kept and i - kept[-1] < _PEAK_MIN_SEPARATION:
            if counts[i] > counts[kept[-1]]:
                kept[-1] = i
            continue
        kept.append(i)
    peaks = kept[:1]
    for prev, cur in zip(kept, kept[1:]):
        valley = min(counts[prev + 1:cur])
        if valley <= _VALLEY_MAX_FRACTION * min(counts[prev], counts[cur]):
            peaks.append(cur)
    return peaks


def _bulk_span(counts: list[int], total: int) -> tuple[int, int]:
    """Lowest/highest bin index holding at least _BULK_BIN_MIN_FRACTION of the
    pixels. With a small total this floor admits single-pixel bins, which makes
    the tail test fire LESS often - the safe direction."""
    floor = total * _BULK_BIN_MIN_FRACTION
    occupied = [i for i, c in enumerate(counts) if c >= floor]
    if not occupied:
        peak = max(range(len(counts)), key=lambda i: counts[i])
        return peak, peak
    return occupied[0], occupied[-1]


def describe_spatial_outliers(
    index_id: str,
    minimum: float | None,
    maximum: float | None,
    bin_edges: list[float] | None,
    counts: list[int] | None,
) -> str | None:
    """Returns None when the distribution is a single unremarkable cluster -
    no filler text. Fires only on a real multi-modal histogram or a min/max
    sitting >= _TAIL_BIN_GAP bins beyond where the pixels actually are."""
    profile = get_profile(index_id)
    if not counts or not bin_edges or len(bin_edges) != len(counts) + 1:
        return None
    total = sum(counts)
    if total <= 0:
        return None
    bin_width = bin_edges[1] - bin_edges[0]
    if bin_width <= 0:
        return None

    parts: list[str] = []

    peaks = _dominant_peaks(counts)
    if len(peaks) >= 2:
        centres = _and_list([f"{_bin_centre(bin_edges, i):.2f}" for i in peaks])
        parts.append(
            f"Values cluster in two or more separate groups (around {centres}), so the "
            f"boundary contains two or more distinct surface types and the single "
            f"{profile.label} mean is averaging across them."
        )

    lo_bin, hi_bin = _bulk_span(counts, total)
    if minimum is not None:
        bulk_low = bin_edges[lo_bin]
        if bulk_low - minimum >= _TAIL_BIN_GAP * bin_width:
            parts.append(
                f"A thin low tail runs down to {minimum:.2f}, well below the bulk of the pixels "
                f"(which start around {bulk_low:.2f}) - typically water, bare ground, cloud "
                "shadow or a sliver of neighbouring land caught by the boundary edge; worth "
                "checking before reading the mean as representative."
            )
    if maximum is not None:
        bulk_high = bin_edges[hi_bin + 1]
        if maximum - bulk_high >= _TAIL_BIN_GAP * bin_width:
            parts.append(
                f"A thin high tail reaches {maximum:.2f}, well above the bulk of the pixels "
                f"(which end around {bulk_high:.2f})."
            )

    return " ".join(parts) if parts else None


# ----------------------------------------------------------- clause: sample


# 10 m x 10 m Sentinel-2 pixel = 0.01 ha - the scale=10 used in the
# reduceRegion that produced these counts.
_PIXEL_AREA_HA = 0.01
_THIN_SAMPLE_PIXELS = 100     # ~1 ha of valid pixels
_MODEST_SAMPLE_PIXELS = 1000  # ~10 ha


def describe_sample(counts: list[int] | None) -> str | None:
    """Caveat when the year's cloud-free sample is too thin to lean on.

    NOTE: `counts` counts IN-RANGE pixels only - gee_analysis_service.py's
    `_annual_index_series` masks each index to its natural [-1, 1] range
    BEFORE reduceRegion, so mean/std_dev/min/max above are computed over
    that same in-range set and `counts` is an EXACT count of it, not a lower
    bound (EVI's denominator approaching zero, e.g. under thin haze/cloud
    edges, is the main source of out-of-range pixels; that count is surfaced
    separately as `distribution[year]["out_of_range_pixel_count"]`, not
    folded in here)."""
    total = sum(counts) if counts else 0
    if total <= 0:
        return (
            "No in-range cloud-free pixels were available inside the boundary for this year, "
            "so there is nothing to describe - treat this year as missing, not as zero."
        )
    if total < _THIN_SAMPLE_PIXELS:
        return (
            f"Only {total} valid pixels (about {total * _PIXEL_AREA_HA:.2f} ha at 10 m) survived "
            "cloud masking inside the boundary, so every number above is indicative only - too "
            "thin a sample to carry into any reporting."
        )
    if total < _MODEST_SAMPLE_PIXELS:
        return (
            f"The sample is small ({total} valid pixels, about {total * _PIXEL_AREA_HA:.1f} ha at "
            "10 m after cloud masking) - fine for a visual read, thin for a trend claim."
        )
    return None


# -------------------------------------------------- clause: out-of-range share


# Distinct concern from describe_sample above: that one flags too FEW in-
# range pixels in absolute terms; this flags a large SHARE of the pixels
# that existed being excluded by the natural-range mask
# (out_of_range_pixel_count), which describe_sample's count-only check can
# miss entirely - a boundary with 5000 in-range pixels reads as "ample
# sample" to describe_sample even if another 5000 were thrown out right
# alongside them. Most indices essentially never hit this (EVI/SAVI's rare
# denominator blowups keep this near 0%); NDDI/CMRI's wider empirical/exact
# ranges (see gee_analysis_service.py's `_INDEX_VALID_RANGE`) were chosen
# from ONE test boundary's distribution and are not guaranteed to keep every
# real, more heterogeneous project boundary under a low exclusion ratio -
# this clause is the safety net for that case, so a high-exclusion year
# cannot pass through the summary with zero mention the way it could before
# this clause existed.
_OUT_OF_RANGE_RATIO_WARN = 0.10    # >=10% excluded - worth a callout
_OUT_OF_RANGE_RATIO_SEVERE = 0.30  # >=30% excluded - the mean may not represent the boundary


def describe_out_of_range(
    counts: list[int] | None, out_of_range_pixel_count: int | None
) -> str | None:
    """Fires on the RATIO of excluded to total pixels, not the raw count -
    `describe_sample` already covers "too few pixels total". Silent (None)
    whenever `out_of_range_pixel_count` is falsy/unavailable, so this is a
    strict no-op for any caller that doesn't pass it (backward compatible
    with every existing compose_summary/summarize_index_result call).

    Also silent below `_THIN_SAMPLE_PIXELS` total: with a tiny total (e.g.
    3-10 cloud-free pixels), the ratio is highly noisy - one stray pixel can
    cross the 30% "severe" line and trigger "the mean may not represent the
    boundary" wording driven by n=1 noise, stacked confusingly on top of
    `describe_sample`'s own (correct, and sufficient on its own) thin-sample
    callout for the exact same year."""
    if not counts or not out_of_range_pixel_count or out_of_range_pixel_count <= 0:
        return None
    in_range_total = sum(counts)
    total = in_range_total + out_of_range_pixel_count
    if total < _THIN_SAMPLE_PIXELS:
        return None
    ratio = out_of_range_pixel_count / total
    if ratio < _OUT_OF_RANGE_RATIO_WARN:
        return None
    if ratio < _OUT_OF_RANGE_RATIO_SEVERE:
        return (
            f"{out_of_range_pixel_count} of {total} pixels ({ratio:.0%}) fell outside this "
            "index's natural value range and were excluded from the numbers above - worth "
            "noting, not yet enough to distrust the mean."
        )
    return (
        f"{out_of_range_pixel_count} of {total} pixels ({ratio:.0%}) fell outside this "
        "index's natural value range and were excluded from the numbers above - a large "
        "enough share that the mean may not represent the whole boundary; worth confirming "
        "against the excluded-pixel count before relying on it."
    )


# ------------------------------------------------------------ clause: trend


_TREND_MIN_CHANGE = 0.5  # x bin_width - half a histogram bin


def describe_trend(
    index_id: str,
    series: dict[str, float | None] | None,
    bin_width: float = INDEX_HISTOGRAM_BIN_WIDTH,
) -> str | None:
    """First vs last year that actually produced a mean. Deliberately a plain
    endpoint difference, not a fitted slope: a regression slope invites being
    read as a rate, and this is not the VM0047 stocking-index Z-test (Appendix
    1) - that runs on control vs project plots, not on one boundary mean.

    `bin_width` defaults to the shared 0.1 every index but CMRI uses - see
    `describe_variability`'s own docstring for why this isn't a module-level
    constant anymore."""
    years = sorted((y for y, v in (series or {}).items() if v is not None), key=int)
    if len(years) < 2:
        return None
    profile = get_profile(index_id)
    first, last = years[0], years[-1]
    start, end = series[first], series[last]
    change = end - start
    if abs(change) < _TREND_MIN_CHANGE * bin_width:
        return (
            f"Across {first}-{last} the boundary mean is essentially flat ({start:.2f} to "
            f"{end:.2f}, change {change:+.2f})."
        )
    direction = "risen" if change > 0 else "fallen"
    note = profile.trend_up_note if change > 0 else profile.trend_down_note
    sentence = (
        f"Across {first}-{last} the boundary mean has {direction} from {start:.2f} to "
        f"{end:.2f} ({change:+.2f})."
    )
    return f"{sentence} {note}" if note else sentence


# --------------------------------------------------------------- composers


def compose_summary(
    index_id: str,
    year: str | int,
    mean: float | None,
    std_dev: float | None,
    minimum: float | None,
    maximum: float | None,
    bin_edges: list[float] | None,
    counts: list[int] | None,
    series: dict[str, float | None] | None = None,
    out_of_range_pixel_count: int | None = None,
) -> str:
    """Joins whichever clauses fired into one paragraph. Fixed clause order
    (level, spread, shape, sample, out-of-range share, trend) so two runs on
    the same numbers produce the same string. Prefixed "<year>: " rather
    than "In <year>, " so no clause ever has to be re-capitalised depending
    on which fired.

    `bin_width` (Wave: composite indices) is derived from the REAL bin_edges
    this call was given, not assumed to be the shared 0.1 - CMRI's own
    histogram uses 0.2-wide bins (see gee_analysis_service.py's
    `_INDEX_VALID_RANGE`), and describe_variability/describe_trend's
    thresholds need to scale with it or CMRI's variability/trend wording
    would be miscalibrated by 2x.

    `out_of_range_pixel_count` (Wave: composite indices) defaults to None -
    every pre-existing caller that doesn't pass it gets the exact same
    behavior as before (describe_out_of_range is then a strict no-op)."""
    profile = get_profile(index_id)
    bin_width = (
        bin_edges[1] - bin_edges[0] if bin_edges and len(bin_edges) > 1
        else INDEX_HISTOGRAM_BIN_WIDTH
    )
    clauses = [
        describe_level(index_id, mean),
        describe_variability(std_dev, bin_width),
        describe_spatial_outliers(index_id, minimum, maximum, bin_edges, counts),
        describe_sample(counts),
        describe_out_of_range(counts, out_of_range_pixel_count),
        describe_trend(index_id, series, bin_width),
    ]
    body = " ".join(c for c in clauses if c)
    if not body:
        return (
            f"{year}: {profile.label} produced no usable pixel statistics for this year. "
            f"{DESCRIPTIVE_ONLY_TRAILER}"
        )
    return f"{year}: {body} {DESCRIPTIVE_ONLY_TRAILER}"


def summarize_index_result(
    index_id: str,
    series: dict[str, float | None],
    distribution: dict[str, dict],
) -> str:
    """Entry point for _annual_index_series(): summarise the most recent year
    that actually produced a mean (not simply the last key - a cloud-blown
    latest year would otherwise silently produce an empty summary), then let
    compose_summary() append the whole-series trend."""
    profile = get_profile(index_id)
    years = sorted(distribution or {}, key=int)
    year = next(
        (y for y in reversed(years) if (distribution[y] or {}).get("mean") is not None), None
    )
    if year is None:
        return (
            f"{profile.label} produced no usable pixel statistics for any of the {len(years)} "
            f"years attempted - no cloud-free coverage inside the boundary. "
            f"{DESCRIPTIVE_ONLY_TRAILER}"
        )
    entry = distribution[year] or {}
    hist = entry.get("histogram") or {}
    return compose_summary(
        index_id, year,
        entry.get("mean"), entry.get("std_dev"), entry.get("min"), entry.get("max"),
        hist.get("bin_edges"), hist.get("counts"), series,
        entry.get("out_of_range_pixel_count"),
    )
