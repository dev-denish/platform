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
EQUAL_AREA_CRS = "EPSG:6933"  # World Equidistant Cylindrical (metres) - global equal-area

DEFAULT_PALETTE = [
    "#2C6B2F", "#C9A227", "#3A7CA5", "#8C8C8C", "#B5895B", "#8FBF5F",
    "#7D3C98", "#E67E22", "#16A085", "#C0392B", "#2980B9", "#34495E",
]

Legend = dict[str, dict[str, str]] | None


@dataclass(frozen=True)
class RasterStats:
    class_area_ha: dict[str, float]  # label -> hectares
    total_area_ha: float
    area_crs: str  # which CRS the area was measured in (provenance)


def color_for_value(value: int, legend: Legend) -> str:
    if legend and str(value) in legend:
        c = legend[str(value)].get("color")
        if c:
            return c
    return DEFAULT_PALETTE[value % len(DEFAULT_PALETTE)]


def label_for_value(value: int, legend: Legend) -> str:
    if legend and str(value) in legend:
        return legend[str(value)].get("label", f"class_{value}")
    return f"class_{value}"


def _iter_windows(width: int, height: int, block: int) -> Iterator[Window]:
    for row in range(0, height, block):
        h = min(block, height - row)
        for col in range(0, width, block):
            w = min(block, width - col)
            yield Window(col, row, w, h)


def _pixel_area_m2(transform) -> float:
    """Ground area of one pixel from the affine transform (linear units of the CRS)."""
    return abs(transform.a * transform.e)


def _accumulate_counts(dataset, block: int, nodata) -> dict[int, int]:
    """Windowed pass over band 1; returns {pixel_value: count}. Memory O(block^2)."""
    counts: dict[int, int] = {}
    for win in _iter_windows(dataset.width, dataset.height, block):
        arr = dataset.read(1, window=win)
        if nodata is not None:
            arr = arr[arr != nodata]
        if arr.size == 0:
            continue
        vals, cnts = np.unique(arr, return_counts=True)
        for v, c in zip(vals.tolist(), cnts.tolist(), strict=True):
            counts[int(v)] = counts.get(int(v), 0) + int(c)
    return counts


def compute_stats(src_path: str, legend: Legend, block: int = 2048) -> RasterStats:
    """Class-wise area in hectares, measured on an equal-area grid in metres."""
    with rasterio.open(src_path) as src:
        crs = src.crs
        nodata = src.nodata
        projected_metres = bool(
            crs and crs.is_projected and (crs.linear_units or "").lower() in {"metre", "meter", "m"}
        )
        if projected_metres:
            # Exact: measure on the native grid, no resampling error.
            counts = _accumulate_counts(src, block, nodata)
            pixel_area_ha = _pixel_area_m2(src.transform) / 10_000.0
            area_crs = crs.to_string()
        else:
            # Geographic/other: measure through a lazy equal-area reprojection.
            with WarpedVRT(src, crs=EQUAL_AREA_CRS, resampling=Resampling.nearest) as vrt:
                counts = _accumulate_counts(vrt, block, vrt.nodata)
                pixel_area_ha = _pixel_area_m2(vrt.transform) / 10_000.0
                area_crs = EQUAL_AREA_CRS

    class_area = {
        label_for_value(v, legend): round(c * pixel_area_ha, 4)
        for v, c in sorted(counts.items())
    }
    total = round(sum(counts.values()) * pixel_area_ha, 4)
    return RasterStats(class_area_ha=class_area, total_area_ha=total, area_crs=area_crs)


def render_preview(src_path: str, out_path: str, legend: Legend, max_dim: int = 2048) -> None:
    """Decimated, colourised RGBA PNG for the map overlay. Never reads full-res."""
    with rasterio.open(src_path) as src, WarpedVRT(
        src, crs=DISPLAY_CRS, resampling=Resampling.nearest
    ) as vrt:
        scale = max(vrt.width, vrt.height) / max_dim
        out_w = max(1, int(vrt.width / scale)) if scale > 1 else vrt.width
        out_h = max(1, int(vrt.height / scale)) if scale > 1 else vrt.height
        arr = vrt.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest)
        nodata = vrt.nodata

    rgba = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    for v in np.unique(arr).tolist():
        if nodata is not None and v == nodata:
            continue
        hexc = color_for_value(int(v), legend).lstrip("#")
        r, g, b = (int(hexc[i : i + 2], 16) for i in (0, 2, 4))
        rgba[arr == v] = (r, g, b, 235)
    if nodata is not None:
        rgba[arr == nodata] = (0, 0, 0, 0)
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
