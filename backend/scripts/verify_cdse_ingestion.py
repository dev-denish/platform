"""
Real, live end-to-end verification for app/services/cdse_ingestion.py (Phase 1
of the VNV Pipeline). NOT a pytest suite - a direct script, because pytest is
not installed in this repo's live Docker images (known standing issue). Prints
real STAC results, real cloud-cover values, and the real output raster's
dimensions/CRS/pixel stats - see the ingestion module's own docstring for what
each step does.

Usage (inside the backend/worker container, which already has network access
to CDSE and the real DMRV_CDSE_* env vars):
    python scripts/verify_cdse_ingestion.py
"""
from __future__ import annotations

import sys

import numpy as np
import rasterio

from app.core.config import get_settings
from app.services.cdse_ingestion import CDSEClient


def _get_real_aoi(project_id: str) -> dict:
    """A real project's unioned Boundary-layer geometry, via the SAME query
    AnalysisResultRepository.get_project_boundary_geojson runs - not
    reimplemented here, just executed directly against the live DB."""
    import json
    import os

    import psycopg

    conn = psycopg.connect(
        host="db", dbname=os.environ["DMRV_DB_NAME"],
        user=os.environ["DMRV_DB_USER"], password=os.environ["DMRV_DB_PASSWORD"],
        row_factory=psycopg.rows.dict_row,
    )
    cur = conn.cursor()
    cur.execute(
        """
        WITH boundary_layer AS (
            SELECT sl.layer_id
            FROM spatial_layer sl
            JOIN dataset d ON d.dataset_id = sl.dataset_id
            WHERE d.project_id = %s AND d.type = 'Boundary' AND d.deleted_at IS NULL
            ORDER BY d.loaded_at DESC LIMIT 1
        )
        SELECT ST_AsGeoJSON(ST_Union(vf.geom)) AS geom
        FROM vector_feature vf JOIN boundary_layer bl ON bl.layer_id = vf.layer_id
        """,
        (project_id,),
    )
    row = cur.fetchone()
    conn.close()
    return json.loads(row["geom"])


def main() -> int:
    settings = get_settings()
    client = CDSEClient(settings)

    # Real project ("rekalakunta"), real boundary layer - queried live above.
    project_id = "fd7d77cb-21b5-4a51-9894-0b073b2b60d5"
    aoi = _get_real_aoi(project_id)
    print(f"AOI: real boundary of project {project_id} ('rekalakunta')")

    date_start, date_end = "2026-06-01", "2026-08-01"

    print("\n=== Sentinel-2 L2A ===")
    s2_result = client.prepare_sentinel2_aoi_raster(
        aoi, date_start, date_end, "/var/lib/dmrv/data/cdse_cache/verify_s2.tif",
        max_cloud_cover=40.0,
    )
    for s in s2_result.scenes:
        print(f"  scene {s.scene_id} | {s.datetime} | cloud_cover={s.cloud_cover}")
    print(f"  output: {s2_result.output_path}")
    print(f"  size: {s2_result.width}x{s2_result.height} px, {s2_result.resolution_m} m/px")
    print(f"  crs: {s2_result.crs}")
    print(f"  bounds: {s2_result.bounds}")
    with rasterio.open(s2_result.output_path) as ds:
        for i in range(1, ds.count + 1):
            band = ds.read(i)
            valid = band[band != 0]
            if valid.size:
                print(
                    f"  band {i} ({ds.descriptions[i - 1]}): "
                    f"min={valid.min()} max={valid.max()} mean={valid.mean():.1f} "
                    f"valid_px={valid.size}/{band.size}"
                )
            else:
                print(f"  band {i} ({ds.descriptions[i - 1]}): ALL MASKED (0 valid px)")

    print("\n=== Sentinel-1 GRD ===")
    s1_result = client.prepare_sentinel1_aoi_raster(
        aoi, date_start, date_end, "/var/lib/dmrv/data/cdse_cache/verify_s1.tif",
    )
    for s in s1_result.scenes:
        print(f"  scene {s.scene_id} | {s.datetime}")
    print(f"  output: {s1_result.output_path}")
    print(f"  size: {s1_result.width}x{s1_result.height} px, {s1_result.resolution_m} m/px")
    print(f"  crs: {s1_result.crs}")
    print(f"  bounds: {s1_result.bounds}")
    with rasterio.open(s1_result.output_path) as ds:
        for i in range(1, ds.count + 1):
            band = ds.read(i)
            valid = band[np.isfinite(band) & (band != 0)]
            if valid.size:
                print(
                    f"  band {i} ({ds.descriptions[i - 1]}): "
                    f"min={valid.min():.4f} max={valid.max():.4f} mean={valid.mean():.4f} "
                    f"valid_px={valid.size}/{band.size}"
                )
            else:
                print(f"  band {i} ({ds.descriptions[i - 1]}): ALL MASKED (0 valid px)")

    print("\nOK - real end-to-end run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
