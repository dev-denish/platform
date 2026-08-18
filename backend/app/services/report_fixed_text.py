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

# The remaining 6 ids: dynamic_world has no `note` key at all
# (gee_analysis_service.py never sets one for it), and the 5 vegetation
# indices' `note` is methodology PROSE (season window, masking) rather than a
# limitation - so these get genuinely authored text instead of a reused field.
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
}

_NO_LIMITATIONS_RECORDED = "No dataset-specific limitations recorded for this analysis."


def limitations_text(analysis_id: str, note: str | None) -> str:
    if analysis_id in _REUSE_NOTE_AS_LIMITATIONS:
        return note or _NO_LIMITATIONS_RECORDED
    return _AUTHORED_LIMITATIONS.get(analysis_id, _NO_LIMITATIONS_RECORDED)


# ------------------------------------------------- Methodology fallback (sections 2 & 3)

# For the 6 ids with no real `stats["methodology"]` dict (hansen_gfc,
# dynamic_world, esa_worldcover, the 3 browse types) - same field names the
# real dict uses (`_land_cover_methodology`/`_veg_index_methodology` in
# gee_analysis_service.py), so `methodology_text`/`data_processing_text` below
# treat both sources uniformly.
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
}


def _methodology_dict(analysis_id: str, methodology: dict[str, Any] | None) -> dict[str, Any]:
    """Falls back to `{}` (not a `KeyError`) for an id with neither a real
    `stats["methodology"]` dict nor a fallback entry - e.g. a legacy stored
    result from before the methodology dict existed, or a test fixture that
    only cares about a different field. `methodology_text`/
    `data_processing_text` below both already treat "field absent" as "say
    nothing about it", so an empty dict degrades gracefully rather than
    crashing the whole report."""
    if methodology is not None:
        return methodology
    return METHODOLOGY_FALLBACK.get(analysis_id, {})


def methodology_text(
    analysis_id: str, description: str, methodology: dict[str, Any] | None
) -> str:
    """'What is being measured' - the catalog description plus whichever of
    formula/valid_range (vegetation indices) or dataset identity/resolution
    (everything else) the source dict carries."""
    m = _methodology_dict(analysis_id, methodology)
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
    m = _methodology_dict(analysis_id, methodology)
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
    stored result (coverage, masking presence, out-of-range pixel count)."""
    if coverage_pct is None:
        return "Coverage could not be determined for this analysis."
    parts = [f"Coverage: {coverage_pct:.1f}% of the project boundary."]
    if has_cloud_masking:
        parts.append("Cloud-affected pixels were masked before computing statistics.")
    if out_of_range_pixel_count is not None:
        parts.append(
            f"{out_of_range_pixel_count:,} pixel(s) fell outside the valid range and were "
            "excluded from the statistics above."
        )
    return " ".join(parts)
