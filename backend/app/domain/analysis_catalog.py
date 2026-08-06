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

from typing import Literal, TypedDict

AnalysisStatus = Literal["available", "in-development"]


class AnalysisCatalogEntry(TypedDict):
    id: str
    name: str
    category: str
    status: AnalysisStatus
    description: str


CATALOG: tuple[AnalysisCatalogEntry, ...] = (
    # --- Real (5), status "available" ---
    {
        "id": "hansen_gfc",
        "name": "Global Forest Change (Hansen)",
        "category": "Forest Change",
        "status": "available",
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
        "description": "Near-real-time 10m land-cover class breakdown (last 12 months).",
    },
    {
        "id": "esa_worldcover",
        "name": "ESA WorldCover",
        "category": "Land Cover",
        "status": "available",
        "description": "10m global land-cover class breakdown, single 2021 snapshot.",
    },
    {
        "id": "io_lulc",
        "name": "10m Annual Land Cover (Impact Observatory / Esri)",
        "category": "Land Cover",
        "status": "available",
        "description": "10m annual land-cover class breakdown, 2017-present.",
    },
    {
        "id": "modis_lulc",
        "name": "MODIS Land Cover",
        "category": "Land Cover",
        "status": "available",
        "description": (
            "500m annual land-cover class breakdown, 2001-present (longest history; "
            "coarse resolution - context/trend only, not microlandscape-scale)."
        ),
    },
    # --- Deferred (8), status "in-development" ---
    {"id": "ndvi", "name": "NDVI", "category": "Vegetation Indices", "status": "in-development",
     "description": "Normalized Difference Vegetation Index time series."},
    {"id": "evi", "name": "EVI", "category": "Vegetation Indices", "status": "in-development",
     "description": "Enhanced Vegetation Index time series."},
    {"id": "savi", "name": "SAVI", "category": "Vegetation Indices", "status": "in-development",
     "description": "Soil-Adjusted Vegetation Index time series."},
    {"id": "mndwi", "name": "MNDWI", "category": "Vegetation Indices", "status": "in-development",
     "description": "Modified Normalized Difference Water Index time series."},
    {"id": "nbr", "name": "NBR", "category": "Vegetation Indices", "status": "in-development",
     "description": "Normalized Burn Ratio time series."},
    {"id": "sar", "name": "SAR", "category": "Radar", "status": "in-development",
     "description": "Sentinel-1 radar backscatter composite."},
    {"id": "timelapse", "name": "Timelapse", "category": "Timelapse", "status": "in-development",
     "description": "Animated imagery timelapse over the project boundary."},
    {"id": "cultivated_area", "name": "Cultivated Area", "category": "Land Use",
     "status": "in-development", "description": "Cultivated-area detection and change."},
)

_BY_ID: dict[str, AnalysisCatalogEntry] = {e["id"]: e for e in CATALOG}

REAL_ANALYSIS_IDS: frozenset[str] = frozenset(
    e["id"] for e in CATALOG if e["status"] == "available"
)


def get_catalog_entry(analysis_id: str) -> AnalysisCatalogEntry | None:
    return _BY_ID.get(analysis_id)
