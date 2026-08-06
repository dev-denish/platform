"""
GEE-backed analysis queries (Wave: GEE analysis registry).

Five analyses are wired to real GEE datasets here - simple, synchronous
dataset queries against a project's boundary, fast enough for a normal
request/response (no job queue). Every other catalog entry
(app/domain/analysis_catalog.py) is "in-development": no query function
exists for it, and refresh() rejects it rather than pretend to compute one.

Each query function builds its ENTIRE computation graph server-side and
calls .getInfo() exactly once (or, for the two annual analyses, once for
ALL years combined) - never inside a Python loop - matching the convention
already established in scripts/gee_phase1_agb_proxy.py.

Four independent classification schemes are used across these analyses
(Dynamic World's 9 classes, ESA WorldCover's 11, Esri/Impact Observatory's
~9, MODIS IGBP's 17) plus Hansen's separate tree-cover-percent scheme. They
are NOT directly comparable - a caller must never merge or diff class
breakdowns across two different analyses; each result stands alone, labeled
with which product it came from.

Known dataset limitations surfaced in `stats["note"]` where they'd
otherwise mislead a reader:
  - Hansen `gain` is a whole-period 2000-2012 figure; this dataset does not
    track gain after 2012 at all.
  - Hansen's baseline forest area is threshold-sensitive BY CONSTRUCTION -
    it uses the live forest-definition canopy-cover setting at compute
    time, so re-running after that setting changes is expected, not a bug.
  - MODIS is 500m resolution vs. 10m for the other land-cover products -
    coarse trend context only, not microlandscape-scale area.

Wave: vegetation indices (NDVI/EVI/SAVI/MNDWI/NBR) compute FRESH from raw
Sentinel-2 imagery rather than querying a pre-built dataset, so unlike the
five analyses above they need real band math and cloud-free compositing -
`_s2_reflectance_composite`/`_annual_index_series` are the ONE shared
utility all five call, not five copies of the same compositing logic.
Verified live before implementing: S2_SR_HARMONIZED bands are raw DN in the
thousands, not [0,1] reflectance - EVI/SAVI's additive constants (+1, +0.5)
are calibrated for reflectance and would be silently wrong without the
divide(10000) in `_s2_reflectance_composite`. Sentinel-2 only (2017-present)
- a real multi-year series (one composite+value PER YEAR, not a snapshot),
matching the trend-chart framing these were designed for. Landsat back to
2013 was evaluated and deliberately deferred: different band names/scale,
no Cloud Score+ equivalent (would need QA_PIXEL bit-mask instead), 30m vs
10m resolution, and a likely fake "trend break" at the sensor handoff year -
real complications, not a drop-in extension. Synchronous like the first
five (measured ~3.5-4s for the full 9-year series in one round trip, same
single-.getInfo()-call convention) - no job queue needed at this AOI scale.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import ee

from app.core.db import Database
from app.core.errors import NotFoundError, ValidationError
from app.domain.analysis_catalog import CATALOG, get_catalog_entry
from app.domain.authz import require_project_upload, require_project_view
from app.domain.dtos import (
    AnalysisResultOut,
    CurrentUser,
    ProjectAnalysisCatalog,
    ProjectAnalysisSummary,
)
from app.domain.enums import AuditAction
from app.domain.gee_class_legends import (
    DYNAMIC_WORLD_LEGEND,
    ESA_WORLDCOVER_LEGEND,
    ESRI_LULC_LEGEND,
    MODIS_IGBP_LEGEND,
    legend_entries,
)
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.audit import AuditRepository
from app.repositories.forest_definition import ForestDefinitionRepository
from app.services.gee_client import init_ee

AOI_CRS = "EPSG:32643"  # UTM 43N, Karnataka -- same convention as scripts/gee_phase1_agb_proxy.py

# ponytail: fixed year lists rather than discovering each collection's actual
# latest year server-side (an extra getInfo() per request for a number that
# only changes once a year). Bump these annually, or add real "latest
# available year" discovery if that becomes painful.
_ESRI_LULC_YEARS = range(2017, 2024)
_MODIS_YEARS = range(2001, 2024)


class GEEAnalysisService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- catalog / cache metadata - no GEE calls ----

    def list_catalog(self) -> list[dict[str, Any]]:
        return list(CATALOG)

    def get_project_analyses(
        self, project_id: UUID, user: CurrentUser
    ) -> ProjectAnalysisCatalog:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            computed_at_by_id = AnalysisResultRepository(cur).list_for_project(project_id)
        analyses = [
            ProjectAnalysisSummary(**entry, computed_at=computed_at_by_id.get(entry["id"]))
            for entry in CATALOG
        ]
        return ProjectAnalysisCatalog(project_id=project_id, analyses=analyses)

    def get_result(
        self, project_id: UUID, analysis_id: str, user: CurrentUser
    ) -> AnalysisResultOut:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            row = AnalysisResultRepository(cur).get(project_id, analysis_id)
        if row is None:
            raise NotFoundError("This analysis has not been computed for this project yet.")
        return AnalysisResultOut(**row)

    # ---- refresh - the only thing that actually calls GEE ----

    def refresh(self, project_id: UUID, analysis_id: str, actor: CurrentUser) -> AnalysisResultOut:
        entry = get_catalog_entry(analysis_id)
        if entry is None:
            raise NotFoundError("Unknown analysis.")
        if entry["status"] != "available":
            raise ValidationError(f"'{entry['name']}' is not implemented yet.")

        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_upload(cur, project_id, actor)
            boundary_geojson = AnalysisResultRepository(cur).get_project_boundary_geojson(
                project_id
            )
            canopy_cover_pct = float(ForestDefinitionRepository(cur).get()["canopy_cover_pct"])

        if boundary_geojson is None:
            raise ValidationError(
                "This project has no Boundary layer yet - upload one before running an analysis."
            )

        stats, legend, tile_url_template = _compute(analysis_id, boundary_geojson, canopy_cover_pct)

        with self.db.transaction() as cur:
            row = AnalysisResultRepository(cur).upsert(
                project_id=project_id, analysis_id=analysis_id, computed_by=actor.user_id,
                stats=stats, legend=legend, tile_url_template=tile_url_template,
            )
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.REFRESH_ANALYSIS, target=analysis_id,
                detail=f"Recomputed '{entry['name']}' for project {project_id}.",
                project_id=project_id,
            )
        return AnalysisResultOut(**row)


# --------------------------------------------------------------- GEE queries


def _compute(
    analysis_id: str, boundary_geojson: dict[str, Any], canopy_cover_pct: float
) -> tuple[dict[str, Any], list[dict[str, Any]] | None, str | None]:
    """The ONLY function that touches the GEE client - init_ee() and
    ee.Geometry() construction both happen here, not in refresh(), so a
    caller can test refresh()'s DB/permission/cache logic by monkeypatching
    this one function alone, with zero GEE credentials/network involved."""
    init_ee()
    boundary = ee.Geometry(boundary_geojson)
    if analysis_id == "hansen_gfc":
        return _hansen_forest_change(boundary, canopy_cover_pct)
    if analysis_id == "dynamic_world":
        return _dynamic_world(boundary)
    if analysis_id == "esa_worldcover":
        return _esa_worldcover(boundary)
    if analysis_id == "io_lulc":
        return _esri_lulc(boundary)
    if analysis_id == "modis_lulc":
        return _modis_lulc(boundary)
    if analysis_id == "ndvi":
        return _ndvi_query(boundary)
    # refresh() already rejects any non-"available" analysis_id before calling this.
    raise AssertionError(f"no query function wired for {analysis_id!r}")


def _pixel_area_ha() -> ee.Image:
    return ee.Image.pixelArea().divide(10000)


def _histogram_counts(img: ee.Image, band: str, boundary: ee.Geometry, scale: int):
    """Returns the (not-yet-evaluated) ee.ComputedObject for a class-frequency
    histogram - the caller decides when to .getInfo(), so per-year callers
    can combine several years into a single round trip."""
    return img.select(band).reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(), geometry=boundary, scale=scale, crs=AOI_CRS,
        maxPixels=1e10, bestEffort=True,
    ).get(band)


def _class_breakdown(
    class_area_ha: dict[int, float], legend: dict[int, tuple[str, str]]
) -> dict[str, float]:
    """Full legend, in code order, 0.0 for any class absent from this AOI -
    a stable key set across projects/refreshes, not one that shrinks/grows
    with what happens to be present."""
    return {legend[c][0]: round(class_area_ha.get(c, 0.0), 2) for c in sorted(legend)}


def _visualize_discrete(img: ee.Image, legend: dict[int, tuple[str, str]]) -> ee.Image:
    """Remaps class codes to a contiguous 0..n-1 index before visualize() -
    codes like WorldCover's 10/20/.../100 are not evenly spaced, so a plain
    min/max/palette visualize() would misassign colors."""
    codes = sorted(legend.keys())
    colors = [legend[c][1] for c in codes]
    return img.remap(codes, list(range(len(codes)))).visualize(
        min=0, max=len(codes) - 1, palette=colors
    )


def _hansen_forest_change(
    boundary: ee.Geometry, canopy_cover_pct: float
) -> tuple[dict[str, Any], None, str]:
    gfc = ee.Image("UMD/hansen/global_forest_change_2025_v1_13")
    forest_mask = gfc.select("treecover2000").gte(canopy_cover_pct)
    area_ha = _pixel_area_ha()

    totals = (
        area_ha.rename("baseline_ha").updateMask(forest_mask)
        .addBands(area_ha.rename("gain_ha").updateMask(gfc.select("gain")))
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=boundary, scale=30, crs=AOI_CRS,
            maxPixels=1e10, bestEffort=True,
        )
    )
    loss_stack = (
        area_ha.rename("loss_ha")
        .addBands(gfc.select("lossyear").rename("year"))
        .updateMask(forest_mask.And(gfc.select("loss")))
    )
    grouped = loss_stack.reduceRegion(
        reducer=ee.Reducer.sum().group(groupField=1, groupName="year"),
        geometry=boundary, scale=30, crs=AOI_CRS, maxPixels=1e10, bestEffort=True,
    )
    # ONE round trip for both reduceRegions.
    result = ee.Dictionary({"totals": totals, "loss_groups": grouped.get("groups")}).getInfo()

    loss_by_year = {
        str(2000 + int(g["year"])): round(float(g["sum"]), 2)
        for g in (result["loss_groups"] or [])
    }
    stats = {
        "canopy_cover_threshold_pct": canopy_cover_pct,
        "baseline_forest_area_ha": round(float(result["totals"].get("baseline_ha") or 0), 2),
        "gain_area_ha_2000_2012": round(float(result["totals"].get("gain_ha") or 0), 2),
        "loss_area_ha_by_year": loss_by_year,
        "note": (
            "Gain is a whole-period 2000-2012 figure only - this dataset does not "
            "track gain after 2012. Baseline forest area applies the canopy-cover "
            "threshold above (the current forest-definition setting); re-run this "
            "analysis after changing that setting to see the effect."
        ),
    }

    vis = (
        gfc.select("treecover2000").updateMask(forest_mask)
        .visualize(min=0, max=100, palette=["ffffe5", "004529"])
        .blend(
            gfc.select("lossyear").updateMask(gfc.select("loss"))
            .visualize(min=1, max=23, palette=["ffff00", "ff0000"])
        )
        .blend(gfc.select("gain").selfMask().visualize(palette=["00ffff"]))
    )
    map_id = vis.getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


def _dynamic_world(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    end = datetime.utcnow()
    start = end - timedelta(days=365)
    dw = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterBounds(boundary)
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .select("label")
        .mode()
    )
    counts = _histogram_counts(dw, "label", boundary, scale=10).getInfo() or {}
    px_area_ha = 100 / 10000.0  # 10m pixel
    class_ha = {int(k): v * px_area_ha for k, v in counts.items()}
    stats = {"class_area_ha": _class_breakdown(class_ha, DYNAMIC_WORLD_LEGEND)}
    map_id = _visualize_discrete(dw, DYNAMIC_WORLD_LEGEND).getMapId()
    return stats, legend_entries(DYNAMIC_WORLD_LEGEND), map_id["tile_fetcher"].url_format


def _esa_worldcover(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    wc = ee.ImageCollection("ESA/WorldCover/v200").first()
    counts = _histogram_counts(wc, "Map", boundary, scale=10).getInfo() or {}
    px_area_ha = 100 / 10000.0
    class_ha = {int(k): v * px_area_ha for k, v in counts.items()}
    stats = {
        "class_area_ha": _class_breakdown(class_ha, ESA_WORLDCOVER_LEGEND),
        "note": "Single 2021 snapshot, not a time series.",
    }
    map_id = _visualize_discrete(wc.select("Map"), ESA_WORLDCOVER_LEGEND).getMapId()
    return stats, legend_entries(ESA_WORLDCOVER_LEGEND), map_id["tile_fetcher"].url_format


def _esri_lulc(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    coll = ee.ImageCollection("projects/sat-io/open-datasets/landcover/ESRI_Global-LULC_10m_TS")

    def _year_mosaic(year: int) -> ee.Image:
        # Tiled by MGRS tile; a project boundary may span more than one tile,
        # so mosaic every tile tagged with this year rather than picking one.
        return coll.filter(ee.Filter.stringContains("system:index", str(year))).mosaic()

    counts_by_year = {
        str(year): _histogram_counts(_year_mosaic(year), "b1", boundary, scale=10)
        for year in _ESRI_LULC_YEARS
    }
    combined = ee.Dictionary(counts_by_year).getInfo()  # ONE round trip for every year

    px_area_ha = 100 / 10000.0
    class_area_ha_by_year = {}
    for year, hist in combined.items():
        class_ha = {int(k): v * px_area_ha for k, v in (hist or {}).items()}
        class_area_ha_by_year[year] = _class_breakdown(class_ha, ESRI_LULC_LEGEND)
    stats = {"class_area_ha_by_year": class_area_ha_by_year}

    latest = _year_mosaic(max(_ESRI_LULC_YEARS)).select("b1")
    map_id = _visualize_discrete(latest, ESRI_LULC_LEGEND).getMapId()
    return stats, legend_entries(ESRI_LULC_LEGEND), map_id["tile_fetcher"].url_format


def _modis_lulc(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    coll = ee.ImageCollection("MODIS/061/MCD12Q1").select("LC_Type1")

    def _year_image(year: int) -> ee.Image:
        return coll.filterDate(f"{year}-01-01", f"{year + 1}-01-01").first()

    counts_by_year = {
        str(year): _histogram_counts(_year_image(year), "LC_Type1", boundary, scale=500)
        for year in _MODIS_YEARS
    }
    combined = ee.Dictionary(counts_by_year).getInfo()  # ONE round trip for every year

    px_area_ha = (500 * 500) / 10000.0
    class_area_ha_by_year = {}
    for year, hist in combined.items():
        class_ha = {int(k): v * px_area_ha for k, v in (hist or {}).items()}
        class_area_ha_by_year[year] = _class_breakdown(class_ha, MODIS_IGBP_LEGEND)
    stats = {
        "class_area_ha_by_year": class_area_ha_by_year,
        "note": (
            "500m resolution - much coarser than the 10m land-cover products above. "
            "Use for multi-year trend context, not microlandscape-scale area."
        ),
    }

    latest = _year_image(max(_MODIS_YEARS))
    map_id = _visualize_discrete(latest, MODIS_IGBP_LEGEND).getMapId()
    return stats, legend_entries(MODIS_IGBP_LEGEND), map_id["tile_fetcher"].url_format


# ----------------------------------------------- vegetation/water/burn indices


_VEG_INDEX_YEARS = range(2017, datetime.utcnow().year + 1)  # Sentinel-2 available since 2017
_VEG_SEASON_START_MD = "02-01"  # pre-monsoon window - same convention as
_VEG_SEASON_END_MD = "05-31"  # scripts/gee_phase1_agb_proxy.py's usage example
_VEG_CLOUD_BAND = "cs_cdf"
_VEG_CLOUD_THRESHOLD = 0.60
# Diverging red-yellow-green - low (sparse/no vegetation) to high (dense vegetation).
# Same ramp for all 5 indices: they all range roughly -1..1 with "more vegetation/
# water/burn signal" at the high end, and reusing one palette keeps every index's
# map view visually consistent rather than inventing a new ramp per formula.
_VEG_INDEX_PALETTE = [
    "a50026", "d73027", "f46d43", "fdae61", "fee08b",
    "d9ef8b", "a6d96a", "66bd63", "1a9850", "006837",
]


def _s2_reflectance_composite(boundary: ee.Geometry, year: int) -> ee.Image:
    """Cloud-masked (Cloud Score+, same mask/threshold as
    scripts/gee_phase1_agb_proxy.py's _s2_composite - reused, not
    reinvented) pre-monsoon Sentinel-2 median composite for one calendar
    year, scaled from raw DN to [0,1] reflectance. The /10000 is not
    optional: verified live that S2_SR_HARMONIZED bands are raw DN in the
    thousands, not already [0,1] - EVI's "+1" and SAVI's "+0.5" are
    constants calibrated for reflectance and would be silently swamped by
    unscaled DN values (NDVI/MNDWI/NBR are pure ratios and technically
    unaffected by scale, but this is scaled for all 5 for consistency, so
    no future index added here has to remember which ones need it)."""
    start, end = f"{year}-{_VEG_SEASON_START_MD}", f"{year}-{_VEG_SEASON_END_MD}"
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(boundary)
    )
    cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

    def _mask(img: ee.Image) -> ee.Image:
        return img.updateMask(img.select(_VEG_CLOUD_BAND).gte(_VEG_CLOUD_THRESHOLD))

    composite = s2.linkCollection(cs_plus, [_VEG_CLOUD_BAND]).map(_mask).median().clip(boundary)
    return composite.divide(10000)


def _annual_index_series(
    boundary: ee.Geometry, index_band
) -> tuple[dict[str, Any], None, str]:
    """Shared by every vegetation/water/burn index - one composite + one
    index value PER YEAR (2017-present), not a single snapshot, matching the
    trend-chart framing these were designed for. `index_band` is a
    `ee.Image -> ee.Image` formula (e.g. NDVI's normalizedDifference) applied
    to each year's reflectance composite. Builds the whole N-year
    computation graph in a Python loop but calls .getInfo() exactly once for
    the whole series (verified live: ~3.5-4s for the full 2017-present range
    over a real boundary - comfortably synchronous, same one-round-trip
    convention as the two annual analyses above).

    Only the LATEST year gets a map tile (one extra getMapId() call) - not
    all N years. A per-year tile for the timeline scrubber to switch between
    was considered; deferred until real timing shows there's headroom for
    N extra getMapId() round trips without risking the sync budget this
    whole batch was measured against. The scrubber still has a real job:
    it drives which year's point is highlighted on the trend chart, using
    the already-fetched series - no extra backend call for that part."""
    per_year_value = {}
    latest_year = max(_VEG_INDEX_YEARS)
    latest_image = None
    for year in _VEG_INDEX_YEARS:
        refl = _s2_reflectance_composite(boundary, year)
        idx = index_band(refl).rename("index")
        if year == latest_year:
            latest_image = idx
        per_year_value[str(year)] = idx.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=boundary, scale=10, crs=AOI_CRS,
            maxPixels=1e10, bestEffort=True,
        ).get("index")
    series = ee.Dictionary(per_year_value).getInfo()
    series = {year: (round(v, 4) if v is not None else None) for year, v in series.items()}

    stats = {
        "series": series,
        "note": (
            "Boundary-mean of a cloud-masked, pre-monsoon (Feb-May) Sentinel-2 "
            "composite per year, 2017-present. Sentinel-2 only - a Landsat "
            "extension back to 2013 would need different cloud-masking (no Cloud "
            "Score+ equivalent) and risks a false 'trend break' at the sensor "
            "handoff year, so isn't included in this batch."
        ),
    }
    map_id = latest_image.visualize(min=-1, max=1, palette=_VEG_INDEX_PALETTE).getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


def _ndvi_query(boundary: ee.Geometry) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(
        boundary, lambda refl: refl.normalizedDifference(["B8", "B4"])
    )
