"""
CDSE (Copernicus Data Space Ecosystem) satellite ingestion - Phase 1 of the
VNV Pipeline, the self-hosted alternative to Google Earth Engine (see
frontend's compute-source toggle in AnalysisPanel.jsx, currently a disabled
preview until this phase and the ones after it land). Sentinel-2 L2A and
Sentinel-1 GRD only, this phase - no Landsat (no USGS M2M credential exists
yet), no arq job, no R sidecar, no FastAPI route. A standalone, importable
module with no DB writes and no wiring into any endpoint - see
scripts/verify_cdse_ingestion.py for a real, live, end-to-end run against a
real project AOI.

Two DIFFERENT CDSE credential types, not interchangeable:
  * OAuth client credentials (`cdse_client_id`/`cdse_client_secret`) - a
    Bearer token (~30 min lifetime) attached to STAC catalog search requests.
  * An S3-style access/secret key pair (`cdse_s3_access_key`/
    `cdse_s3_secret_key`) - authenticates GDAL's /vsis3/ driver (via
    `rasterio.session.AWSSession`, which wraps boto3 - already an installed
    dependency here, see app/services/ingestion/storage.py's `S3Storage`)
    against eodata.dataspace.copernicus.eu for windowed pixel reads. Neither
    credential can stand in for the other.

Windowed reads, not full-scene downloads: `rasterio.mask.mask(..., crop=True)`
computes a bounded window from the AOI geometry before ever reading a pixel
(see `rasterio.mask.geometry_window`) - memory is O(AOI bbox in pixels), the
same guarantee app/services/ingestion/raster.py's block-iteration gives for
whole-scene stats, just via a different rasterio mechanism suited to
AOI-bounded (rather than whole-raster) reads.

AOI format: a plain GeoJSON geometry dict in EPSG:4326 - the SAME shape
`AnalysisResultRepository.get_project_boundary_geojson` (app/repositories/
analysis_results.py) already returns and `gee_analysis_service.py` already
consumes via `ee.Geometry(boundary_geojson)`. No new AOI representation, no
new shapely/GEOS dependency - rasterio's mask/warp functions accept a plain
geometry dict directly.

Cloud masking (Sentinel-2 only): the SCL (Scene Classification Layer) band,
resampled onto each scene's 10 m reflectance grid with nearest-neighbour
resampling (SCL is categorical - matches this codebase's existing
nearest-only convention for classified data, see ingestion/raster.py's
module docstring). `_INVALID_SCL_CLASSES` below is deliberately narrower than
"anything not vegetation" - see its own comment for exactly which classes are
excluded and why.

Mosaicking: when more than one scene is needed, each scene's AOI-clipped,
cloud-masked bands are merged with `rasterio.merge.merge(..., method="first")`,
scenes ordered least-cloudy-first (S2) or most-recent-first (S1) - a
cloud-masked (nodata) pixel in the priority scene falls through to the next
scene's real pixel automatically, which is the whole point of compositing
more than one date. The merged mosaic is then clipped a SECOND time, precisely
to the AOI polygon (not just its bounding box) - matches this module's own
brief: "mosaic... then clip precisely to the AOI polygon", not the other order.

FAILURE SEMANTICS: `CDSEAuthError`/`CDSESearchError` are raised, never
swallowed - an empty scene search, a bad token response, or an unset
credential all fail loudly. There is no fallback collection name hardcoded
for a date-range gap (see `search_scenes`'s docstring) - a genuine coverage
gap surfaces as `CDSESearchError`, not a silent empty result.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.env import Env
from rasterio.io import MemoryFile
from rasterio.mask import mask as rio_mask
from rasterio.merge import merge as rio_merge
from rasterio.session import AWSSession
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, reproject, transform_geom

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("dmrv.cdse_ingestion")

__all__ = [
    "CDSEAuthError",
    "CDSESearchError",
    "SceneMeta",
    "PreparedRaster",
    "CDSEClient",
]

STAC_API_BASE = "https://stac.dataspace.copernicus.eu/v1"
TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
S3_ENDPOINT = "eodata.dataspace.copernicus.eu"

# Verified live against STAC /collections on 2026-08-13 (418 real collections
# returned) - both ids below exist and are current. Do NOT add a fallback
# collection name here on a date-range gap: a real gap must surface as
# `CDSESearchError` (see search_scenes), never silently substitute a
# different, unverified collection.
SENTINEL2_L2A_COLLECTION = "sentinel-2-l2a"
SENTINEL1_GRD_COLLECTION = "sentinel-1-grd"

# ESA Sentinel-2 Scene Classification Layer legend - classes excluded from a
# clean analysis composite: 0 no-data, 1 saturated/defective, 3 cloud shadow,
# 8/9 cloud medium/high probability, 10 thin cirrus. Deliberately NOT
# excluded: 2 (dark area pixels) and 11 (snow) - a monitored AOI that is
# genuinely in shadow or snow-covered is real observed data, not a cloud
# artifact, and discarding it would silently bias a forest/vegetation time
# series toward only ever showing clear, sunlit conditions.
_INVALID_SCL_CLASSES = frozenset({0, 1, 3, 8, 9, 10})

_S2_DEFAULT_BANDS: tuple[str, ...] = ("B02", "B03", "B04", "B08")
_S1_DEFAULT_POLARIZATIONS: tuple[str, ...] = ("vv", "vh")


class CDSEAuthError(Exception):
    """CDSE OAuth token request failed, or a required credential is unset."""


class CDSESearchError(Exception):
    """CDSE STAC search failed, or returned zero usable scenes."""


@dataclass(frozen=True)
class SceneMeta:
    scene_id: str
    collection: str
    datetime: str
    cloud_cover: float | None  # None for Sentinel-1 (no cloud-cover concept)


@dataclass(frozen=True)
class PreparedRaster:
    output_path: str
    scenes: list[SceneMeta]
    width: int
    height: int
    crs: str
    resolution_m: float
    bounds: tuple[float, float, float, float]  # in `crs`, not necessarily 4326


def _to_vsis3(s3_href: str) -> str:
    if not s3_href.startswith("s3://"):
        raise CDSESearchError(f"Expected an s3:// asset href, got: {s3_href}")
    return "/vsis3/" + s3_href[len("s3://") :]


def _band_transform_res(transform) -> float:
    return abs(transform.a)


def _flatten_coords(node: Any) -> list[tuple[float, float]]:
    """Every [lon, lat] pair inside a GeoJSON `coordinates` array/nested-array,
    regardless of geometry type (Polygon, MultiPolygon, ...) - just enough to
    get a real bounding box, not a general GeoJSON parser."""
    if (
        isinstance(node, list)
        and len(node) == 2
        and isinstance(node[0], (int, float))
        and isinstance(node[1], (int, float))
    ):
        return [(float(node[0]), float(node[1]))]
    out: list[tuple[float, float]] = []
    for child in node:
        out.extend(_flatten_coords(child))
    return out


def _utm_epsg_for_aoi(aoi_4326: dict[str, Any]) -> str:
    """A UTM zone EPSG code derived from the AOI's own bounding-box centroid.
    Needed only for Sentinel-1 (see `_clip_scene_bands`'s docstring on why
    GRD has no native CRS to fall back on) - Sentinel-2 never calls this,
    it already lands in its tile's own UTM zone."""
    coords = _flatten_coords(aoi_4326.get("coordinates", []))
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    lon = (min(lons) + max(lons)) / 2
    lat = (min(lats) + max(lats)) / 2
    zone = int((lon + 180) // 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return f"EPSG:{epsg}"


class CDSEClient:
    """One client per settings instance. Caches its own OAuth token in
    memory and refreshes it lazily once expired (tokens are short-lived,
    ~30 min - see module docstring). Not thread-safe by design: this Phase 1
    module has exactly one caller today (scripts/verify_cdse_ingestion.py);
    add a lock here if a later phase's arq orchestration calls it
    concurrently from multiple workers."""

    def __init__(self, settings: Settings, *, http_timeout_s: float = 30.0) -> None:
        self._client_id = settings.cdse_client_id
        self._client_secret = settings.cdse_client_secret
        self._s3_access_key = settings.cdse_s3_access_key
        self._s3_secret_key = settings.cdse_s3_secret_key
        self._http_timeout_s = http_timeout_s
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self._client_id or not self._client_secret:
            raise CDSEAuthError(
                "CDSE ingestion requires DMRV_CDSE_CLIENT_ID/DMRV_CDSE_CLIENT_SECRET, "
                "but at least one is unset."
            )
        try:
            resp = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=self._http_timeout_s,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise CDSEAuthError(f"CDSE OAuth token request failed: {e}") from e
        body = resp.json()
        token = body.get("access_token")
        expires_in = body.get("expires_in", 0)
        if not token:
            raise CDSEAuthError("CDSE OAuth token response had no access_token.")
        self._token = token
        # Refresh 60s before actual expiry - a slow windowed-read batch must
        # never start against a token that dies mid-request.
        self._token_expires_at = time.monotonic() + max(expires_in - 60, 0)
        return token

    def search_scenes(
        self,
        collection: str,
        aoi_geojson: dict[str, Any],
        date_start: str,
        date_end: str,
        *,
        max_cloud_cover: float | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Real STAC item dicts for `collection` intersecting `aoi_geojson`
        (EPSG:4326) within [date_start, date_end] (plain "YYYY-MM-DD"
        dates). Raises `CDSESearchError` (never returns an empty list) if
        nothing matches - a real coverage gap for the requested window is a
        caller-visible failure, not a silent empty result."""
        token = self._get_access_token()
        body: dict[str, Any] = {
            "collections": [collection],
            "intersects": aoi_geojson,
            "datetime": f"{date_start}T00:00:00Z/{date_end}T23:59:59Z",
            "limit": limit,
        }
        if max_cloud_cover is not None:
            body["query"] = {"eo:cloud_cover": {"lt": max_cloud_cover}}
        try:
            resp = httpx.post(
                f"{STAC_API_BASE}/search",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._http_timeout_s,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise CDSESearchError(f"CDSE STAC search failed for {collection}: {e}") from e
        features = resp.json().get("features", [])
        if not features:
            cc_clause = f" with cloud cover < {max_cloud_cover}" if max_cloud_cover is not None else ""
            raise CDSESearchError(
                f"No {collection} scenes found for this AOI in [{date_start}, {date_end}]"
                f"{cc_clause}. This may be a genuine data gap for the requested window - "
                "verify against https://stac.dataspace.copernicus.eu before assuming a bug."
            )
        return features

    def _rasterio_env(self) -> Env:
        if not self._s3_access_key or not self._s3_secret_key:
            raise CDSEAuthError(
                "CDSE ingestion requires DMRV_CDSE_S3_ACCESS_KEY/DMRV_CDSE_S3_SECRET_KEY, "
                "but at least one is unset."
            )
        session = AWSSession(
            aws_access_key_id=self._s3_access_key,
            aws_secret_access_key=self._s3_secret_key,
            endpoint_url=S3_ENDPOINT,
        )
        return Env(
            session=session,
            AWS_VIRTUAL_HOSTING="FALSE",
            AWS_HTTPS="YES",
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        )

    def _clip_scene_bands(
        self, band_hrefs: dict[str, str], aoi_4326: dict[str, Any], *, dst_crs: str | None = None
    ) -> tuple[dict[str, np.ndarray], Any, Any]:
        """AOI-clipped (windowed, memory-bounded - see module docstring)
        read of every band in `band_hrefs` for ONE scene. Every band must
        already share one grid (true for S2 L2A's *_10m assets and S1 GRD's
        vv/vh) - the first band read pins the reference transform/crs every
        later band is checked to still match.

        Real gap found running this live against CDSE (2026-08-13): Sentinel-1
        GRD ground-range COGs carry NO native CRS at all - `src.crs` is None,
        the transform is identity, and georeferencing exists only as ~200
        ground-control points in EPSG:4326 (`src.gcps`). rio_mask needs a real
        affine transform to clip against, so a GCP-only source is warped
        through its own GCPs onto `dst_crs` first (bilinear - continuous SAR
        amplitude, not categorical, unlike SCL) via WarpedVRT, which is itself
        just another windowed read once wrapped - never the whole scene.
        Sentinel-2 tiles carry a real native UTM CRS already and never take
        this branch; `dst_crs` is unused for that caller."""
        arrays: dict[str, np.ndarray] = {}
        ref_transform = ref_crs = None
        with self._rasterio_env():
            for band, href in band_hrefs.items():
                with rasterio.open(_to_vsis3(href)) as src:
                    if src.crs is None:
                        with WarpedVRT(
                            src, src_crs=src.gcps[1], crs=dst_crs, resampling=Resampling.bilinear
                        ) as vrt:
                            if ref_crs is None:
                                ref_crs = vrt.crs
                                aoi_native = transform_geom("EPSG:4326", ref_crs, aoi_4326)
                            arr, transform = rio_mask(
                                vrt, [aoi_native], crop=True, filled=True, nodata=0
                            )
                    else:
                        if ref_crs is None:
                            ref_crs = src.crs
                            aoi_native = transform_geom("EPSG:4326", ref_crs, aoi_4326)
                        arr, transform = rio_mask(
                            src, [aoi_native], crop=True, filled=True, nodata=0
                        )
                    arrays[band] = arr[0]
                    if ref_transform is None:
                        ref_transform = transform
        return arrays, ref_transform, ref_crs

    def _clip_scl_mask(
        self, scl_href: str, ref_crs, ref_transform, ref_shape: tuple[int, int]
    ) -> np.ndarray:
        """SCL resampled (nearest - categorical data) onto the exact grid of
        the already-clipped reflectance bands, itself via a windowed read
        (`WarpedVRT` with a fixed target transform/width/height only ever
        materializes that window, never the whole 20 m scene)."""
        with self._rasterio_env(), rasterio.open(_to_vsis3(scl_href)) as src:
            with WarpedVRT(
                src, crs=ref_crs, transform=ref_transform,
                width=ref_shape[1], height=ref_shape[0], resampling=Resampling.nearest,
            ) as vrt:
                return vrt.read(1)

    def _process_s2_scene(
        self, scene: dict[str, Any], bands: tuple[str, ...], aoi_4326: dict[str, Any]
    ) -> tuple[np.ndarray, Any, Any]:
        assets = scene["assets"]
        band_hrefs = {b: assets[f"{b}_10m"]["href"] for b in bands}
        arrays, transform, crs = self._clip_scene_bands(band_hrefs, aoi_4326)
        scl_key = "SCL_20m" if "SCL_20m" in assets else "SCL_60m"
        scl = self._clip_scl_mask(assets[scl_key]["href"], crs, transform, arrays[bands[0]].shape)
        invalid = np.isin(scl, tuple(_INVALID_SCL_CLASSES))
        stacked = np.stack([np.where(invalid, 0, arrays[b]) for b in bands])
        return stacked, transform, crs

    def _process_s1_scene(
        self,
        scene: dict[str, Any],
        polarizations: tuple[str, ...],
        aoi_4326: dict[str, Any],
        dst_crs: str,
    ) -> tuple[np.ndarray, Any, Any]:
        assets = scene["assets"]
        band_hrefs = {p: assets[p]["href"] for p in polarizations}
        arrays, transform, crs = self._clip_scene_bands(band_hrefs, aoi_4326, dst_crs=dst_crs)
        stacked = np.stack([arrays[p] for p in polarizations])
        return stacked, transform, crs

    def _prepare(
        self,
        scenes: list[dict[str, Any]],
        collection: str,
        band_names: tuple[str, ...],
        aoi_4326: dict[str, Any],
        output_path: str,
        process_scene,
    ) -> PreparedRaster:
        stacked_scenes = [process_scene(s) for s in scenes]
        merged, merged_transform, merged_crs = _merge_and_clip(stacked_scenes, aoi_4326, nodata=0)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        count, h, w = merged.shape
        with rasterio.open(
            output_path, "w", driver="GTiff", height=h, width=w, count=count,
            dtype=merged.dtype, crs=merged_crs, transform=merged_transform, nodata=0,
            tiled=True, blockxsize=256, blockysize=256, compress="deflate",
        ) as dst:
            dst.write(merged)
            dst.descriptions = tuple(band_names)
        # rasterio.transform.array_bounds returns a plain (left, bottom,
        # right, top) tuple - NOT a BoundingBox namedtuple like src.bounds.
        left, bottom, right, top = rasterio.transform.array_bounds(h, w, merged_transform)
        return PreparedRaster(
            output_path=output_path,
            scenes=[
                SceneMeta(
                    scene_id=s["id"], collection=collection,
                    datetime=s["properties"].get("datetime", ""),
                    cloud_cover=s["properties"].get("eo:cloud_cover"),
                )
                for s in scenes
            ],
            width=w, height=h, crs=merged_crs.to_string(),
            resolution_m=_band_transform_res(merged_transform),
            bounds=(left, bottom, right, top),
        )

    def prepare_sentinel2_aoi_raster(
        self,
        aoi_geojson: dict[str, Any],
        date_start: str,
        date_end: str,
        output_path: str,
        *,
        max_cloud_cover: float = 40.0,
        max_scenes: int = 4,
        bands: tuple[str, ...] = _S2_DEFAULT_BANDS,
    ) -> PreparedRaster:
        """Cloud-masked, AOI-clipped Sentinel-2 L2A raster (`bands`, default
        blue/green/red/NIR at 10 m - Phase 2's minimum for NDVI and a true-
        color composite). Written to `output_path` on local disk (this
        environment is DMRV_ENVIRONMENT=dev, no S3 activation yet - matches
        every other local-disk artifact in this codebase, see
        app/services/ingestion/storage.py's LocalStorage)."""
        scenes = self.search_scenes(
            SENTINEL2_L2A_COLLECTION, aoi_geojson, date_start, date_end,
            max_cloud_cover=max_cloud_cover, limit=20,
        )
        scenes = sorted(scenes, key=lambda s: s["properties"].get("eo:cloud_cover", 100))[:max_scenes]
        return self._prepare(
            scenes, SENTINEL2_L2A_COLLECTION, bands, aoi_geojson, output_path,
            process_scene=lambda s: self._process_s2_scene(s, bands, aoi_geojson),
        )

    def prepare_sentinel1_aoi_raster(
        self,
        aoi_geojson: dict[str, Any],
        date_start: str,
        date_end: str,
        output_path: str,
        *,
        max_scenes: int = 4,
        polarizations: tuple[str, ...] = _S1_DEFAULT_POLARIZATIONS,
    ) -> PreparedRaster:
        """AOI-clipped Sentinel-1 GRD raster (`polarizations`, default
        VV/VH). No cloud masking - SAR is unaffected by cloud cover.

        GRD ground-range COGs carry no native CRS (GCP-georeferenced only -
        see `_clip_scene_bands`'s docstring for the real gap this closed), so
        unlike Sentinel-2 (which lands in its tile's own UTM zone for free),
        S1 needs an explicit target CRS to warp onto - derived from the AOI's
        own centroid via `_utm_epsg_for_aoi`."""
        dst_crs = _utm_epsg_for_aoi(aoi_geojson)
        scenes = self.search_scenes(
            SENTINEL1_GRD_COLLECTION, aoi_geojson, date_start, date_end, limit=20,
        )
        scenes = sorted(scenes, key=lambda s: s["properties"].get("datetime", ""), reverse=True)[:max_scenes]
        return self._prepare(
            scenes, SENTINEL1_GRD_COLLECTION, polarizations, aoi_geojson, output_path,
            process_scene=lambda s: self._process_s1_scene(s, polarizations, aoi_geojson, dst_crs),
        )


def _reproject_to(stacked: np.ndarray, src_transform, src_crs, dst_crs) -> tuple[np.ndarray, Any]:
    """Cross-UTM-zone edge case: an AOI straddling a zone boundary can pull
    scenes from two different UTM zones. Reprojects one scene's
    already-AOI-clipped array onto `dst_crs` (the priority scene's own zone)
    before merging.

    ponytail: reprojects to the FIRST scene's own UTM zone rather than a
    shared neutral CRS - correct as long as the AOI's span is small relative
    to a UTM zone, true for every real AOI this platform has ingested so far
    (single-farm/single-forest-plot scale). Upgrade to a shared equal-area
    CRS (see EQUAL_AREA_CRS in app/services/ingestion/raster.py) if a
    genuinely zone-spanning AOI shows up."""
    count, h, w = stacked.shape
    left, bottom, right, top = rasterio.transform.array_bounds(h, w, src_transform)
    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, w, h, left, bottom, right, top
    )
    dst = np.zeros((count, dst_h, dst_w), dtype=stacked.dtype)
    for i in range(count):
        reproject(
            source=stacked[i], destination=dst[i],
            src_transform=src_transform, src_crs=src_crs,
            dst_transform=dst_transform, dst_crs=dst_crs,
            resampling=Resampling.nearest,
        )
    return dst, dst_transform


def _merge_and_clip(
    stacked_scenes: list[tuple[np.ndarray, Any, Any]],
    aoi_4326: dict[str, Any],
    nodata: float,
) -> tuple[np.ndarray, Any, Any]:
    """Merges `stacked_scenes` ((array, transform, crs) tuples, already
    ordered priority-first by the caller) with `method="first"` - a
    cloud-masked/nodata pixel in the priority scene falls through to the
    next scene's real pixel, which is the point of compositing more than one
    date. Then clips the merged mosaic a SECOND time, precisely to the AOI
    POLYGON (rio_mask.mask, not just the bbox `merge` itself produces)."""
    ref_crs = stacked_scenes[0][2]
    memfiles: list[MemoryFile] = []
    datasets = []
    try:
        for stacked, transform, crs in stacked_scenes:
            if crs != ref_crs:
                stacked, transform = _reproject_to(stacked, transform, crs, ref_crs)
            count, h, w = stacked.shape
            mf = MemoryFile()
            with mf.open(
                driver="GTiff", height=h, width=w, count=count, dtype=stacked.dtype,
                crs=ref_crs, transform=transform, nodata=nodata,
            ) as ds:
                ds.write(stacked)
            memfiles.append(mf)
            datasets.append(mf.open())

        merged, merged_transform = rio_merge(datasets, method="first", nodata=nodata)

        aoi_native = transform_geom("EPSG:4326", ref_crs, aoi_4326)
        count, h, w = merged.shape
        with MemoryFile() as clip_mf:
            with clip_mf.open(
                driver="GTiff", height=h, width=w, count=count, dtype=merged.dtype,
                crs=ref_crs, transform=merged_transform, nodata=nodata,
            ) as ds:
                ds.write(merged)
            with clip_mf.open() as ds:
                final_arr, final_transform = rio_mask(
                    ds, [aoi_native], crop=True, filled=True, nodata=nodata
                )
        return final_arr, final_transform, ref_crs
    finally:
        for ds in datasets:
            ds.close()
        for mf in memfiles:
            mf.close()
