"""Unit tests for vnv_analysis_jobs.py's run_vnv_band_index_analysis -
carbon-mrv-vm0047 report-generation fix. Proves:
  * fix #4 - coverage_pct's denominator is PreparedRaster.aoi_pixel_count
    (the true AOI polygon-footprint pixel count), not index_arr.size (the
    bbox grid) - a non-rectangular-AOI regression test, with a second test
    asserting what the OLD bug's arithmetic would have produced.
  * fixes #1/#2 (upstream half) - std_dev is actually computed, and
    stats["distribution"]["latest"] is populated in the shape
    report_content.py's stat-grid branch and report_deterministic_narrative
    .py's VNV branch both expect.

No DB/CDSE network access - CDSEClient and the 3 repositories
(JobRepository/AnalysisRunRepository/AnalysisResultRepository) are faked;
only the real numpy/rasterio compute path inside the job itself runs."""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.cdse_ingestion import PreparedRaster, SceneMeta
from app.workers import vnv_analysis_jobs as vnv_jobs

_CAPTURED: dict = {}


class _FakeCursor:
    pass


class _FakeDB:
    @contextmanager
    def transaction(self):
        yield _FakeCursor()


class _FakeJobRepository:
    def __init__(self, cur):
        pass

    def mark_running(self, job_id):
        pass

    def mark_succeeded(self, job_id, result):
        pass

    def mark_dead_letter(self, job_id, error):
        raise AssertionError(f"job unexpectedly dead-lettered: {error}")

    def record_retry_error(self, job_id, error):
        raise AssertionError(f"job unexpectedly retried: {error}")


class _FakeAnalysisRunRepository:
    def __init__(self, cur):
        pass

    def mark_running(self, run_id):
        pass

    def set_input_raster_ref(self, run_id, path):
        pass

    def mark_failed(self, run_id, message):
        raise AssertionError(f"analysis run unexpectedly failed: {message}")

    def mark_done(self, run_id, *, output_raster_ref, stats):
        pass


class _FakeAnalysisResultRepository:
    def __init__(self, cur):
        pass

    def upsert(self, *, project_id, analysis_id, computed_by, stats, legend, tile_url_template):
        _CAPTURED["stats"] = stats


def _write_fake_s2_raster(path: str, h: int, w: int, *, outside_aoi_pixel_count: int) -> int:
    """A synthetic 6-band raster the job will `rasterio.open` and read
    directly, standing in for a real CDSE-fetched composite. The LAST
    `outside_aoi_pixel_count` pixels (row-major order) are zeroed across all
    6 bands - the SAME nodata=0 value both cloud-masked AND
    outside-true-polygon pixels get in the real pipeline (see
    cdse_ingestion.py's `_process_s2_scene`/`_merge_and_clip`). Returns the
    real count of non-zeroed (valid, pre-index) pixels."""
    rng = np.random.default_rng(42)
    stacked = rng.integers(2000, 5000, size=(6, h, w)).astype("uint16")
    flat_mask = np.zeros(h * w, dtype=bool)
    if outside_aoi_pixel_count:
        flat_mask[-outside_aoi_pixel_count:] = True
    mask2d = flat_mask.reshape(h, w)
    for b in range(6):
        stacked[b][mask2d] = 0
    transform = from_origin(500000, 1600000, 10, 10)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=6,
        dtype="uint16", crs="EPSG:32644", transform=transform, nodata=0,
    ) as dst:
        dst.write(stacked)
    return int((~mask2d).sum())


def _run_job(monkeypatch, tmp_path, *, h: int, w: int, outside_aoi_pixel_count: int, aoi_pixel_count: int):
    monkeypatch.setattr(vnv_jobs, "JobRepository", _FakeJobRepository)
    monkeypatch.setattr(vnv_jobs, "AnalysisRunRepository", _FakeAnalysisRunRepository)
    monkeypatch.setattr(vnv_jobs, "AnalysisResultRepository", _FakeAnalysisResultRepository)

    settings = SimpleNamespace(local_data_dir=str(tmp_path), job_max_retries=3)
    analysis_run_id = "run-1"
    input_path = f"{settings.local_data_dir}/vnv/{analysis_run_id}/s2_aoi.tif"
    valid_count = _write_fake_s2_raster(
        input_path, h, w, outside_aoi_pixel_count=outside_aoi_pixel_count
    )

    class _FakeCDSEClient:
        def __init__(self, settings):
            pass

        def prepare_sentinel2_aoi_raster(self, boundary_geojson, date_start, date_end, output_path):
            assert output_path == input_path  # the real path the job already wrote above
            return PreparedRaster(
                output_path=output_path,
                scenes=[SceneMeta(
                    scene_id="s1", collection="sentinel-2-l2a",
                    datetime="2026-01-01T00:00:00Z", cloud_cover=5.0,
                )],
                width=w, height=h, crs="EPSG:32644", resolution_m=10.0,
                bounds=(500000.0, 1600000.0 - h * 10, 500000.0 + w * 10, 1600000.0),
                aoi_pixel_count=aoi_pixel_count,
            )

    monkeypatch.setattr(vnv_jobs, "CDSEClient", _FakeCDSEClient)

    ctx = {"db": _FakeDB(), "settings": settings, "job_try": 1}
    asyncio.run(vnv_jobs.run_vnv_band_index_analysis(
        ctx, job_id="job-1", analysis_run_id=analysis_run_id, project_id="proj-1",
        analysis_id="vnv_ndvi", boundary_geojson={"type": "Polygon", "coordinates": []},
        actor={"user_id": "user-1"},
    ))
    return _CAPTURED["stats"], valid_count


def test_coverage_pct_denominator_is_aoi_pixel_count_not_bbox_size(monkeypatch, tmp_path):
    """320 real, cloud-clear pixels on a 400-pixel (20x20) bbox grid,
    `aoi_pixel_count=320` (simulating a non-rectangular AOI whose true
    footprint is smaller than its bbox). The fix uses `aoi_pixel_count`
    (320) as the denominator, not `h * w` (400)."""
    h = w = 20
    stats, valid_count = _run_job(
        monkeypatch, tmp_path, h=h, w=w, outside_aoi_pixel_count=80, aoi_pixel_count=320,
    )
    assert valid_count == 320
    assert stats["valid_pixel_count"] == 320
    assert stats["total_pixel_count"] == 320
    assert stats["total_pixel_count"] != h * w
    assert stats["coverage_pct"] == pytest.approx(100.0)


def test_coverage_pct_was_previously_deflated_by_the_bbox_denominator(monkeypatch, tmp_path):
    """Regression guard phrased as the OLD bug's own arithmetic: had the job
    still divided by `index_arr.size` (400, the bbox grid) instead of
    `aoi_pixel_count` (320, the true AOI footprint), coverage_pct here would
    read 80.0%, not the true 100.0%."""
    h = w = 20
    stats, valid_count = _run_job(
        monkeypatch, tmp_path, h=h, w=w, outside_aoi_pixel_count=80, aoi_pixel_count=320,
    )
    old_buggy_coverage_pct = 100.0 * valid_count / (h * w)
    assert old_buggy_coverage_pct == pytest.approx(80.0)
    assert stats["coverage_pct"] != pytest.approx(old_buggy_coverage_pct)


def test_coverage_pct_is_a_near_no_op_for_a_genuinely_rectangular_aoi(monkeypatch, tmp_path):
    """No pixels outside the AOI at all (aoi_pixel_count == the full grid) -
    the fix must not change behaviour for a rectangular AOI."""
    h = w = 15
    stats, valid_count = _run_job(
        monkeypatch, tmp_path, h=h, w=w, outside_aoi_pixel_count=0, aoi_pixel_count=h * w,
    )
    assert valid_count == h * w
    assert stats["total_pixel_count"] == h * w
    assert stats["coverage_pct"] == pytest.approx(100.0)


def test_std_dev_and_distribution_latest_are_populated(monkeypatch, tmp_path):
    """Fixes #1/#2's upstream half: std_dev was not computed at all before
    this fix, and stats["distribution"] did not exist - both are needed for
    report_content.py's stat-grid branch and report_deterministic_narrative
    .py's VNV branch to work."""
    h = w = 10
    stats, _valid_count = _run_job(
        monkeypatch, tmp_path, h=h, w=w, outside_aoi_pixel_count=0, aoi_pixel_count=h * w,
    )
    assert stats["std_dev"] is not None
    assert stats["std_dev"] > 0.0
    assert stats["distribution"] == {
        "latest": {
            "mean": stats["mean"], "std_dev": stats["std_dev"],
            "min": stats["min"], "max": stats["max"],
        },
    }
