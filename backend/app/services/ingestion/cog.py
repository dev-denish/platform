"""
Cloud-Optimized GeoTIFF conversion (Phase 3 Wave A).

Existing implementation (Phase 1/2): the ingest pipeline reprojects to EPSG:4326
and writes a plain tiled GeoTIFF (`raster.reproject_to_4326`) - correct for
windowed reads and for the /previews overlay, but NOT internally overviewed, so
serving map tiles from it directly would mean rio-tiler re-decimating arbitrary
windows from a non-optimized file on every request.

Enterprise solution: a real Cloud-Optimized GeoTIFF via rio-cogeo (pinned - never
hand-rolled GDAL COG creation, which is easy to get subtly wrong: internal
tiling, overview count/alignment, and the specific IFD layout COG readers expect
are exactly what rio-cogeo exists to get right). `nearest` resampling for both
the overview build and any decimation matches every other resampling choice in
raster.py: our rasters are classified/categorical (discrete class values), and
bilinear/cubic would blend adjacent classes into meaningless intermediate
numbers.
"""
from __future__ import annotations

from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles


def convert_to_cog(src_path: str, dst_path: str) -> None:
    """Convert an already-ingested raster (the reprojected EPSG:4326 GeoTIFF this
    platform stores as a layer's `file_key`) into a COG at `dst_path`. Raises
    whatever rio-cogeo/rasterio raises on a genuinely broken source - the caller
    (workers/jobs.py) decides whether that should fail the whole ingest job."""
    profile = dict(cog_profiles.get("deflate"))
    cog_translate(
        src_path,
        dst_path,
        profile,
        overview_resampling="nearest",
        resampling="nearest",
        quiet=True,
    )
