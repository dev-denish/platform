"""
Phase-1 auxiliary variable for two-phase (double) sampling AGB uncertainty, per
VM0047 Sec. 9.2 / Eq. 27-31. Produces the GEDI-gap-filled AGB raster over a project
AOI and extracts it (a) as a wall-to-wall/large-sample mean (phase1_mean, phase1_n)
and (b) at each phase-2 field-plot location (x_proxy, to pair with ground-measured
y_measured before calling app.services.sampling.agb_two_phase.two_phase_regression_estimate).

Why this exists: no RS/GEE code exists in this repo yet. This is the minimal
pipeline needed to produce the phase-1 side of the double-sampling design -- it does
NOT re-derive a general gap-fill framework, just this one AGB proxy for this one use.

CRS/scale: GEE geometry inputs are EPSG:4326 (as GEE requires); ALL reduceRegion /
reduceRegions / export calls below pin crs='EPSG:32643' (UTM 43N, Karnataka) at
scale=25 m -- 25 m matches the GEDI L4A footprint size. Do not sample this at 10 m;
that would silently oversample a coarser process (see project cheat sheet: "scale
mismatch" gotcha).

Confidence: the resulting AGB raster is a MODEL PREDICTION (Random Forest, trained
on sparse GEDI shots), not ground truth. Pixel-level error is commonly 20-40% per
GEDI/RF-biomass literature. Its role here is strictly as the phase-1 auxiliary
variable in double sampling -- phase 2 (actual field plots) is what anchors the
reported AGB mean; this raster only reduces how many field plots are needed for a
given Up,t. Do not report phase1_mean directly as project AGB.

Usage (interactive, one site at a time -- test small before batching all 10):
    import ee; ee.Initialize()
    from scripts.gee_phase1_agb_proxy import build_agb_proxy, phase1_stats, sample_at_plots

    aoi = ee.FeatureCollection('users/vnv/microlandscapes').filter(
        ee.Filter.eq('name', 'Suntikoppa')).geometry()
    agb = build_agb_proxy(
        aoi, '2025-02-01', '2025-05-31', gedi_start='2019-01-01', gedi_end='2025-05-31',
    )
    stats = phase1_stats(agb, aoi)  # -> {'phase1_mean': ..., 'phase1_n': ...} (getInfo() once)
    x_at_plots = sample_at_plots(agb, plots_fc)  # plots_fc: ee.FeatureCollection w/ 'plot_id'
"""
from __future__ import annotations

import ee

AOI_CRS = "EPSG:32643"  # UTM 43N, Karnataka -- all reduceRegion/export calls use this
SAMPLE_SCALE_M = 25  # matches GEDI L4A footprint size; do not sample at S-2's native 10 m
QA_BAND = "cs_cdf"
CLEAR_THRESHOLD = 0.60


def _s2_composite(aoi: ee.Geometry, start: str, end: str) -> ee.Image:
    """Cloud-masked (Cloud Score+) median S-2 composite with NDVI/EVI added."""
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterDate(start, end).filterBounds(aoi)
    cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

    def _mask(img: ee.Image) -> ee.Image:
        return img.updateMask(img.select(QA_BAND).gte(CLEAR_THRESHOLD))

    composite = (
        s2.linkCollection(cs_plus, [QA_BAND]).map(_mask).median().clip(aoi)
    )
    ndvi = composite.normalizedDifference(["B8", "B4"]).rename("NDVI")
    evi = composite.expression(
        "2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)",
        {
            "NIR": composite.select("B8"),
            "RED": composite.select("B4"),
            "BLUE": composite.select("B2"),
        },
    ).rename("EVI")
    return composite.addBands([ndvi, evi])


def _s1_composite(aoi: ee.Geometry, start: str, end: str) -> ee.Image:
    """Median VV/VH composite, IW mode only (S-1 GRD is already in dB in GEE)."""
    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterDate(start, end)
        .filterBounds(aoi)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(["VV", "VH"])
    )
    composite = s1.median().clip(aoi)
    ratio = composite.select("VV").subtract(composite.select("VH")).rename("VV_VH_ratio")
    return composite.addBands(ratio)


def _dem_terrain(aoi: ee.Geometry) -> ee.Image:
    dem = ee.Image("USGS/SRTMGL1_003").clip(aoi)
    return dem.rename("elevation").addBands(ee.Terrain.slope(dem).rename("slope"))


def _forest_mask(aoi: ee.Geometry) -> ee.Image:
    """Dynamic World-derived tree-cover mask (label 1 == 'trees')."""
    dw = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterBounds(aoi)
        .filterDate("2024-01-01", "2025-12-31")
        .select("label")
        .mode()
    )
    return dw.eq(1)


def build_agb_proxy(
    aoi: ee.Geometry,
    s2_start: str,
    s2_end: str,
    gedi_start: str,
    gedi_end: str,
    n_trees: int = 100,
) -> ee.Image:
    """
    GEDI L4A -> Random Forest regression on S-1 (VV/VH/ratio) + S-2 (NDVI/EVI) + SRTM
    (elevation/slope) -> continuous AGB raster (Mg/ha, band 'AGB'), masked to forest
    via Dynamic World. This IS the phase-1 auxiliary-variable raster.

    Predictor collection is trained and applied at SAMPLE_SCALE_M/AOI_CRS throughout,
    so the model sees the same grid it predicts on (no train/predict scale mismatch).
    """
    s1 = _s1_composite(aoi, "2015-01-01", "2025-12-31").select(["VV", "VH", "VV_VH_ratio"])
    predictors = (
        _s2_composite(aoi, s2_start, s2_end)
        .select(["NDVI", "EVI"])
        .addBands(s1)
        .addBands(_dem_terrain(aoi))
    )

    def _quality_mask(img: ee.Image) -> ee.Image:
        good = img.select("l4_quality_flag").eq(1).And(img.select("degrade_flag").eq(0))
        return img.updateMask(good)

    gedi = (
        ee.ImageCollection("LARSE/GEDI/GEDI04_A_002_MONTHLY")
        .filterDate(gedi_start, gedi_end)
        .filterBounds(aoi)
        .map(_quality_mask)
        .select("agbd")
        .mosaic()
        .rename("agbd")
    )

    training = predictors.addBands(gedi).sample(
        region=aoi, scale=SAMPLE_SCALE_M, projection=AOI_CRS, geometries=False, tileScale=4,
    ).filter(ee.Filter.notNull(["agbd"]))

    predictor_bands = ["NDVI", "EVI", "VV", "VH", "VV_VH_ratio", "elevation", "slope"]
    rf = ee.Classifier.smileRandomForest(numberOfTrees=n_trees).setOutputMode("REGRESSION").train(
        features=training, classProperty="agbd", inputProperties=predictor_bands,
    )

    agb = predictors.classify(rf).rename("AGB")
    return agb.updateMask(_forest_mask(aoi)).clip(aoi)


def phase1_stats(agb: ee.Image, aoi: ee.Geometry) -> dict:
    """
    Wall-to-wall (census) phase-1 mean + pixel count over the AOI. Returns an ee
    computed-value dict -- call .getInfo() ONCE on the result (fine for one AOI/site;
    do NOT nest this inside a per-pixel or per-plot Python loop, and if looping over
    all 10 microlandscapes, that's 10 getInfo() calls total, which is an acceptable
    quota cost -- don't add a second loop inside it).
    """
    reducer = ee.Reducer.mean().combine(ee.Reducer.count(), sharedInputs=True)
    stats = agb.reduceRegion(
        reducer=reducer, geometry=aoi, scale=SAMPLE_SCALE_M, crs=AOI_CRS, maxPixels=1e10,
    )
    return {"phase1_mean": stats.get("AGB_mean"), "phase1_n": stats.get("AGB_count")}


def sample_at_plots(agb: ee.Image, plots_fc: ee.FeatureCollection) -> ee.FeatureCollection:
    """
    Extracts the RS-proxy value (band 'AGB') at each phase-2 field-plot point.
    plots_fc must carry a 'plot_id' property (EPSG:4326 point geometries, as GEE
    requires for FeatureCollection input). Pull results with .aggregate_array(...)
    (server-side), never per-feature .getInfo() in a Python loop.
    """
    return agb.reduceRegions(
        collection=plots_fc, reducer=ee.Reducer.first(), scale=SAMPLE_SCALE_M, crs=AOI_CRS,
    )


def plots_to_phase2_input(
    sampled_fc: ee.FeatureCollection, plot_agb_by_id: dict[str, float]
) -> list[dict]:
    """
    Joins the RS-proxy extraction (sampled_fc, from sample_at_plots) with a caller-
    supplied {plot_id: ground_measured_AGB} dict (from the KML/PostGIS + Excel field-
    plot tracker -- ingestion of that is out of scope here) into the exact
    `phase2_plots` shape `two_phase_regression_estimate` expects. Uses
    aggregate_array (2 server-side calls total, not one getInfo per plot).
    """
    plot_ids = sampled_fc.aggregate_array("plot_id").getInfo()
    x_proxy = sampled_fc.aggregate_array("first").getInfo()
    return [
        {"plot_id": pid, "x_proxy": float(x), "y_measured": float(plot_agb_by_id[pid])}
        for pid, x in zip(plot_ids, x_proxy, strict=True)
        if pid in plot_agb_by_id
    ]
