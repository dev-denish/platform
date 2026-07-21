"""
XYZ map tile rendering from a Cloud-Optimized GeoTIFF (Phase 3 Wave A).

Dynamic (render-on-request) tiling via rio-tiler (pinned) - not a pre-generated
tile pyramid. This is the standard modern approach for exactly this scale: it
needs no cache-warming step, serves every zoom level from one COG, and the COG's
own overviews (built by ingestion/cog.py) keep each request's I/O bounded to
roughly one overview level's worth of data, not the full-resolution raster.

Format: PNG, not WEBP. Classified rasters are discrete class values, not
photographic RGB - PNG is lossless, so hard class boundaries render pixel-exact
with no compression artefacts blurring a boundary between two classes (a real
interpretive/compliance concern for an MRV product, not just a quality nicety).
PNG also needs no browser feature-detection and matches the format
`raster.render_preview` already uses for the same reason.

Bugfix (Phase 3 Wave C): a classified colormap was being built and applied
unconditionally, regardless of whether the COG was actually single-band
classified data - a real multi-band "Satellite / Raw Imagery" scene made
rio_tiler raise `InvalidFormat("Source data must be 1 band")` on every tile
request. Branch on the COG's actual band count instead of assuming.

Bugfix (Phase 3 Wave E): a rotated/irregular real scene reprojected onto an
axis-aligned lat/lng grid (raster.reproject_to_4326) leaves warp-fill padding
around its actual footprint - and when the SOURCE raster never had a `nodata`
value set, that padding is literal 0 in every band with no nodata marker
anywhere to say so. Treat "every requested band exactly 0" as an additional
mask on top of whatever rio-tiler already masks, so those pixels are excluded
from the stretch calculation AND rendered transparent instead of opaque black.

Symbology (Phase 3 Wave F): band-to-channel assignment, stretch percentiles,
and per-class color overrides, all applied live at request time - no
re-ingestion. Two independent render modes:
  - "classified": a persisted `class_legend` colors band 1 by class value
    (LULC etc.) - `color_overrides` lets a caller override specific class
    colors without touching the stored legend.
  - "raw bands": an explicit band composite (1 band -> grayscale, 3 bands ->
    RGB) with a configurable percentile stretch - the mode Wave C/E's
    band-composite logic already used as the only option for unclassified
    data, now also reachable (and reconfigurable) for a classified layer's
    raw underlying bands if a caller explicitly asks for it.
Mode selection: `bands` given (even for a 1-band COG) -> raw bands, always.
No `bands` AND a legend exists -> classified. Otherwise -> raw bands with the
Wave C/E defaults (first 3 bands or grayscale, 2nd-98th percentile) - i.e.
identical output to before this wave when no new params are passed at all.
"""
from __future__ import annotations

import math

import numpy as np
from rio_tiler.io import Reader

from app.core.errors import ValidationError
from app.services.ingestion.raster import color_for_value, legend_defines, padding_value

_TILE_FORMAT = "PNG"
_DEFAULT_STRETCH = (2, 98)


def _colormap_for_uint8(
    nodata: float | None,
    legend: dict | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[int, tuple[int, int, int, int]]:
    """Full 0-255 colormap so rio-tiler can map every possible class byte value.
    `overrides` (value-string -> "#rrggbb") take precedence over the legend's
    own color for that value, which takes precedence over DEFAULT_PALETTE - the
    same fallback chain `color_for_value` already implements, just with one
    more rung on top.

    Bugfix (Phase 3 Wave G): classified rasters never got `_mask_warp_fill`'s
    treatment (that only ever applied to the multi-band raw-imagery render
    path) - a classified layer with no real nodata tag rendered its
    reproject_to_4326 warp-fill padding as an opaque fake class color instead
    of transparent (`color_for_value` falls through to DEFAULT_PALETTE[0] for
    an unlisted value 0, painting it solid). `padding_value` is the same
    single definition `compute_stats` now excludes from area entirely - both
    paths agree on what counts as padding."""
    cmap: dict[int, tuple[int, int, int, int]] = {}
    pad = padding_value(nodata, legend)
    for v in range(256):
        if pad is not None and v == pad:
            cmap[v] = (0, 0, 0, 0)
            continue
        override = overrides.get(str(v)) if overrides else None
        hexc = (override or color_for_value(v, legend)).lstrip("#")
        r, g, b = (int(hexc[i : i + 2], 16) for i in (0, 2, 4))
        cmap[v] = (r, g, b, 235)
    return cmap


def _mask_warp_fill(img) -> None:
    """Mask pixels that are exactly 0 in EVERY requested band, on top of
    whatever rio-tiler already masks. Real multi-band reflectance reading
    exactly integer 0 in every single band simultaneously is essentially
    never genuine ground data - it's the warp-fill padding reproject_to_4326
    leaves around a rotated/irregular scene's real footprint, and there's no
    `nodata` value recorded here to catch it any other way (see module
    docstring). Mutates `img` in place; a no-op if nothing is exactly zero."""
    zero_fill = np.all(img.data == 0, axis=0)
    if not zero_fill.any():
        return
    mask = np.ma.getmaskarray(img.array) | np.broadcast_to(zero_fill, img.array.shape)
    img.array = np.ma.masked_where(mask, img.array.data)


def _percentile_ranges(stats, lo_pct: int, hi_pct: int) -> list[tuple[float, float]]:
    lo_key, hi_key = f"percentile_{lo_pct}", f"percentile_{hi_pct}"
    ranges = []
    for v in stats.values():
        d = v.model_dump()
        lo, hi = d[lo_key], d[hi_key]
        if math.isnan(lo) or math.isnan(hi):
            # Every pixel in this band is masked (e.g. an all-padding tile) -
            # the range is moot, nothing will render but transparency anyway.
            lo, hi = 0.0, 1.0
        elif hi <= lo:
            hi = lo + 1
        ranges.append((lo, hi))
    return ranges


def read_pixel(cog_path: str, lon: float, lat: float, legend: dict | None = None) -> list[float | None]:
    """Phase 3 Wave D: the real per-band value at one EPSG:4326 lon/lat, for
    pixel/attribute inspection. Raw numbers only, in native band order - no
    colormap/legend interpretation here (the caller already has the layer's
    class_legend from GET /projects/{id}/layers and maps a value to its label
    itself, same as the frontend's own Symbology panel already does - no
    reason to duplicate that lookup server-side). `legend` is only consulted
    for the padding heuristic below, never to relabel the returned values.

    Raises `rio_tiler.errors.PointOutsideBounds` if the point isn't covered by
    this raster - same "not a failure, just no data here" contract as
    `render_tile`'s `TileOutsideBounds`, mapped by the caller to a clean 404.
    """
    with Reader(cog_path) as cog:
        point = cog.point(lon, lat)
    data = np.ma.getdata(point.array)
    mask = np.ma.getmaskarray(point.array)
    if not mask.any() and bool(np.all(data == 0)) and not legend_defines(0, legend):
        # Mirrors _mask_warp_fill's reasoning above: every band reading exactly
        # 0 at once is the warp-fill padding reproject_to_4326 leaves around a
        # rotated/irregular scene's real footprint when no nodata value was
        # ever recorded - not genuine data. Tiles already render this padding
        # fully transparent, so a click there should report "no data", not a
        # fabricated all-zero reading. Skipped when the legend names class 0
        # as real (e.g. Water=0) - same ambiguity padding_value resolves.
        mask = np.ones_like(mask)
    return [None if m else float(v) for v, m in zip(data, mask)]


def _validate_bands(bands: tuple[int, ...], band_count: int) -> None:
    for b in bands:
        if b < 1 or b > band_count:
            raise ValidationError(
                f"Band {b} is out of range - this layer has {band_count} band(s)."
            )


def render_tile(
    cog_path: str,
    z: int,
    x: int,
    y: int,
    *,
    legend: dict | None = None,
    bands: tuple[int, ...] | None = None,
    stretch: tuple[int, int] | None = None,
    color_overrides: dict[str, str] | None = None,
) -> bytes:
    """Render one PNG tile. Raises `rio_tiler.errors.TileOutsideBounds` if the
    requested tile doesn't intersect the raster - the normal, expected outcome
    for XYZ requests at a viewport's edges, not a failure. The caller (the tiles
    API route) maps that to a clean 404, matching how any XYZ tile server
    behaves for out-of-coverage tiles. Raises `ValidationError` (-> 422) for an
    out-of-range band index - a client input mistake, not a server failure."""
    with Reader(cog_path) as cog:
        band_count = cog.dataset.count
        if bands is not None:
            _validate_bands(bands, band_count)

        if bands is None and legend:
            # Classified: band 1 holds class values by convention (matches
            # compute_stats' own assumption for legend-driven data).
            img = cog.tile(x, y, z)
            colormap = _colormap_for_uint8(cog.dataset.nodata, legend=legend, overrides=color_overrides)
            return img.render(img_format=_TILE_FORMAT, colormap=colormap)

        # Raw band composite: there are no discrete "classes" here (or the
        # caller explicitly asked to bypass classification), so a colormap
        # doesn't apply - render a real band composite instead (1 band ->
        # grayscale, 3 bands -> RGB), contrast-stretched from this tile's own
        # percentile range so raw reflectance values land in a visible 0-255
        # range.
        # ponytail: the stretch is per-tile, not one fixed per-layer range, so
        # exposure can vary slightly tile-to-tile - a real fix means storing a
        # per-layer statistic. Not done here: it's a bigger, separate change
        # (would need the stat computed once at ingest and persisted), and
        # per-tile stretching already gives correct, just not perfectly
        # uniform, results.
        indexes = bands if bands is not None else ((1, 2, 3) if band_count >= 3 else (1,))
        lo_pct, hi_pct = stretch if stretch is not None else _DEFAULT_STRETCH

        if len(indexes) == 3:
            img = cog.tile(x, y, z, indexes=indexes)
            _mask_warp_fill(img)
            ranges = _percentile_ranges(img.statistics(percentiles=[lo_pct, hi_pct]), lo_pct, hi_pct)
        else:
            solo = cog.tile(x, y, z, indexes=indexes)
            _mask_warp_fill(solo)
            ranges = _percentile_ranges(solo.statistics(percentiles=[lo_pct, hi_pct]), lo_pct, hi_pct) * 3
            img = cog.tile(x, y, z, indexes=indexes * 3)
            _mask_warp_fill(img)
        img.rescale(in_range=ranges, out_range=((0, 255),) * 3)
        return img.render(img_format=_TILE_FORMAT)
