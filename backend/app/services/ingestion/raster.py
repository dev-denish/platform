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

Wave: geometric padding fix. Replaces the old `padding_value()` heuristic
(guessing "0 probably means padding" from a pixel's VALUE, with no way to
tell a real legend-defined class 0 apart from warp-fill padding once a
legend names it) with a REAL geometric mask: every warp in this module now
passes `add_alpha=True` to its `WarpedVRT`, which makes rasterio/GDAL warp a
SYNTHETIC "fully valid" band alongside the real data, through the identical
transform and resampling - the result is exactly which output pixels the
warp actually placed real source data into, independent of what VALUE ended
up there. A real Water=0 pixel and warp-fill padding that happens to also
read 0 are no longer indistinguishable, because this mask never looks at
values at all.

This also runs for the "already projected in metres" case that never used
to warp at all: `WarpedVRT(src, crs=src.crs, ...)` is a verified lossless,
bit-exact passthrough when the source is already north-up (the common
case - see tests/unit/test_raster_stats.py), but a WarpedVRT unconditionally
produces a north-up destination grid, so for the rare source whose OWN
geotransform carries genuine rotation, this is what reveals (and correctly
masks) the resulting corner fill - the equivalent of `reproject_to_4326`'s
own axis-alignment for the area-measurement path.

Known limitation, stated plainly: this can only mask padding that a WARP
introduces (rotation, reprojection, or resampling onto a different grid). It
cannot recover a case where an upload's own flat pixel array already has
padding baked into ordinary VALUES by some tool upstream of this app, with
no `nodata` tag and no geometric mismatch for a warp to reveal - GDAL has no
way to distinguish that from real data, and neither do we. That is a
data-quality/source-metadata concern the uploader has to fix, not something
any warp-time mask can invent after the fact.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import MaskFlags, Resampling
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


def _accumulate_counts(vrt, block: int) -> dict[int, int]:
    """Windowed pass over band 1 of a VRT opened with `add_alpha=True` (see
    compute_stats); returns {pixel_value: count}, with padding excluded via
    the warp's own real geometric alpha/mask band (the last band,
    `vrt.count`) - not a guess from the pixel's value. A legend-defined
    class 0 (e.g. Water=0) and genuine warp-fill padding that happens to
    also read 0 are distinguishable now, because this mask never inspects
    values at all - only where the warp actually placed real source data.
    Memory O(block^2)."""
    mask_band = vrt.count
    counts: dict[int, int] = {}
    for win in _iter_windows(vrt.width, vrt.height, block):
        arr = vrt.read(1, window=win)
        alpha = vrt.read(mask_band, window=win)
        valid = arr[alpha > 0]
        if valid.size == 0:
            continue
        vals, cnts = np.unique(valid, return_counts=True)
        for v, c in zip(vals.tolist(), cnts.tolist(), strict=True):
            counts[int(v)] = counts.get(int(v), 0) + int(c)
    return counts


def _accumulate_band_stats(vrt, block: int) -> tuple[float, float, float, float, int]:
    """Windowed pass over a VRT opened with `add_alpha=True` (see
    compute_stats): band 1's own (min, max, mean, stddev, valid_pixel_count),
    padding excluded via the warp's real geometric alpha/mask band - not the
    old single-band "band 1 is exactly 0" heuristic (Wave H), which wrongly
    excluded a pixel with a real value in every OTHER band just because band
    1 alone happened to be 0.

    Wave: production render audit / area-accuracy follow-up. A real >=3-band
    raster additionally excludes a pixel that is an exact 0 in EVERY real
    band AT ONCE - the identical signal
    tile_renderer._mask_uninitialized_fill excludes from rendering (see its
    docstring for why this is safe), applied here too: Total Area was found
    inflated by the same uncounted fake-fill fraction on the same files the
    render bug hit. This is NOT a return of Wave H's bug - a lone band
    reading 0 while others carry real data (Wave H's actual failure case)
    still always counts; only every real band landing on 0 simultaneously
    does not."""
    count = 0
    total = 0.0
    total_sq = 0.0
    minv = math.inf
    maxv = -math.inf
    mask_band = vrt.count
    real_band_count = mask_band - 1
    check_uninitialized = real_band_count >= 3
    for win in _iter_windows(vrt.width, vrt.height, block):
        band1 = vrt.read(1, window=win)
        alpha = vrt.read(mask_band, window=win)
        valid = alpha > 0
        if check_uninitialized:
            all_bands = vrt.read(list(range(1, real_band_count + 1)), window=win)
            valid = valid & ~(all_bands == 0).all(axis=0)
        arr = band1[valid]
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
        projected_metres = bool(
            crs and crs.is_projected and (crs.linear_units or "").lower() in {"metre", "meter", "m"}
        )
        # Always warp - even when already projected in metres, where the
        # target CRS is the source's OWN crs. That's a verified lossless,
        # bit-exact passthrough for an already north-up source (the common
        # case), but a WarpedVRT unconditionally produces a north-up
        # destination grid, so this is what reveals - and, via `add_alpha`,
        # correctly geometry-masks - any corner fill for the rare source
        # whose own geotransform carries genuine rotation. Geographic
        # sources still go through the same lazy equal-area reprojection as
        # before; the only change is the mask mechanism, not the target CRS
        # choice.
        target_crs = crs if projected_metres else EQUAL_AREA_CRS
        area_crs = crs.to_string() if projected_metres else EQUAL_AREA_CRS
        with WarpedVRT(src, crs=target_crs, resampling=Resampling.nearest, add_alpha=True) as vrt:
            if has_legend:
                counts = _accumulate_counts(vrt, block)
            else:
                minv, maxv, mean, std, count = _accumulate_band_stats(vrt, block)
            pixel_area_ha = _pixel_area_m2(vrt.transform) / 10_000.0

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


def _classified_rgba(arr: np.ndarray, legend: Legend, valid_mask: np.ndarray) -> np.ndarray:
    """Per-value class-color render - only meaningful when a legend actually
    names what each value means (LULC and any other legend-driven upload).
    `valid_mask` (Wave: geometric padding fix) is the real per-pixel mask
    from the SAME warp that produced `arr` (see render_preview) - wherever
    it's False, that pixel is warp-fill padding, geometrically, regardless
    of its raw value; a legend-defined class 0 (e.g. Water) renders normally
    everywhere it's True."""
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for v in np.unique(arr).tolist():
        hexc = color_for_value(int(v), legend).lstrip("#")
        r, g, b = (int(hexc[i : i + 2], 16) for i in (0, 2, 4))
        rgba[arr == v] = (r, g, b, 235)
    rgba[~valid_mask] = (0, 0, 0, 0)
    return rgba


def _band_composite_rgba(
    vrt, out_h: int, out_w: int, real_band_count: int, valid_mask: np.ndarray
) -> np.ndarray:
    """Real band-to-RGB composite for raw/unclassified imagery: first 3 bands
    as R/G/B (1 band repeated to grayscale if that's all there is), each
    contrast-stretched independently. Not a "true color" render (band order
    isn't known to carry any particular meaning without per-band role
    metadata this pipeline doesn't collect) - just a genuine visual of the
    actual data, instead of a classified-raster color palette applied to
    continuous values (which produces meaningless per-pixel color noise).

    `valid_mask` (Wave: geometric padding fix) is the warp's own real
    geometric alpha/mask band (see render_preview), decimated to this same
    (out_h, out_w) - the actual answer for padding a WARP introduced,
    independent of what value ended up there, replacing the old blanket
    "every requested band exactly 0" guess (Wave E).

    Wave: production render audit. That geometric mask can't see padding an
    upload's OWN source array already had baked into ordinary values before
    reaching this app (see reproject_to_4326's "Known limitation") - proven
    against a real ad-hoc upload where ~40% of pixels are an exact `0` in
    every one of 3 real bands simultaneously, confined to an irregular blob,
    while the geometric mask reports 100% valid. With >=3 real distinct
    bands (`n == 3` below) that combination is what synthetic/uninitialized
    fill looks like, not real imagery - a genuine measurement varies at
    least slightly band-to-band - so it's excluded here too, same as
    tile_renderer._mask_uninitialized_fill. Not extended to the n == 1
    (grayscale) case: with only one real band there's no cross-band signal
    to tell real (possibly legitimately dark) data apart from fill."""
    n = 3 if real_band_count >= 3 else 1
    raw = vrt.read(
        list(range(1, n + 1)), out_shape=(n, out_h, out_w), resampling=Resampling.nearest
    )
    if n == 3:
        valid_mask = valid_mask & ~(raw == 0).all(axis=0)

    channels = [_percentile_stretch_uint8(raw[i], valid_mask) for i in range(n)]
    if n == 1:
        channels = channels * 3
    r, g, b = channels
    rgba = np.dstack([r, g, b, np.full_like(r, 235)])
    rgba[~valid_mask, 3] = 0
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
        src, crs=DISPLAY_CRS, resampling=Resampling.nearest, add_alpha=True
    ) as vrt:
        real_band_count = vrt.count - 1  # the last band is the synthetic alpha add_alpha appended
        scale = max(vrt.width, vrt.height) / max_dim
        out_w = max(1, int(vrt.width / scale)) if scale > 1 else vrt.width
        out_h = max(1, int(vrt.height / scale)) if scale > 1 else vrt.height
        alpha = vrt.read(vrt.count, out_shape=(out_h, out_w), resampling=Resampling.nearest)
        valid_mask = alpha > 0

        if legend:
            arr = vrt.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest)
            rgba = _classified_rgba(arr, legend, valid_mask)
        else:
            rgba = _band_composite_rgba(vrt, out_h, out_w, real_band_count, valid_mask)

    Image.fromarray(rgba, mode="RGBA").save(out_path)


def has_real_mask(path: str) -> bool:
    """True if `path` already carries a real, warp-derived geometric mask
    (this module's `add_alpha`/`write_mask`, above) rather than no mask at
    all or the old value-based `nodata` heuristic it replaced. A cheap,
    header-only check - no pixel data is read - so a caller can flag a layer
    ingested before this fix without a DB migration or backfill job (see
    ProjectService._needs_reingestion). Per this module's own "Known
    limitation" above, a False result can only be resolved by re-ingesting
    from the original source file - there is nothing to recompute from the
    file alone."""
    with rasterio.open(path) as ds:
        flags = ds.mask_flag_enums[0]
    return MaskFlags.per_dataset in flags and MaskFlags.all_valid not in flags


def reproject_to_4326(
    src_path: str, dst_path: str, block: int = 2048
) -> tuple[str, tuple[float, float, float, float]]:
    """
    Stream a reprojection to EPSG:4326 into a tiled GeoTIFF, tile-by-tile.
    Returns (source_crs_string, bounds_4326 as (minx,miny,maxx,maxy)).

    Wave: geometric padding fix. Alongside the real bands, a synthetic
    "fully valid" band is warped through the IDENTICAL transform/resampling
    (`add_alpha=True` - see this module's docstring) and used to write a
    REAL internal mask band onto `dst_path` ITSELF - not a separate shadow
    file, not app-only metadata. Any tool that opens this GeoTIFF later
    (including outside this app) sees the correct valid-data area; rio-cogeo
    (ingestion/cog.py) carries the mask through COG conversion unchanged
    (verified: mask coverage is bit-identical before/after). This replaces
    the old value-based `padding_value()` guess (removed) with the only
    thing that can actually distinguish "genuine class value" from
    "warp-introduced fill": where the warp geometrically placed real source
    data, independent of what value ended up there. No `nodata` value is
    written on the output at all - the mask is the single source of truth,
    so a real class value that happens to equal whatever a nodata sentinel
    would have been can never collide with it again.
    """
    with rasterio.open(src_path) as src:
        src_crs = src.crs.to_string() if src.crs else "unknown"
        real_band_count = src.count
        with WarpedVRT(
            src, crs=DISPLAY_CRS, resampling=Resampling.nearest, add_alpha=True
        ) as vrt:
            profile = vrt.profile.copy()
            profile.update(
                driver="GTiff", tiled=True, blockxsize=512, blockysize=512,
                compress="deflate", predictor=1, count=real_band_count,
            )
            profile.pop("nodata", None)
            with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True), rasterio.open(
                dst_path, "w", **profile
            ) as dst:
                for win in _iter_windows(vrt.width, vrt.height, block):
                    data = vrt.read(window=win)
                    dst.write(data[:real_band_count], window=win)
                    dst.write_mask(data[-1] > 0, window=win)
    # Read back the written file's bounds and normalise numerically to 4326.
    with rasterio.open(dst_path) as d:
        b = transform_bounds(d.crs, DISPLAY_CRS, *d.bounds)
    return src_crs, (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
