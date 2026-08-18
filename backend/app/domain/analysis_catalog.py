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
    # Wave: VNV Pipeline NDFI go-live. Which compute path owns this entry -
    # absent (falsy) means "gee", the original 18 entries' unchanged,
    # implicit default (never retrofitted onto them). Only "vnv_ndfi" below
    # sets this to "vnv_pipeline"; app/api/v1/analyses.py's refresh_analysis
    # dispatches to VNVAnalysisService instead of GEEAnalysisService when
    # this is "vnv_pipeline".
    compute_source: NotRequired[Literal["gee", "vnv_pipeline"]]


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
    # --- Real (20 original + 3 Raw Imagery below = 23), status "available" ---
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
    {
        "id": "ndwi",
        "name": "NDWI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Normalized Difference Water Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "gndvi",
        "name": "GNDVI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Green Normalized Difference Vegetation Index for a selected year (or "
            "range, 2017-present) and season window, from cloud-masked Sentinel-2 "
            "composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "ndbi",
        "name": "NDBI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Normalized Difference Built-up Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "ndmi",
        "name": "NDMI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Normalized Difference Moisture Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "lswi",
        "name": "LSWI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Land Surface Water Index for a selected year (or range, 2017-present) and "
            "season window, from cloud-masked Sentinel-2 composites. Same band math as "
            "NDMI (NIR/SWIR1), read here under its own flood/wetland-monitoring "
            "convention rather than NDMI's canopy-moisture-stress framing."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "bsi",
        "name": "BSI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Bare Soil Index for a selected year (or range, 2017-present) and season "
            "window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "arvi",
        "name": "ARVI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Atmospherically Resistant Vegetation Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "nddi",
        "name": "NDDI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Normalized Difference Drought Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites. "
            "Composite of NDVI and NDMI, computed within the same composite - no "
            "separate NDVI/NDMI run required. Computed and masked on its own [-3,3] "
            "range rather than the shared [-1,1] - the ratio's denominator can approach "
            "zero on ordinary land, not just as a rare artifact."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "cmri",
        "name": "CMRI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Combined Mangrove Recognition Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites. "
            "Composite of NDVI and NDWI (NDVI - NDWI). Its natural range ([-2,2]) is "
            "wider than every other index here, so it is computed and masked on its own "
            "[-2,2] range rather than the shared [-1,1]."
        ),
        "config": _VEG_INDEX_CONFIG,
    },
    {
        "id": "psri",
        "name": "PSRI",
        "category": "Vegetation Indices",
        "status": "available",
        "execution": "async",
        "description": (
            "Plant Senescence Reflectance Index for a selected year (or range, "
            "2017-present) and season window, from cloud-masked Sentinel-2 composites. "
            "Uses the red-edge band (B6) as its denominator, not the standard NIR band "
            "every other index here uses - Merzlyak et al. (1999)'s formula is defined "
            "on Red/Blue/RedEdge (~750nm), not Red/Blue/NIR."
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
    # --- VNV Pipeline (self-hosted compute, not GEE) - Wave: VNV Pipeline
    # NDFI go-live. `status: "available"` because this really does compute
    # and store a real result via app/services/vnv_analysis_service.py /
    # app/workers/vnv_analysis_jobs.py - "available" here means "wired to a
    # real compute path", same meaning it has for every GEE entry above, NOT
    # "validated for compliance reporting" (see its own description). Do NOT
    # add stocking_index or control_plot_matching entries here - those stay
    # frontend-only stubs, out of scope for this wave. ---
    {
        "id": "vnv_ndfi",
        "name": "NDFI — Spectral Unmixing",
        "category": "VM0047 Compute — ForesToolboxRS",
        "status": "available",
        "execution": "async",
        "compute_source": "vnv_pipeline",
        "description": (
            "Experimental — pending domain review. Normalized Difference Fraction "
            "Index unmixes each pixel into soil, vegetation, and shade fractions via "
            "the ForesToolboxRS sidecar. A confirmed methodology gap exists on "
            "forest-heavy scenes (up to 97.66% of pixels masked, near-zero Hansen "
            "correlation in testing) — not for compliance reporting."
        ),
    },
    # --- VNV Pipeline band indices (Wave: VNV band indices) - 13 direct
    # band-math indices computed from CDSEClient's existing 6-band Sentinel-2
    # AOI raster (see app/services/vnv_band_indices.py). 12 are real and
    # runnable (status: "available"); vnv_nddi is the one exception - fully
    # implemented but held at "in-development" pending a real, confirmed
    # formula-degeneracy issue (see that entry's own comment below).
    # Deliberately a DIFFERENT category from `vnv_ndfi` above (no
    # ForesToolboxRS/spectral unmixing involved at all - pure numpy
    # arithmetic, no R sidecar call) so the frontend can group them
    # separately and NOT apply vnv_ndfi's masked_fraction warning styling to
    # results that don't share its failure mode. No `config` block (unlike
    # the 5 GEE vegetation indices above) - these compute over a fixed
    # 90-day trailing window with no caller-choosable year/season, same as
    # vnv_ndfi. ---
    {
        "id": "vnv_ndvi", "name": "NDVI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Normalized Difference Vegetation Index computed directly from Sentinel-2 "
            "surface reflectance (no spectral unmixing) — a 90-day trailing composite. "
            "Experimental — pending domain review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_evi", "name": "EVI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Enhanced Vegetation Index, optimized for dense-canopy scenes — a 90-day "
            "trailing Sentinel-2 composite. Pixels where EVI's denominator is severely "
            "unstable (near zero) are masked rather than reported, but a real, smaller "
            "excursion beyond the nominal [-1,1] range can still occur (observed max "
            "~3.0 on a real scene) - see app/services/vnv_band_indices.py's evi(). "
            "Experimental — pending domain review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_savi", "name": "SAVI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Soil-Adjusted Vegetation Index, minimizing bare-soil background effects — "
            "a 90-day trailing Sentinel-2 composite. Experimental — pending domain "
            "review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_ndwi", "name": "NDWI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Normalized Difference Water Index (McFeeters) for surface-water detection — "
            "a 90-day trailing Sentinel-2 composite. Experimental — pending domain "
            "review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_mndwi", "name": "MNDWI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Modified NDWI (SWIR-based), for mangrove/tidal-wetland delineation — a "
            "90-day trailing Sentinel-2 composite. Experimental — pending domain "
            "review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_ndmi", "name": "NDMI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Normalized Difference Moisture Index (also known as NDII/LSWI — one formula, "
            "implemented once) for canopy moisture/drought stress — a 90-day trailing "
            "Sentinel-2 composite. Experimental — pending domain review. Not for "
            "compliance reporting."
        ),
    },
    {
        "id": "vnv_nbr", "name": "NBR — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Normalized Burn Ratio for burn severity / post-fire carbon-loss estimation "
            "— a 90-day trailing Sentinel-2 composite. Experimental — pending domain "
            "review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_bsi", "name": "BSI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Bare Soil Index for soil-erosion/degradation/potential-emission-zone "
            "monitoring — a 90-day trailing Sentinel-2 composite. Experimental — pending "
            "domain review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_ndbi", "name": "NDBI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Normalized Difference Built-up Index, to exclude urban pixels from carbon "
            "baselines — a 90-day trailing Sentinel-2 composite. Experimental — pending "
            "domain review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_arvi", "name": "ARVI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Atmospherically Resistant Vegetation Index, for haze/aerosol-robust "
            "vegetation monitoring — a 90-day trailing Sentinel-2 composite. Can exceed "
            "the nominal [-1,1] range on real scenes (a known characteristic of this "
            "formula, not a masking bug — see app/services/vnv_band_indices.py's arvi()). "
            "Experimental — pending domain review. Not for compliance reporting."
        ),
    },
    {
        "id": "vnv_gndvi", "name": "GNDVI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Green NDVI for chlorophyll/canopy-stress sensitivity — a 90-day trailing "
            "Sentinel-2 composite. Experimental — pending domain review. Not for "
            "compliance reporting."
        ),
    },
    # vnv_nddi is deliberately status: "in-development", unlike the other 12
    # band indices above - the formula is fully implemented and tested
    # (app/services/vnv_band_indices.py's nddi()) but confirmed, via a real
    # run over a real forest AOI, to be mathematically degenerate on
    # vegetated land: this document's own NDVI and NDWI rows are near-mirror
    # images there, so NDDI's denominator (NDVI+NDWI) sits within 0.14 of
    # zero across 99.9% of a real scene - no masking threshold produces both
    # usable coverage and sane output (see nddi()'s own docstring for the
    # exact numbers). `compute_source: "vnv_pipeline"` is kept (unlike other
    # in-development entries, which have none) so the frontend's "vnv" tab
    # still lists this honestly, muted, under the VNV Pipeline compute
    # source rather than defaulting to "gee" or disappearing entirely.
    {
        "id": "vnv_nddi", "name": "NDDI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "in-development", "compute_source": "vnv_pipeline",
        "description": (
            "Normalized Difference Drought Index (composed from this pipeline's own "
            "NDVI/NDWI) for vegetation-water interaction and drought severity. Blocked: "
            "confirmed mathematically degenerate on vegetated land using the source "
            "document's own NDWI definition (near-zero denominator scene-wide) - see "
            "app/services/vnv_band_indices.py's nddi() for the real-data evidence and the "
            "flagged conflict with outside published literature that likely resolves it."
        ),
    },
    {
        "id": "vnv_psri", "name": "PSRI — Band Math", "category": "VM0047 Compute — Band Indices",
        "status": "available", "execution": "async", "compute_source": "vnv_pipeline",
        "description": (
            "Plant Senescence Reflectance Index for growth-stage/phenology tracking — a "
            "90-day trailing Sentinel-2 composite. Experimental — pending domain "
            "review. Not for compliance reporting."
        ),
    },
)

_BY_ID: dict[str, AnalysisCatalogEntry] = {e["id"]: e for e in CATALOG}

REAL_ANALYSIS_IDS: frozenset[str] = frozenset(
    e["id"] for e in CATALOG if e["status"] == "available"
)


def get_catalog_entry(analysis_id: str) -> AnalysisCatalogEntry | None:
    return _BY_ID.get(analysis_id)
