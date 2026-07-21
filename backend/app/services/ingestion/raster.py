"""
Raster processing - the single source of truth for all raster math.

Existing implementation (MVP): TWO divergent copies (ingest.py and
load_demo_data.py) each did `src.read(1)` to pull the ENTIRE band into memory,
twice per ingest, and computed hectares from pixel counts on the EPSG:4326
(reprojected) grid.

Why insufficient - two separate defects:
  1. Memory: a full-resolution Sentinel-2 tile is hundreds of MB to GBs as a NumPy
     array; reading it whole (repeatedly) OOM-kills the worker. There is no upper
     bound on memory as raster size grows.
  2. Correctness: EPSG:4326 is a geographic CRS measured in degrees. A "pixel" in
     4326 does not cover a constant ground area - it shrinks toward the poles. Area
     computed from 4326 pixel counts is therefore wrong, and the error grows with
     latitude. For a carbon-accounting platform where area drives credit volume,
     this is a material accuracy bug, not a rounding issue.

Enterprise solution:
  * WINDOWED reads (fixed-size tiles) so peak memory is O(window^2), independent of
    total raster size - the approach ArcGIS / GEE / any serious raster stack uses.
  * Area computed on a projected, equal-area grid in METRES: the native grid when
    the source is already projected in metres (exact, zero resampling error - the
    Sentinel-2/UTM common case), otherwise a lazy WarpedVRT reprojection to
    EPSG:6933 (a global equal-area CRS). Pixel ground area comes from the raster's
    own affine transform, never a user-typed number.
  * Previews are DECIMATED on read (rasterio `out_shape`) to a bounded pixel budget,
    so rendering a preview never loads full resolution.
  * Reprojection to EPSG:4326 (for display) streams tile-by-tile into a tiled
    GeoTIFF via WarpedVRT, so it is also memory-bounded.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window

DISPLAY_CRS = "EPSG:4326"
EQUAL_AREA_CRS = "EPSG:6933"  # Lambert Cylindrical Equal Area (NSIDC EASE-Grid 2.0 Global), metres

DEFAULT_PALETTE = [
    "#2C6B2F", "#C9A227", "#3A7CA5", "#8C8C8C", "#B5895B", "#8FBF5F",
    "#7D3C98", "#E67E22", "#16A085", "#C0392B", "#2980B9", "#34495E",
]

# A legend entry may be a plain label string (`{"1": "Forest"}`, the shape the
# upload form documents) or a {"label", "color"} dict (adds a preview colour) -
# both are accepted everywhere a legend entry is read.
Legend = dict[str, dict[str, str] | str] | None


@dataclass(frozen=True)
class BandStats:
    min: float
    max: float
    mean: float
    stddev: float


@dataclass(frozen=True)
class RasterStats:
    total_area_ha: float
    area_crs: str  # which CRS the area was measured in (provenance)
    # Exactly one of these is populated, depending on whether a legend was
    # supplied - see compute_stats.
    class_area_ha: dict[str, float] | None  # label -> hectares
    band_stats: BandStats | None  # generic min/max/mean/stddev for an unclassified band


def legend_defines(value: int, legend: Legend) -> bool:
    """Whether `legend` explicitly names `value` as a real class (e.g.
    Dynamic World's class 0 = Water) - the check that decides whether the
    padding heuristic below is even safe to apply for that value."""
    if not legend:
        return False
    return _entry_label(legend.get(str(value))) is not None


def padding_value(nodata: float | None, legend: Legend = None) -> float | None:
    """The pixel value that means 'no real data here', for both area stats
    and classified tile rendering: the raster's own recorded `nodata` if it
    has one, otherwise 0 - reproject_to_4326's warp-fill padding signature
    around a rotated/irregular scene's real footprint on a raster that never
    had a nodata value set (see this module's docstring; tile_renderer.py's
    `_mask_warp_fill`/`read_pixel` apply the equivalent rule for multi-band
    data, where "every band exactly 0" plays the same role as "value 0" does
    for a single-band classified raster). Single, importable definition so
    stats computation and tile rendering can't silently disagree on what
    counts as padding. Returns None - "no safe padding value" - when there's
    no way to tell, so callers must treat every pixel as real data.

    Bugfix (Phase 3 Wave G): `compute_stats`'s classified-area counting and
    `tile_renderer._colormap_for_uint8` both read pixel values from a raster
    directly (not through rio-tiler's own nodata masking), and neither ever
    excluded warp-fill padding when the raster had no real nodata tag - a
    classified LULC layer with a real, irregular plot boundary sitting in a
    larger axis-aligned raster showed the surrounding padding as ~59% of the
    "Unclassified" area (should be 0 real ha), and the same padding rendered
    as an opaque fake class color on the map instead of transparent.

    Bugfix (Phase 3 Wave I): the fallback-to-0 heuristic above then went too
    far the other way - it fired unconditionally whenever no nodata was
    recorded, silently dropping a legend-defined class 0 (a real, named
    class like Dynamic World's Water=0) as if it were warp-fill padding. 0
    is ambiguous between "real class" and "padding heuristic" with no way to
    tell them apart by value alone once a legend names it, so the heuristic
    simply doesn't apply in that case - real nodata (explicit source
    metadata) still always takes precedence and is unaffected by this."""
    if nodata is not None:
        return nodata
    if legend_defines(0, legend):
        return None
    return 0


def color_for_value(value: int, legend: Legend) -> str:
    entry = legend.get(str(value)) if legend else None
    if isinstance(entry, dict):
        c = entry.get("color")
        if c:
            return c
    return DEFAULT_PALETTE[value % len(DEFAULT_PALETTE)]


def _entry_label(entry: dict[str, str] | str | None) -> str | None:
    """The human label for one legend entry, in this codebase's one accepted
    shape (a plain string, or a {"label", "color"} dict). None for a
    malformed/empty/absent entry. Shared by `_bucket_by_legend` (labels
    actually found in the raster's pixels) and `legend_class_labels` (Phase 3
    Wave G: every label a legend DEFINES, regardless of whether any pixel
    matched it)."""
    if isinstance(entry, dict) and entry.get("label"):
        return entry["label"]
    if isinstance(entry, str) and entry.strip():
        return entry
    return None


def legend_class_labels(legend: Legend) -> set[str]:
    """Every real class label a legend defines, regardless of whether any
    pixel in the raster actually matched it.

    Phase 3 Wave G (Landscape Evolution): `compute_stats` only ever writes a
    KPI row for a pixel value that occurs at least once (see
    `_bucket_by_legend` below) - a legend-defined class with zero matching
    pixels gets no row at all. That's indistinguishable from "this class
    isn't part of this date's legend" by looking at KPI rows alone; reading
    the legend itself (this function) is what makes the distinction
    possible - see ProjectService.get_evolution, which needs "0 ha, defined
    but unmeasured" and "not defined at this date" to mean different things."""
    if not legend:
        return set()
    return {label for entry in legend.values() if (label := _entry_label(entry))}


def metric_key(label: str) -> str:
    """The kpi.metric_name a class label is stored under (e.g. "Dense Forest"
    -> "class_area_dense_forest") - the exact slugification
    IngestionService.ingest uses when writing class-area KPI rows, kept here
    as the one definition (not re-implemented) so anything reading those
    rows back by label - like the Landscape Evolution endpoint - can't
    silently drift from how they were written."""
    safe = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return f"class_area_{safe}"


def _bucket_by_legend(counts: dict[int, int], legend: Legend) -> dict[str, int]:
    """Map raw pixel-value counts through the legend. A pixel value the legend
    doesn't name isn't a real class - it's raw, unlabeled raster data - so it is
    summed into one "Unclassified" bucket instead of being reported as its own
    fake per-value class (the bug this fixes: a raw scene has thousands of
    distinct values, none of which are meaningful "classes" on their own)."""
    buckets: dict[str, int] = {}
    for v, c in counts.items():
        entry = legend.get(str(v)) if legend else None
        label = _entry_label(entry) or "Unclassified"
        buckets[label] = buckets.get(label, 0) + c
    return buckets


def _iter_windows(width: int, height: int, block: int) -> Iterator[Window]:
    for row in range(0, height, block):
        h = min(block, height - row)
        for col in range(0, width, block):
            w = min(block, width - col)
            yield Window(col, row, w, h)


def _pixel_area_m2(transform) -> float:
    """Ground area of one pixel from the affine transform (linear units of
    the CRS). Determinant form (a*e - b*d), exact for a rotated/sheared grid
    too - `a*e` alone (dropping b*d) is only correct for a north-up grid;
    every `WarpedVRT` output is north-up so that branch was never wrong, but
    the native-projected branch reads a source's own transform directly and
    a rotated/sheared source would have silently understated area there."""
    return abs(transform.a * transform.e - transform.b * transform.d)


def _accumulate_counts(dataset, block: int, nodata, legend: Legend = None) -> dict[int, int]:
    """Windowed pass over band 1; returns {pixel_value: count}, with padding
    (see `padding_value`) excluded entirely - not bucketed as a fake class
    and not counted toward total area. Classified data is always single-band
    by this app's own convention (band 1 = class value), so "this pixel's
    value is the padding value" is the complete, exact check here - no
    approximation the way a genuinely multi-band check would need. Memory
    O(block^2).

    `legend` is passed through to `padding_value` so a legend-defined class 0
    (e.g. Water=0) is never excluded as padding - see that function."""
    pad = padding_value(nodata, legend)
    counts: dict[int, int] = {}
    for win in _iter_windows(dataset.width, dataset.height, block):
        arr = dataset.read(1, window=win)
        if pad is not None:
            arr = arr[arr != pad]
        if arr.size == 0:
            continue
        vals, cnts = np.unique(arr, return_counts=True)
        for v, c in zip(vals.tolist(), cnts.tolist(), strict=True):
            counts[int(v)] = counts.get(int(v), 0) + int(c)
    return counts


def _accumulate_band_stats(dataset, block: int, nodata) -> tuple[float, float, float, float, int]:
    """Windowed pass returning band 1's own (min, max, mean, stddev,
    valid_pixel_count) - excluding warp-fill padding correctly: padding means
    EVERY band is 0 at once (the same rule `tile_renderer._mask_warp_fill`
    already applies to tile rendering), so this reads all of the dataset's
    bands per window (still windowed/memory-bounded - O(block^2 * band_count)
    per window, not per whole raster) to build that mask, then reports band
    1's stats over only the real (unmasked) pixels.

    Bugfix (Phase 3 Wave H): this used to read ONLY band 1 and had no
    padding exclusion at all for the reason documented in the git history -
    checking just band 1 for a genuinely multi-band scene risks the same
    silent-miscount bug `padding_value`/`_mask_warp_fill` already fixed
    elsewhere: a real pixel can have band 1 == 0 while other bands are real
    data (undercounting if band 1 alone were treated as padding), and
    conversely warp-fill padding doesn't always land on exactly 0 in band 1
    specifically (overcounting/skewing total_area_ha and band_stats if
    nothing masked it). Reading all bands to check "all bands 0 at once" is
    the same rule used everywhere else in this module, applied here too."""
    count = 0
    total = 0.0
    total_sq = 0.0
    minv = math.inf
    maxv = -math.inf
    band_count = dataset.count
    band_indexes = list(range(1, band_count + 1))
    for win in _iter_windows(dataset.width, dataset.height, block):
        raw = dataset.read(band_indexes, window=win)
        band1 = raw[0]
        invalid = np.all(raw == 0, axis=0)
        if nodata is not None:
            invalid |= band1 == nodata
        arr = band1[~invalid]
        if arr.size == 0:
            continue
        arr = arr.astype(np.float64)
        count += arr.size
        total += float(arr.sum())
        total_sq += float(np.square(arr).sum())
        minv = min(minv, float(arr.min()))
        maxv = max(maxv, float(arr.max()))
    if count == 0:
        return 0.0, 0.0, 0.0, 0.0, 0
    mean = total / count
    variance = max(total_sq / count - mean * mean, 0.0)
    return minv, maxv, mean, variance**0.5, count


def compute_stats(src_path: str, legend: Legend, block: int = 2048) -> RasterStats:
    """Area is always measured on an equal-area grid in metres.

    With a legend: per-class area in hectares, each pixel value mapped through
    the legend to its label; any value the legend doesn't name is bucketed into
    a single "Unclassified" total rather than reported as its own fake class.

    Without a legend: there is no classification to report areas for - the
    scene is raw/unclassified (e.g. a reflectance band), and a per-value area
    breakdown would just be its brightness histogram. Generic per-band
    statistics (min/max/mean/stddev) are returned instead, in `band_stats`.
    """
    has_legend = bool(legend)
    with rasterio.open(src_path) as src:
        crs = src.crs
        nodata = src.nodata
        projected_metres = bool(
            crs and crs.is_projected and (crs.linear_units or "").lower() in {"metre", "meter", "m"}
        )
        if projected_metres:
            # Exact: measure on the native grid, no resampling error.
            if has_legend:
                counts = _accumulate_counts(src, block, nodata, legend)
            else:
                minv, maxv, mean, std, count = _accumulate_band_stats(src, block, nodata)
            pixel_area_ha = _pixel_area_m2(src.transform) / 10_000.0
            area_crs = crs.to_string()
        else:
            # Geographic/other: measure through a lazy equal-area reprojection.
            with WarpedVRT(src, crs=EQUAL_AREA_CRS, resampling=Resampling.nearest) as vrt:
                if has_legend:
                    counts = _accumulate_counts(vrt, block, vrt.nodata, legend)
                else:
                    minv, maxv, mean, std, count = _accumulate_band_stats(vrt, block, vrt.nodata)
                pixel_area_ha = _pixel_area_m2(vrt.transform) / 10_000.0
                area_crs = EQUAL_AREA_CRS

    if has_legend:
        buckets = _bucket_by_legend(counts, legend)
        class_area = {label: round(c * pixel_area_ha, 4) for label, c in sorted(buckets.items())}
        total = round(sum(counts.values()) * pixel_area_ha, 4)
        return RasterStats(
            total_area_ha=total, area_crs=area_crs, class_area_ha=class_area, band_stats=None
        )

    total = round(count * pixel_area_ha, 4)
    band_stats = BandStats(
        min=round(minv, 4), max=round(maxv, 4), mean=round(mean, 4), stddev=round(std, 4)
    )
    return RasterStats(
        total_area_ha=total, area_crs=area_crs, class_area_ha=None, band_stats=band_stats
    )


def _percentile_stretch_uint8(band: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """2nd-98th percentile contrast stretch of one band to 0-255, computed only
    from pixels `valid_mask` marks as real data. For raw continuous data
    (reflectance, etc.) there's no fixed "class" to color - just a visual
    range to spread across the display range."""
    valid = band[valid_mask]
    if valid.size == 0:
        return np.zeros(band.shape, dtype=np.uint8)
    lo, hi = np.percentile(valid, [2, 98])
    if hi <= lo:
        hi = lo + 1
    stretched = np.clip((band.astype(np.float64) - lo) / (hi - lo) * 255.0, 0, 255)
    return stretched.astype(np.uint8)


def _classified_rgba(arr: np.ndarray, legend: Legend, nodata) -> np.ndarray:
    """Per-value class-color render - only meaningful when a legend actually
    names what each value means (LULC and any other legend-driven upload)."""
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for v in np.unique(arr).tolist():
        if nodata is not None and v == nodata:
            continue
        hexc = color_for_value(int(v), legend).lstrip("#")
        r, g, b = (int(hexc[i : i + 2], 16) for i in (0, 2, 4))
        rgba[arr == v] = (r, g, b, 235)
    if nodata is not None:
        rgba[arr == nodata] = (0, 0, 0, 0)
    return rgba


def _band_composite_rgba(vrt, out_h: int, out_w: int, nodata) -> np.ndarray:
    """Real band-to-RGB composite for raw/unclassified imagery: first 3 bands
    as R/G/B (1 band repeated to grayscale if that's all there is), each
    contrast-stretched independently. Not a "true color" render (band order
    isn't known to carry any particular meaning without per-band role
    metadata this pipeline doesn't collect) - just a genuine visual of the
    actual data, instead of a classified-raster color palette applied to
    continuous values (which produces meaningless per-pixel color noise).

    Bugfix (Wave E): a rotated/irregular scene reprojected onto an
    axis-aligned grid leaves warp-fill padding around its real footprint - 0
    in every band - with no `nodata` recorded when the source never had one.
    Treated as ordinary data, that padding both skews the percentile stretch
    and paints as opaque black instead of transparent (see tile_renderer.py's
    twin fix for the map-tile side of the same bug). "Every requested band
    exactly 0" is masked out alongside real `nodata`, not just `nodata`
    alone."""
    n = 3 if vrt.count >= 3 else 1
    raw = vrt.read(list(range(1, n + 1)), out_shape=(n, out_h, out_w), resampling=Resampling.nearest)

    invalid = np.all(raw == 0, axis=0)
    if nodata is not None:
        invalid |= raw[0] == nodata
    valid_mask = ~invalid

    channels = [_percentile_stretch_uint8(raw[i], valid_mask) for i in range(n)]
    if n == 1:
        channels = channels * 3
    r, g, b = channels
    rgba = np.dstack([r, g, b, np.full_like(r, 235)])
    rgba[invalid, 3] = 0
    return rgba


def render_preview(src_path: str, out_path: str, legend: Legend, max_dim: int = 2048) -> None:
    """Decimated RGBA PNG for the map overlay. Never reads full-res.

    With a legend: the raster is classified (LULC etc.) - band 1 holds class
    values, colored per `color_for_value`/the legend, same as always.

    Without a legend: the raster is raw/unclassified (e.g. Satellite / Raw
    Imagery). Bugfix (Wave C): this used to run the SAME per-value class-color
    render on band 1 regardless of legend - fine for a handful of discrete
    class values, but for a real multi-band scene with continuous reflectance
    values, coloring each unique pixel value via a 12-color cyclic palette
    produced what looked like solid-color-plus-random-speckle noise, not an
    image. Render a real band composite instead.
    """
    with rasterio.open(src_path) as src, WarpedVRT(
        src, crs=DISPLAY_CRS, resampling=Resampling.nearest
    ) as vrt:
        scale = max(vrt.width, vrt.height) / max_dim
        out_w = max(1, int(vrt.width / scale)) if scale > 1 else vrt.width
        out_h = max(1, int(vrt.height / scale)) if scale > 1 else vrt.height
        nodata = vrt.nodata

        if legend:
            arr = vrt.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest)
            rgba = _classified_rgba(arr, legend, nodata)
        else:
            rgba = _band_composite_rgba(vrt, out_h, out_w, nodata)

    Image.fromarray(rgba, mode="RGBA").save(out_path)


def reproject_to_4326(
    src_path: str, dst_path: str, block: int = 2048
) -> tuple[str, tuple[float, float, float, float]]:
    """
    Stream a reprojection to EPSG:4326 into a tiled GeoTIFF, tile-by-tile.
    Returns (source_crs_string, bounds_4326 as (minx,miny,maxx,maxy)).
    """
    with rasterio.open(src_path) as src:
        src_crs = src.crs.to_string() if src.crs else "unknown"
        with WarpedVRT(src, crs=DISPLAY_CRS, resampling=Resampling.nearest) as vrt:
            profile = vrt.profile.copy()
            profile.update(
                driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                compress="deflate", predictor=1,
            )
            with rasterio.open(dst_path, "w", **profile) as dst:
                for win in _iter_windows(vrt.width, vrt.height, block):
                    dst.write(vrt.read(window=win), window=win)
    # Read back the written file's bounds and normalise numerically to 4326.
    with rasterio.open(dst_path) as d:
        b = transform_bounds(d.crs, DISPLAY_CRS, *d.bounds)
    return src_crs, (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
