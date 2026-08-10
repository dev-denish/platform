"""
Analysis registry (Wave: GEE analysis registry).

The registry itself is static application data, not a DB table - nothing
here is ever edited at runtime, so there is no admin UI or migration for it,
just this one constant list. What IS in the DB is the per-project computed
RESULT cache (see app/repositories/analysis_results.py) - the registry only
says what analyses exist and whether each one is real yet.

Status convention: "available" is wired to a real GEEAnalysisService query
function and returns real computed data. "in-development" is registered so
the UI can list it honestly (visible, not hidden) but has no query function
behind it - the frontend shows an empty state for these, never fake data.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from app.domain.analysis_config import AnalysisConfigSpec

AnalysisStatus = Literal["available", "in-development"]
# Decided per entry from REAL measured timing (Wave: vegetation indices), not
# guessed: "sync" analyses query a pre-built GEE dataset and return in a
# normal request/response (the 5 land-cover/forest-change ones, all a few
# seconds). "async" analyses compute fresh from raw imagery (cloud-free
# compositing across multiple years) and go through the same job-queue/
# polling pattern as dataset uploads instead - measured 5-59s end-to-end
# across repeated real runs for NDVI's 2017-present series (the other 4
# vegetation indices share NDVI's exact compositing path and were spot-
# checked at the same order of magnitude, see gee_analysis_service.py), well
# past "a few seconds". Only meaningful for status="available" entries.
AnalysisExecution = Literal["sync", "async"]


class AnalysisCatalogEntry(TypedDict):
    id: str
    name: str
    category: str
    status: AnalysisStatus
    description: str
    execution: NotRequired[AnalysisExecution]
    # Wave: AOI clip / raw-imagery browsing. True only for the 3 "Raw
    # Imagery" entries below - they alone accept an optional `year` request
    # param (a real single-scene/mosaic lookup for that year, or the most
    # recent scene if omitted). The original 10 analyses stay exactly as
    # parameter-free as they've always been; this field is absent (falsy) on
    # all of them, not retrofitted.
    year_selectable: NotRequired[bool]
    # Wave: analysis config and methodology. Present only on the 7 ids with
    # something real to configure (io_lulc/modis_lulc/the 5 vegetation
    # indices) - absent (not an empty dict) on the other 6, same "NotRequired
    # means genuinely not applicable" convention `year_selectable` already
    # uses. `GEEAnalysisService._prepare_refresh` gates on `"config" in
    # entry` to decide whether to call `analysis_config.resolve_and_validate`
    # at all.
    config: NotRequired[AnalysisConfigSpec]


# All 5 vegetation indices share the exact same real config surface (same
# season window, same imagery-source/cloud-masking options, same single
# implemented combination) - a shared reference rather than 5 repeated
# literal dicts, since this is real structured config compared for equality
# elsewhere (test_analysis_catalog.py), not prose that happens to look
# similar. `year_max: None` = ask `current_veg_index_years()` live (the
# ceiling moves every year); io_lulc/modis_lulc use a static year instead.
_VEG_INDEX_CONFIG: AnalysisConfigSpec = {
    "year_mode_default": "single",
    "year_min": 2017,
    "year_max": None,
    "season_editable": True,
    "season_start_default": "02-01",
    "season_end_default": "05-31",
    "imagery_sources": ["sentinel2", "landsat8", "landsat9"],
    "cloud_masking_methods": ["cloud_score_plus", "qa_pixel", "none"],
    # Landsat needs different cloud-masking (no Cloud Score+ equivalent) and
    # risks a false trend-break at the sensor handoff year - out of scope
    # for this wave (see gee_analysis_service.py's own _annual_index_series
    # note). The UI still shows every real GEE option above; only this one
    # combination is actually implemented.
    "supported_combos": [("sentinel2", "cloud_score_plus")],
}

CATALOG: tuple[AnalysisCatalogEntry, ...] = (
    # --- Real (10 original + 3 Raw Imagery below = 13), status "available" ---
    {
        "id": "hansen_gfc",
        "name": "Global Forest Change (Hansen)",
        "category": "Forest Change",
        "status": "available",
        "execution": "sync",
        "description": (
            "Tree-cover loss/gain within the project boundary, UMD/Google/USGS/NASA. "
            "Baseline forest area applies the current forest-definition canopy-cover "
            "threshold; gain is whole-period 2000-2012 only (dataset limitation)."
        ),
    },
    {
        "id": "dynamic_world",
        "name": "Dynamic World",
        "category": "Land Cover",
        "status": "available",
        "execution": "sync",
        "description": "Near-real-time 10m land-cover class breakdown (last 12 months).",
    },
    {
        "id": "esa_worldcover",
        "name": "ESA WorldCover",
        "category": "Land Cover",
        "status": "available",
        "execution": "sync",
        "description": "10m global land-cover class breakdown, single 2021 snapshot.",
    },
    {
        "id": "io_lulc",
        "name": "10m Annual Land Cover (Impact Observatory / Esri)",
        "category": "Land Cover",
        "status": "available",
        "execution": "sync",
        "description": (
            "10m annual land-cover class breakdown for a selected year (or range), "
            "2017-2023."
        ),
        # Wave: analysis config and methodology. Every year in this range is
        # already computed unconditionally on every refresh today (real,
        # unnecessary GEE cost) - single-year default makes that opt-in.
        "config": {"year_mode_default": "single", "year_min": 2017, "year_max": 2023},
    },
    {
        "id": "modis_lulc",
        "name": "MODIS Land Cover",
        "category": "Land Cover",
        "status": "available",
        "execution": "sync",
        "description": (
            "500m annual land-cover class breakdown for a selected year (or range), "
            "2001-2023 (longest history; coarse resolution - context/trend only, not "
            "microlandscape-scale)."
        ),
        "config": {"year_mode_default": "single", "year_min": 2001, "year_max": 2023},
    },
    {
        "id": "ndvi",
        "name": "NDVI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Normalized Difference Vegetation Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "evi",
        "name": "EVI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Enhanced Vegetation Index for a selected year (or range, 2017-present) "
            "and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "savi",
        "name": "SAVI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Soil-Adjusted Vegetation Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "mndwi",
        "name": "MNDWI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Modified Normalized Difference Water Index for a selected year (or "
            "range, 2017-present) and season window, from cloud-masked Sentinel-2 "
            "composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "nbr",
        "name": "NBR",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Normalized Burn Ratio for a selected year (or range, 2017-present) and "
            "season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    # --- Raw Imagery (3), status "available" - Wave: AOI clip / raw-imagery
    # browsing. Single-scene/same-family-mosaic browsing, no cloud-masked
    # multi-image compositing, no band-math/index formula, no cross-sensor
    # math - see gee_analysis_service.py's own module docstring for why
    # these must never feed the vegetation-index compute path. `year`
    # (optional request param) picks a calendar year; omitted -> most recent
    # scene available. Distinct from the in-development `sar` entry below,
    # which is a DIFFERENT, future, COMPUTED backscatter time series, not
    # raw single-scene browsing. ---
    {
        "id": "s2_browse", "name": "Sentinel-2 True Color", "category": "Raw Imagery",
        "status": "available", "execution": "sync", "year_selectable": True,
        "description": (
            "Raw Sentinel-2 true-color (B4/B3/B2) scene, least-cloud within the "
            "selected year (2017-present). Browse only - no index math, decoupled "
            "from the vegetation-index compute path."
        ),
    },
    {
        "id": "s1_browse", "name": "Sentinel-1 Radar Backscatter", "category": "Raw Imagery",
        "status": "available", "execution": "sync", "year_selectable": True,
        "description": (
            "Raw Sentinel-1 GRD VV/VH backscatter composite (IW mode), most recent "
            "scene within the selected year (2015-present). Cloud-independent."
        ),
    },
    {
        "id": "landsat_browse", "name": "Landsat True Color", "category": "Raw Imagery",
        "status": "available", "execution": "sync", "year_selectable": True,
        "description": (
            "Raw Landsat Collection 2 Level 2 true-color scene, Landsat 8+9 combined "
            "for the shortest achievable revisit, least-cloud within the selected "
            "year (2013-present)."
        ),
    },
    # --- Deferred (5), status "in-development" ---
    {"id": "sar", "name": "SAR", "category": "Radar", "status": "in-development",
     "description": (
         "Computed Sentinel-1 radar backscatter TIME SERIES for change detection - "
         "different from the available `s1_browse` entry above, which is a raw "
         "single-scene/year browse with no time-series compositing."
     )},
    {"id": "landtrendr", "name": "LandTrendr", "category": "Forest Change",
     "status": "in-development",
     "description": "Landsat time-series disturbance/recovery trajectory analysis."},
    {"id": "canopy_density", "name": "Canopy Density", "category": "Land Cover",
     "status": "in-development",
     "description": "Sub-canopy density estimation within the forest mask."},
    {"id": "satellite_timelapse", "name": "Satellite Timelapse", "category": "Timelapse",
     "status": "in-development",
     "description": "True-color imagery timelapse over the project boundary."},
    {"id": "falsecolor_timelapse", "name": "False-color Timelapse", "category": "Timelapse",
     "status": "in-development",
     "description": "False-color (NIR) imagery timelapse over the project boundary."},
    {"id": "cultivated_area", "name": "Cultivated Area", "category": "Land Use",
     "status": "in-development", "description": "Cultivated-area detection and change."},
)

_BY_ID: dict[str, AnalysisCatalogEntry] = {e["id"]: e for e in CATALOG}

REAL_ANALYSIS_IDS: frozenset[str] = frozenset(
    e["id"] for e in CATALOG if e["status"] == "available"
)


def get_catalog_entry(analysis_id: str) -> AnalysisCatalogEntry | None:
    return _BY_ID.get(analysis_id)
