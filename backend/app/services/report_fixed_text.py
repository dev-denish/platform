"""Fixed, code-controlled report text (Wave: 11-section report restructure).

Carbon Project Relevance and Limitations are the two sections the redesign
explicitly forbids AI (or a user prompt) from ever touching - every string
here is a literal keyed by `analysis_id`, never built from a template the
model fills in. `report_content.build_section_content` calls into this module
for BOTH report types identically; nothing downstream can make these two
sections differ between a system report and an AI report.

The same applies to `METHODOLOGY_FALLBACK` below: it is a static description
of a fixed GEE dataset (name/resolution/vintage), not a live query - these
facts change about as often as the dataset's own DOI, which is why this is a
literal dict here rather than a call into `gee_analysis_service.py`.
"""
from __future__ import annotations

from typing import Any

_DESCRIPTIVE_ONLY = (
    "Descriptive only - not a forest-definition, eligibility, additionality, "
    "baseline, leakage, permanence, uncertainty, or carbon determination."
)

# ---------------------------------------------------------- Carbon Project Relevance (section 9)

_BROWSE_RELEVANCE = (
    "A raw satellite scene of the project boundary can support visual screening and "
    "field-verification planning - spotting obvious access changes, clearing, or "
    "flooding worth a closer look before it shows up in a computed analysis. "
    + _DESCRIPTIVE_ONLY
)

_ANNUAL_LULC_RELEVANCE = (
    "This annual land-cover classification can support monitoring of land-use change "
    "within the project boundary over time, and may assist with screening areas for "
    "further review against the project's activities. It does not itself determine "
    "forest definition, eligibility, or land-use change for accounting purposes. "
    + _DESCRIPTIVE_ONLY
)

# The 12 vnv_* band-math indices (Wave: VNV band indices) share this same
# closing frame regardless of which formula each one's own opening sentence
# describes - computed by the self-hosted VNV Pipeline directly from
# Sentinel-2 band math (see app/services/vnv_band_indices.py). `vnv_ndfi`
# (spectral unmixing, a confirmed masking failure mode) is deliberately NOT
# built from this suffix - see its own bespoke entry below.
#
# carbon-mrv-vm0047 review (2026-08-19) corrected this from an earlier
# "the equivalent Earth Engine-based index above" phrasing: "above" is
# positional and false (report sections render in caller-supplied order,
# report_service.py, not this dict's order - a VNV-only report has no GEE
# section to point at), and "equivalent" overstates - the two paths differ
# in compositing (this one: a 90-day trailing, least-cloud-first, up-to-4-
# scene mosaic), cloud masking (SCL classes, not Cloud Score+), and offset
# handling, so even the best-agreeing pair (vnv_ndvi vs ndvi, 5% apart on a
# real identical AOI/date test) isn't the same computation. vnv_psri's own
# entry goes further still - see its own note on why it isn't comparable to
# GEE psri at all, not just numerically different.
_VNV_BAND_INDEX_RELEVANCE_SUFFIX = (
    " Computed by a separately implemented, self-hosted VNV Pipeline directly from "
    "Sentinel-2 band math (no spectral unmixing) - not the same computation as the "
    "similarly named Earth Engine index elsewhere in this catalog; absolute values "
    "from the two are not interchangeable and should not be combined into a single "
    "time series. Experimental - pending domain review. " + _DESCRIPTIVE_ONLY
)

CARBON_RELEVANCE: dict[str, str] = {
    "hansen_gfc": (
        "Global Forest Change data can support monitoring of tree-cover loss and gain "
        "within the project boundary, providing spatial evidence that may assist with "
        "screening areas for further review. It does not itself determine forest "
        "definition, eligibility, additionality, or carbon stock/removal. " + _DESCRIPTIVE_ONLY
    ),
    "dynamic_world": (
        "This near-real-time land-cover classification can support rapid screening of "
        "the project boundary for gross land-cover change between updates. It is not a "
        "substitute for a validated baseline or monitoring land-cover map. " + _DESCRIPTIVE_ONLY
    ),
    "esa_worldcover": (
        "This land-cover snapshot can support a one-time baseline characterisation of "
        "the project boundary's land-cover composition. As a single 2021 snapshot it "
        "cannot itself support change monitoring. " + _DESCRIPTIVE_ONLY
    ),
    "io_lulc": _ANNUAL_LULC_RELEVANCE,
    "modis_lulc": _ANNUAL_LULC_RELEVANCE,
    "ndvi": (
        "NDVI can support vegetation monitoring within the project boundary by "
        "providing spatial evidence of vegetation conditions and changes over time. "
        "The analysis may assist with monitoring and screening areas requiring further "
        "review. NDVI alone does not determine forest definition, project eligibility, "
        "additionality, baseline, leakage, permanence, uncertainty, carbon stock or "
        "carbon removal."
    ),
    "evi": (
        "EVI can support vegetation monitoring in areas with dense canopy or atmospheric "
        "interference, where it is less prone to saturation than NDVI, providing "
        "spatial evidence of vegetation conditions and changes over time that may "
        "assist with screening areas for further review. " + _DESCRIPTIVE_ONLY
    ),
    "savi": (
        "SAVI can support vegetation monitoring in areas of sparse canopy or exposed "
        "soil, where its soil-brightness correction gives a more reliable read on "
        "vegetation conditions than NDVI, providing spatial evidence that may assist "
        "with screening areas for further review. " + _DESCRIPTIVE_ONLY
    ),
    "mndwi": (
        "MNDWI can support monitoring of standing water and boundary hydrology "
        "relevant to project planning, providing spatial evidence that may assist with "
        "screening areas for further review. It is not a vegetation or carbon "
        "indicator. " + _DESCRIPTIVE_ONLY
    ),
    "nbr": (
        "NBR can support monitoring of fire and disturbance within the project "
        "boundary by highlighting NIR/SWIR contrast consistent with recently burned or "
        "recovering surfaces, providing spatial evidence that may assist with "
        "screening areas for further review. " + _DESCRIPTIVE_ONLY
    ),
    "s2_browse": _BROWSE_RELEVANCE,
    "s1_browse": (
        "A raw radar scene of the project boundary can support visual screening "
        "independent of cloud cover, useful for field-verification planning in "
        "persistently cloudy seasons. " + _DESCRIPTIVE_ONLY
    ),
    "landsat_browse": _BROWSE_RELEVANCE,
    "ndwi": (
        "NDWI can support monitoring of surface water bodies and boundary hydrology "
        "within the project boundary, providing spatial evidence that may assist with "
        "screening areas for further review. It is not a carbon indicator, and over "
        "vegetated land its signal is the inverse of GNDVI's. " + _DESCRIPTIVE_ONLY
    ),
    "gndvi": (
        "GNDVI can support vegetation monitoring within the project boundary, using "
        "the green band's chlorophyll sensitivity to provide spatial evidence of "
        "vegetation conditions that may assist with screening areas for further "
        "review. " + _DESCRIPTIVE_ONLY
    ),
    "ndbi": (
        "NDBI can support screening for built-up or impervious surface within the "
        "project boundary, providing spatial evidence that may assist with detecting "
        "encroachment worth further review. It does not itself determine land-use "
        "change for accounting purposes, and it is not a vegetation or carbon "
        "indicator. " + _DESCRIPTIVE_ONLY
    ),
    "ndmi": (
        "NDMI can support monitoring of vegetation canopy water content within the "
        "project boundary, providing spatial evidence of moisture-stress conditions "
        "that may assist with screening areas for further review. " + _DESCRIPTIVE_ONLY
    ),
    "lswi": (
        "LSWI can support monitoring of flooding and wetland conditions within the "
        "project boundary, providing spatial evidence that may assist with screening "
        "areas for further review. It is not a carbon indicator, and it responds to "
        "canopy water content as well as standing water. " + _DESCRIPTIVE_ONLY
    ),
    "bsi": (
        "BSI can support screening for bare or exposed soil within the project "
        "boundary, providing spatial evidence that may assist with detecting clearing "
        "or loss of vegetative cover worth further review. It is not a carbon "
        "indicator. " + _DESCRIPTIVE_ONLY
    ),
    "arvi": (
        "ARVI can support vegetation monitoring within the project boundary with "
        "reduced sensitivity to atmospheric haze and aerosols compared to NDVI, "
        "providing spatial evidence that may assist with screening areas for further "
        "review. " + _DESCRIPTIVE_ONLY
    ),
    "nddi": (
        "NDDI can support drought screening within the project boundary by combining "
        "vegetation and moisture signal into a single index, providing spatial "
        "evidence that may assist with screening areas for further review. It does "
        "not itself quantify drought severity or impact. " + _DESCRIPTIVE_ONLY
    ),
    "cmri": (
        "CMRI can support screening for mangrove-consistent vegetation within the "
        "project boundary, providing spatial evidence that may assist with screening "
        "areas for further review. It does not itself confirm mangrove presence or "
        "extent. " + _DESCRIPTIVE_ONLY
    ),
    "psri": (
        "PSRI can support screening for plant senescence or stress within the project "
        "boundary, providing spatial evidence that may assist with screening areas for "
        "further review. " + _DESCRIPTIVE_ONLY
    ),
    "vnv_ndfi": (
        "NDFI (spectral unmixing into soil/vegetation/shade fractions, via the "
        "self-hosted VNV Pipeline's ForesToolboxRS sidecar) is intended to support "
        "degradation screening within the project boundary, but a confirmed "
        "methodology gap - up to 97.66% of pixels masked on forest-heavy scenes in "
        "testing, with near-zero correlation to Hansen Global Forest Change - means "
        "it does not currently provide usable spatial evidence here. Experimental - "
        "pending domain review. " + _DESCRIPTIVE_ONLY
    ),
    "vnv_ndvi": (
        "NDVI can support vegetation monitoring within the project boundary by "
        "providing spatial evidence of vegetation conditions and changes over time."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_evi": (
        "EVI can support vegetation monitoring in areas with dense canopy, where it "
        "is less prone to saturation than NDVI."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_savi": (
        "SAVI can support vegetation monitoring in areas of sparse canopy or exposed "
        "soil, where its soil-brightness correction gives a more reliable read than "
        "NDVI."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_ndwi": (
        "NDWI can support monitoring of surface water bodies and boundary hydrology "
        "within the project boundary. It is not a carbon indicator, and its band "
        "pair is the exact negation of vnv_gndvi's, not just an inverse over "
        "vegetated land."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_mndwi": (
        "MNDWI can support monitoring of standing water and boundary hydrology "
        "relevant to project planning. It is not a vegetation or carbon indicator."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_ndmi": (
        "NDMI can support monitoring of vegetation canopy water content within the "
        "project boundary, providing spatial evidence of moisture-stress conditions. "
        "It uses the same NIR/SWIR1 bands as vnv_ndbi, negated, so the two are one "
        "measurement reported two ways, not independent evidence."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_nbr": (
        "NBR can support monitoring of fire and disturbance within the project "
        "boundary by highlighting NIR/SWIR contrast consistent with recently burned "
        "or recovering surfaces."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_bsi": (
        "BSI can support screening for bare or exposed soil within the project "
        "boundary, providing spatial evidence that may assist with detecting "
        "clearing or loss of vegetative cover worth further review. It is not a "
        "carbon indicator."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_ndbi": (
        "NDBI can support screening for built-up or impervious surface within the "
        "project boundary, providing spatial evidence that may assist with detecting "
        "encroachment worth further review. It does not itself determine land-use "
        "change for accounting purposes, and it is not a vegetation or carbon "
        "indicator. It uses the same NIR/SWIR1 bands as vnv_ndmi, negated, so the two "
        "are one measurement reported two ways, not independent evidence."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_arvi": (
        "ARVI can support vegetation monitoring with reduced sensitivity to "
        "atmospheric haze and aerosols compared to NDVI. Real-data testing found "
        "this formula's `2 x Red - Blue` term can legitimately go negative on "
        "ordinary land, which pushes values above the usual [-1, 1] range more "
        "often than NDVI's - see this analysis's own Limitations section."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_gndvi": (
        "GNDVI can support vegetation monitoring using the green band's chlorophyll "
        "sensitivity. Its band pair is the exact negation of vnv_ndwi's, so the two "
        "carry the same information with the sign reversed."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
    "vnv_psri": (
        "PSRI can support screening for plant senescence or stress within the "
        "project boundary."
    ) + _VNV_BAND_INDEX_RELEVANCE_SUFFIX,
}


def carbon_project_relevance(analysis_id: str) -> str:
    return CARBON_RELEVANCE[analysis_id]


# --------------------------------------------------------------- Limitations (section 11)

# Analyses whose real `stats["note"]` (see gee_analysis_service.py's own
# construction sites) is genuinely a dataset CAVEAT - not a methodology
# description - and can be reused verbatim as this section's content, e.g.
# hansen_gfc's "Gain is a whole-period 2000-2012 figure only" or
# esa_worldcover's "Single 2021 snapshot, not a time series."
_REUSE_NOTE_AS_LIMITATIONS: frozenset[str] = frozenset({
    "hansen_gfc", "esa_worldcover", "io_lulc", "modis_lulc",
    "s2_browse", "s1_browse", "landsat_browse",
})

# The remaining ids: dynamic_world has no `note` key at all
# (gee_analysis_service.py never sets one for it), and every vegetation
# index's `note` is methodology PROSE (season window, masking) rather than a
# limitation - so these get genuinely authored text instead of a reused field.

# Shared base for the 12 vnv_* band-math indices (Wave: VNV band indices) -
# same "not GEE, fixed 90-day window" framing regardless of formula; each
# entry above prepends its own real, index-specific caveat (if any) to this.
#
# carbon-mrv-vm0047 review (2026-08-19) added two corrections here:
# (1) unlike every GEE vegetation index, this compute path applies NO
#     value-range mask at all (see EXPECTED_RANGES's own docstring in
#     vnv_band_indices.py: "not enforced or clamped anywhere") - the worker
#     (vnv_analysis_jobs.py) reports flat min/max/mean over every finite
#     pixel, so an out-of-range value (see vnv_evi/vnv_arvi's own entries)
#     IS included in the reported mean, unlike the GEE indices' masked one.
# (2) the BOA_ADD_OFFSET correction is a HARDCODED constant (-1000 raw DN),
#     not read per-scene from each scene's own STAC metadata - it was
#     verified against real CDSE STAC raster:scale/raster:offset fields for
#     the scenes actually tested, and is only valid for Sentinel-2
#     Processing Baseline >=04.00 (true for this pipeline's fixed 90-day
#     trailing window, which can never reach further back than that
#     baseline's 2022-01-25 cutover - see vnv_band_indices.py's own
#     `_BOA_ADD_OFFSET_REFLECTANCE` comment). The original wording here
#     ("verified against real CDSE STAC asset metadata") could be misread
#     as a live, per-scene lookup; it is not.
_VNV_BAND_INDEX_LIMITATIONS = (
    "Computed from a 90-day trailing Sentinel-2 composite (least-cloud-first mosaic "
    "of up to 4 scenes) via the self-hosted VNV Pipeline, not a caller-choosable "
    "season/year window like the Earth Engine version of this index. No value-range "
    "mask is applied on this path - out-of-range pixels (if any for this index) are "
    "retained in the reported min/max/mean, unlike the Earth Engine version, which "
    "masks and excludes them. Reflectance is corrected using the fixed Sentinel-2 "
    "L2A Processing Baseline >=04.00 BOA_ADD_OFFSET (-1000 digital numbers), verified "
    "against real CDSE STAC asset metadata for the scenes tested, before this formula "
    "is applied. Experimental, pending domain review - not for compliance reporting."
)

_AUTHORED_LIMITATIONS: dict[str, str] = {
    "dynamic_world": (
        "Reflects a rolling 12-month composite, not a fixed calendar year - two "
        "reports generated months apart may draw on different underlying imagery "
        "windows even if nothing on the ground changed. Not designed for year-over-"
        "year comparison; use a fixed-vintage dataset (e.g. ESA WorldCover) for that."
    ),
    "ndvi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year."
    ),
    # FOLLOW-UP (not urgent, carbon-mrv-vm0047 review, 2026-08-18): like arvi/psri
    # below, "[-1, 1]" here is an applied clamp, not a mathematical bound - EVI's
    # denominator (NIR + 6*Red - 7.5*Blue + 1) can approach zero, so out-of-range
    # pixels are possible the same way they are for arvi/psri. This entry predates
    # the arvi/psri fix and was deliberately left unchanged then; when picked up,
    # apply the same "applied default rather than a mathematical bound ... check the
    # out-of-range pixel count before reading the mean" wording those two use.
    "evi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year."
    ),
    "savi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year."
    ),
    "mndwi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year."
    ),
    "nbr": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year. Published "
        "burn-severity classes are defined on a pre/post-fire difference (dNBR), not on "
        "the single-date value shown here."
    ),
    "ndwi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year. Its band "
        "pair is the exact negation of GNDVI's (Green/NIR), so NDWI and GNDVI carry the "
        "same information with the sign reversed, not two independent lines of "
        "evidence."
    ),
    "gndvi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year. Its band "
        "pair is the exact negation of NDWI's, so the two carry the same information "
        "with the sign reversed."
    ),
    "ndbi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year. Uses the "
        "same NIR/SWIR1 bands as NDMI and LSWI, negated, so those three indices are one "
        "measurement reported three ways, not three independent lines of evidence."
    ),
    "ndmi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year. Uses the "
        "same NIR/SWIR1 band math as LSWI and the exact negation of NDBI's, so those "
        "three indices are one measurement reported three ways, not three independent "
        "lines of evidence."
    ),
    "lswi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year. Produces "
        "numerically identical values to NDMI (same NIR/SWIR1 band math) and the exact "
        "negation of NDBI's, so those three indices are one measurement reported three "
        "ways, not three independent lines of evidence."
    ),
    "bsi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; index values outside the valid [-1, 1] range are "
        "masked and excluded from the boundary statistics, and years with no cloud-"
        "free coverage in the season window produce no result for that year."
    ),
    # FOLLOW-UP (not urgent, carbon-mrv-vm0047 review, 2026-08-19): "its
    # denominator can go negative and cross zero" below describes the wrong
    # mechanism - with denominator NIR+(2*Red-Blue), a NEGATIVE (2*Red-Blue)
    # term makes the numerator EXCEED a still-positive denominator, pushing
    # the ratio above +1 without the denominator ever crossing zero (worked
    # against the VNV sibling's own real numbers: NIR~0.30, term~-0.08 ->
    # 0.38/0.22 = 1.73, denominator never near zero). See vnv_arvi's own,
    # corrected entry for the right mechanism and wording. Left unchanged
    # here rather than silently edited, same convention the evi follow-up
    # above already uses.
    "arvi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; years with no cloud-free coverage in the season "
        "window produce no result for that year. Masked to the shared [-1, 1] range, "
        "which for this index is an applied default rather than a mathematical bound - "
        "its denominator can go negative and cross zero (e.g. over water or deep "
        "shadow) - so the out-of-range pixel count should be checked before reading "
        "the mean."
    ),
    "nddi": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; computed and masked on its own [-3, 3] range and "
        "excluded from the boundary statistics outside it, and years with no cloud-"
        "free coverage in the season window produce no result for that year. The "
        "[-3, 3] bound is empirical, not mathematical (its NDVI/NDMI ratio can approach "
        "a zero denominator on ordinary land) and no near-zero-denominator guard is "
        "applied, so extreme values can still fall inside the range and influence the "
        "boundary mean. The distribution chart's fixed 20 bins are also 3x wider here "
        "than for a [-1, 1] index."
    ),
    "cmri": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; computed and masked on its own [-2, 2] range and "
        "excluded from the boundary statistics outside it, and years with no cloud-"
        "free coverage in the season window produce no result for that year. [-2, 2] is "
        "wider than the shared [-1, 1] the other indices use, but is CMRI's exact "
        "mathematical range (a plain difference of two [-1, 1] indices, NDVI - NDWI), "
        "not an empirical bound. The distribution chart's fixed 20 bins are also 2x "
        "wider here than for a [-1, 1] index."
    ),
    "psri": (
        "Sentinel-2-only implementation, restricted to the configured season window "
        "within each requested year; years with no cloud-free coverage in the season "
        "window produce no result for that year. Uses the red-edge band (B6) as its "
        "denominator rather than the standard NIR band every other index here uses. "
        "Masked to the shared [-1, 1] range, which for this index is an applied "
        "default rather than a mathematical bound - it is a plain ratio whose "
        "denominator can approach zero - so the out-of-range pixel count should be "
        "checked before reading the mean."
    ),
    "vnv_ndfi": (
        "Self-hosted VNV Pipeline compute (ForesToolboxRS R sidecar), not Google "
        "Earth Engine - confirmed in testing to mask up to 97.66% of pixels on "
        "forest-heavy scenes, with near-zero correlation to Hansen Global Forest "
        "Change. A 90-day trailing composite with no caller-choosable season/year. "
        "Experimental, pending domain review - not for compliance reporting."
    ),
    "vnv_ndvi": _VNV_BAND_INDEX_LIMITATIONS,
    "vnv_evi": (
        "A near-zero-denominator guard converts the most unstable pixels to no-data "
        "(masking ~0.1% of a real test AOI, 205/241194 pixels) rather than reporting "
        "them, which shows up here as a slightly lower coverage percentage, not as a "
        "separate count - this analysis has no distribution/histogram breakdown. A "
        "smaller residual excursion beyond the documented [-1, 1] range can still "
        "occur even after the guard (observed max ~3.0 on that same test AOI). "
        + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_savi": (
        "Its x1.5 soil-brightness factor gives it a mathematical range of [-1.5, "
        "1.5], wider than the shared [-1, 1] most other indices here use - not an "
        "error, just this formula's own exact range. " + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_ndwi": (
        "Its band pair is the exact negation of vnv_gndvi's (Green/NIR), so the two "
        "carry the same information with the sign reversed, not two independent "
        "lines of evidence. " + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_mndwi": _VNV_BAND_INDEX_LIMITATIONS,
    "vnv_ndmi": (
        "Uses the same NIR/SWIR1 bands as vnv_ndbi, negated, so the two are one "
        "measurement reported two ways, not independent evidence. "
        + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_nbr": (
        "Published burn-severity classes are defined on a pre/post-fire difference "
        "(dNBR), not on the single-date value shown here - and unlike the Earth "
        "Engine version, this compute path has no caller-choosable year, so it "
        "cannot construct a pre/post-fire pairing at all on its own. "
        + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_bsi": _VNV_BAND_INDEX_LIMITATIONS,
    "vnv_ndbi": (
        "Uses the same NIR/SWIR1 bands as vnv_ndmi, negated, so the two are one "
        "measurement reported two ways, not independent evidence. "
        + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_arvi": (
        "Can exceed the documented [-1, 1] range on real scenes (observed max 1.74 "
        "on a real test AOI) - a genuine characteristic of this formula, not a "
        "divide-by-near-zero bug: the denominator itself stays comfortably positive "
        "(observed 0.09-0.89 on that same AOI), but its `2 x Red - Blue` term can go "
        "negative on ordinary land, which makes the numerator exceed the denominator "
        "and pushes the ratio above +1. Nothing is masked here (see this analysis's "
        "own note on why no epsilon threshold both bounds it and preserves coverage), "
        "so the reported max already reflects the full excursion - there is no "
        "separate out-of-range count to check. " + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_gndvi": (
        "Its band pair is the exact negation of vnv_ndwi's, so the two carry the "
        "same information with the sign reversed, not two independent lines of "
        "evidence. " + _VNV_BAND_INDEX_LIMITATIONS
    ),
    "vnv_psri": (
        "Uses the standard NIR band as its denominator - this is the reference "
        "document's own specified formula for this pipeline, not a fallback; the "
        "6-band Sentinel-2 raster this pipeline fetches has no red-edge band "
        "available regardless. The Earth Engine version of PSRI uses the red-edge "
        "band (B6) instead, a genuinely different denominator - the two are "
        "different formulas, not the same index on a different compute path, and "
        "their values are not comparable in any sense. It is a plain, unguarded "
        "ratio (see vnv_band_indices.py's psri()) whose denominator can approach "
        "zero over dark targets. " + _VNV_BAND_INDEX_LIMITATIONS
    ),
}

_NO_LIMITATIONS_RECORDED = "No dataset-specific limitations recorded for this analysis."


def limitations_text(analysis_id: str, note: str | None) -> str:
    if analysis_id in _REUSE_NOTE_AS_LIMITATIONS:
        return note or _NO_LIMITATIONS_RECORDED
    return _AUTHORED_LIMITATIONS.get(analysis_id, _NO_LIMITATIONS_RECORDED)


# ------------------------------------------------- Methodology fallback (sections 2 & 3)

# For the ids with no real `stats["methodology"]` dict (hansen_gfc,
# dynamic_world, esa_worldcover, the 3 browse types) - same field names the
# real dict uses (`_land_cover_methodology`/`_veg_index_methodology` in
# gee_analysis_service.py), so `methodology_text`/`data_processing_text` below
# treat both sources uniformly.
#
# carbon-mrv-vm0047 review (2026-08-19): the VNV Pipeline's own real job
# stats (run_vnv_band_index_analysis, vnv_analysis_jobs.py) carry no
# `methodology` dict either, and had NO fallback entry here - so
# `data_processing_text` fell through to its own generic "Processed
# directly from the source dataset with no additional compositing" line,
# which is flatly false for a 90-day, up-to-4-scene composite (contradicts
# the Limitations text in the SAME report). Deliberately no
# `valid_range` key on any of these 13 - `methodology_text` hardcodes
# "values outside this range are masked" for that key, which is true for
# every GEE entry but false here (see `_VNV_BAND_INDEX_LIMITATIONS`'s own
# "no value-range mask is applied" correction) - adding one would
# reintroduce the exact bug this fallback is fixing.
#
# carbon-mrv-vm0047 review, report-generation fix pass: every VNV entry below
# was ALSO missing a `cloud_masking` key, even though `cdse_ingestion.py`'s
# `_process_s2_scene` genuinely applies real SCL-based masking before this
# path ever sees a pixel (`_INVALID_SCL_CLASSES = {0, 1, 3, 8, 9, 10}` - see
# that module's own comment for exactly which classes and why) - so
# `data_quality_text`'s "Cloud-affected pixels were masked before computing
# statistics." sentence never appeared for any VNV analysis, silently
# under-disclosing real masking that did happen.
#
# carbon-mrv-vm0047 review (M2): the first version of this text named only
# the excluded classes plus 2 retained ones (dark-area, snow), which read as
# though everything else was excluded - not true, and with no numeric SCL
# ids an auditor had no way to verify exactly which classes were meant (SCL
# class names/definitions have shifted across ESA product baselines). Now
# names every excluded AND every retained class by its numeric SCL id
# (matching `_INVALID_SCL_CLASSES` in cdse_ingestion.py exactly), and states
# the real consequence of retaining class 7 (unclassified - cloud-adjacent in
# some ESA SCL baseline docs, previously undisclosed) rather than only the
# two classes retained for a stated reason.
_VNV_CLOUD_MASKING = (
    "Sentinel-2 Scene Classification Layer (SCL)-based per-pixel masking, applied to "
    "each scene before compositing. Excluded: SCL 0 (no data), 1 (saturated or "
    "defective), 3 (cloud shadow), 8 (cloud medium probability), 9 (cloud high "
    "probability), 10 (thin cirrus). Retained: SCL 2 (dark area), 4 (vegetation), "
    "5 (bare soil), 6 (water), 7 (unclassified), 11 (snow/ice) - classes 2 and 11 are "
    "retained deliberately, since genuinely dark or snow-covered ground is real observed "
    "data and discarding it would bias a vegetation time series toward clear, sunlit "
    "conditions only; the consequence is that shadow or low-confidence cloud the SCL did "
    "not label as class 3 or class 8/9 can remain in the composite and depress index "
    "values locally."
)

METHODOLOGY_FALLBACK: dict[str, dict[str, Any]] = {
    "hansen_gfc": {
        "dataset": "UMD/Google/USGS/NASA Global Forest Change v1.11",
        "resolution_m": 30,
        "years_available": "2000-2023 (loss), 2000-2012 (gain)",
    },
    "dynamic_world": {
        "dataset": "Dynamic World V1 (Google/WRI/National Geographic Society)",
        "resolution_m": 10,
        "years_available": "rolling 12-month window",
    },
    "esa_worldcover": {
        "dataset": "ESA WorldCover v200",
        "resolution_m": 10,
        "years_available": "2021",
    },
    "s2_browse": {
        "dataset": "Sentinel-2 Surface Reflectance Harmonized (COPERNICUS/S2_SR_HARMONIZED)",
        "resolution_m": 10,
        "years_available": "2017-present",
    },
    "s1_browse": {
        "dataset": "Sentinel-1 GRD, IW mode, VV/VH backscatter",
        "resolution_m": 10,
        "years_available": "2015-present",
    },
    "landsat_browse": {
        "dataset": "Landsat 8/9 Collection 2 Level 2",
        "resolution_m": 30,
        "years_available": "2013-present",
    },
    "vnv_ndfi": {
        "dataset": "Sentinel-2 L2A (via CDSE, not Earth Engine) -> ForesToolboxRS spectral unmixing",
        "resolution_m": 10,
        "imagery_source": "Sentinel-2 (self-hosted VNV Pipeline, via CDSE)",
        "season_window": "90-day trailing composite, no caller-choosable window",
        "cloud_masking": _VNV_CLOUD_MASKING,
    },
    **{
        f"vnv_{name}": {
            "dataset": "Sentinel-2 L2A (via CDSE, not Earth Engine)",
            "resolution_m": 10,
            "imagery_source": "Sentinel-2 (self-hosted VNV Pipeline, via CDSE)",
            "season_window": "90-day trailing composite, no caller-choosable window",
            "cloud_masking": _VNV_CLOUD_MASKING,
        }
        for name in (
            "ndvi", "evi", "savi", "ndwi", "mndwi", "ndmi",
            "nbr", "bsi", "ndbi", "arvi", "gndvi", "psri",
        )
    },
}


def methodology_dict(analysis_id: str, methodology: dict[str, Any] | None) -> dict[str, Any]:
    """Falls back to `{}` (not a `KeyError`) for an id with neither a real
    `stats["methodology"]` dict nor a fallback entry - e.g. a legacy stored
    result from before the methodology dict existed, or a test fixture that
    only cares about a different field. `methodology_text`/
    `data_processing_text` below both already treat "field absent" as "say
    nothing about it", so an empty dict degrades gracefully rather than
    crashing the whole report.

    Public (not `_`-prefixed): `report_content.py`'s `has_cloud_masking` flag
    must resolve through this SAME fallback, not just re-read the raw
    `stats.get("methodology")` - see that call site's own comment for the
    bug this fixes (VNV's real stats never carry a `methodology` key, so the
    raw check silently never fired even once `METHODOLOGY_FALLBACK` gained a
    `cloud_masking` entry for VNV)."""
    if methodology is not None:
        return methodology
    return METHODOLOGY_FALLBACK.get(analysis_id, {})


def methodology_text(
    analysis_id: str, description: str, methodology: dict[str, Any] | None
) -> str:
    """'What is being measured' - the catalog description plus whichever of
    formula/valid_range (vegetation indices) or dataset identity/resolution
    (everything else) the source dict carries."""
    m = methodology_dict(analysis_id, methodology)
    parts = [description]
    if "formula" in m:
        parts.append(f"Formula: {m['formula']}.")
    if "valid_range" in m:
        lo, hi = m["valid_range"]
        parts.append(f"Valid range: {lo} to {hi}; values outside this range are masked.")
    if "dataset" in m:
        parts.append(f"Dataset: {m['dataset']}.")
    if "resolution_m" in m:
        parts.append(f"Native resolution: {m['resolution_m']} m.")
    return " ".join(parts)


def data_processing_text(analysis_id: str, methodology: dict[str, Any] | None) -> str:
    """'What data and processing were used' - imagery source/cloud masking/
    season window/computed years (vegetation indices), or years computed vs
    available (annual land cover), or years available alone (the 6 fallback
    types, which have no per-request processing to describe)."""
    m = methodology_dict(analysis_id, methodology)
    parts = []
    if "imagery_source" in m:
        parts.append(f"Imagery source: {m['imagery_source']}.")
    if "cloud_masking" in m:
        parts.append(f"Cloud masking: {m['cloud_masking']}.")
    if "season_window" in m:
        parts.append(f"Season window: {m['season_window']}.")
    if "years_computed" in m:
        years = m["years_computed"]
        years_str = ", ".join(str(y) for y in years) if isinstance(years, list) else str(years)
        parts.append(f"Years computed: {years_str}.")
    if "years_available" in m:
        parts.append(f"Dataset years available: {m['years_available']}.")
    if not parts:
        parts.append("Processed directly from the source dataset with no additional compositing.")
    return " ".join(parts)


# ------------------------------------------------------------- Data Quality (section 10)


def data_quality_text(
    coverage_pct: float | None,
    *,
    out_of_range_pixel_count: int | None = None,
    has_cloud_masking: bool = False,
) -> str:
    """Deterministic, never AI-decided - built only from fields already on the
    stored result (coverage, masking presence, out-of-range pixel count).

    carbon-mrv-vm0047 review (M1): the cloud-masking sentence must NOT be
    gated behind `coverage_pct is not None` - `vnv_ndfi`'s real stats dict
    (built from the R sidecar's own payload, see vnv_analysis_jobs.py) has no
    `coverage_pct` key at all, so the old early-return on `coverage_pct is
    None` silently dropped the masking disclosure for it even after
    `has_cloud_masking` started correctly resolving True via
    `METHODOLOGY_FALLBACK["vnv_ndfi"]["cloud_masking"]`. Coverage and masking
    are two independent facts about the same result; one being unknown must
    not suppress the other."""
    parts: list[str] = []
    if coverage_pct is None:
        parts.append("Coverage of the project boundary was not recorded for this analysis.")
    else:
        parts.append(f"Coverage: {coverage_pct:.1f}% of the project boundary area produced a valid value.")
    if has_cloud_masking:
        parts.append("Cloud-affected pixels were masked before computing statistics.")
    if out_of_range_pixel_count is not None:
        parts.append(
            f"{out_of_range_pixel_count:,} pixel(s) fell outside the valid range and were "
            "excluded from the statistics above."
        )
    return " ".join(parts)
