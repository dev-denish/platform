"""API-contract tests. These drive the REAL FastAPI app (routing, the auth
dependency, the shared require_role RBAC, response models, the global exception
handlers, pagination) with the service layer faked - so they exercise the HTTP
contract end-to-end WITHOUT a database."""
from __future__ import annotations

from uuid import UUID

from prometheus_client.parser import text_string_to_metric_families

from app.api import deps
from app.core.security import create_tile_token
from tests.conftest import _LAYER_ID, FakeTileService

AUTH = {"Authorization": "Bearer admin-token"}
VIEWER = {"Authorization": "Bearer viewer-token"}


def test_health_endpoints(client):
    assert client.get("/livez").status_code == 200
    body = client.get("/livez").json()
    assert body["status"] == "ok"


def test_metrics_is_valid_prometheus_text(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # Round-trips through prometheus_client's own parser -> syntactically valid
    # (raises on malformed text). The parser groups samples under a family name
    # with the _total/_created suffixes stripped, so check the raw text for the
    # exact series names instead.
    list(text_string_to_metric_families(r.text))
    assert "jobs_submitted_total" in r.text
    assert "jobs_completed_total" in r.text
    assert "job_duration_seconds" in r.text


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_current_user(client):
    r = client.get("/api/v1/auth/me", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert r.json()["role"] == "Administrator"


def test_invalid_token_is_401_with_clean_error(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    # no internal detail leaked
    assert "Traceback" not in r.text


def test_projects_list_is_paginated(client):
    r = client.get("/api/v1/projects?limit=25&offset=0", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["limit"] == 25
    assert body["items"][0]["name"] == "Karnataka Restoration"


def test_projects_list_rejects_bad_pagination(client):
    # limit above the allowed ceiling -> 422 from query validation
    assert client.get("/api/v1/projects?limit=9999", headers=AUTH).status_code == 422


def test_project_detail_404_maps_cleanly(client):
    r = client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_kpis_and_layers_and_summary(client):
    pid = "33333333-3333-3333-3333-333333333333"
    assert client.get(f"/api/v1/projects/{pid}/kpis", headers=AUTH).status_code == 200
    layers = client.get(f"/api/v1/projects/{pid}/layers", headers=AUTH).json()
    assert layers["layers"][0]["crs"] == "EPSG:4326"
    summary = client.get("/api/v1/summary", headers=AUTH).json()
    assert summary["project_count"] == 1


def test_upload_forbidden_for_viewer_role(client):
    # RBAC: Viewer cannot upload -> 403 via the shared require_role dependency
    files = {"file": ("x.tif", b"fake", "image/tiff")}
    data = {
        "project_name": "P", "dataset_type": "LULC", "source": "S",
        "accuracy_score": "90", "date_processed": "2026-01-01",
    }
    r = client.post("/api/v1/datasets/upload", headers=VIEWER, files=files, data=data)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_upload_rejects_bad_extension(client):
    files = {"file": ("x.exe", b"fake", "application/octet-stream")}
    data = {
        "project_name": "P", "dataset_type": "LULC", "source": "S",
        "accuracy_score": "90", "date_processed": "2026-01-01",
    }
    r = client.post("/api/v1/datasets/upload", headers=AUTH, files=files, data=data)
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("validation_error", "unprocessable")


def test_delete_project_forbidden_for_non_administrator(client):
    # RBAC: only Administrator may delete -> 403 via the shared require_role
    # dependency, same pattern as upload's role gate.
    r = client.delete(
        "/api/v1/projects/33333333-3333-3333-3333-333333333333", headers=VIEWER
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_delete_project_missing_returns_404(client):
    r = client.delete(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000", headers=AUTH
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_delete_project_then_second_delete_is_404_and_it_vanishes_from_listings(client):
    pid = "33333333-3333-3333-3333-333333333333"

    r1 = client.delete(f"/api/v1/projects/{pid}", headers=AUTH)
    assert r1.status_code == 204
    assert r1.content == b""  # no body on 204

    # Same delete again -> indistinguishable from "never existed".
    r2 = client.delete(f"/api/v1/projects/{pid}", headers=AUTH)
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "not_found"

    # Direct GET on the now-deleted project -> 404, not "410 gone" or similar -
    # no leak that it ever existed.
    assert client.get(f"/api/v1/projects/{pid}", headers=AUTH).status_code == 404

    # Vanishes from the project list and the portfolio summary.
    listing = client.get("/api/v1/projects", headers=AUTH).json()
    assert listing["total"] == 0
    assert listing["items"] == []
    summary = client.get("/api/v1/summary", headers=AUTH).json()
    assert summary["project_count"] == 0


def test_upload_accepts_valid_request_as_admin(client):
    """202 + job_id/status_url contract (Phase 2). The fake TaskRunner completes
    the job synchronously (no real Redis/worker in this tier - see conftest.py),
    so the very first poll already shows `succeeded`; a real deployment would poll
    until the status leaves queued/running."""
    files = {"file": ("scene.tif", b"fakebytes", "image/tiff")}
    data = {
        "project_name": "Karnataka Restoration", "dataset_type": "LULC",
        "source": "Sentinel-2", "accuracy_score": "88.5", "date_processed": "2026-01-01",
    }
    r = client.post("/api/v1/datasets/upload", headers=AUTH, files=files, data=data)
    assert r.status_code == 202
    body = r.json()
    job_id = body["job_id"]
    assert body["status_url"] == f"/api/v1/jobs/{job_id}"

    poll = client.get(f"/api/v1/jobs/{job_id}", headers=AUTH)
    assert poll.status_code == 200
    poll_body = poll.json()
    assert poll_body["status"] == "succeeded"
    assert poll_body["result"]["total_area_ha"] == 2250.0


def test_upload_accepts_missing_accuracy_score_without_a_legend(client):
    """accuracy_score is a classification-accuracy metric - optional when no
    class_legend is supplied, since there's no classification to be accurate
    about (e.g. a raw, unclassified scene)."""
    files = {"file": ("scene.tif", b"fakebytes", "image/tiff")}
    data = {
        "project_name": "Raw Scene", "dataset_type": "LULC",
        "source": "Sentinel-2", "date_processed": "2026-01-01",
    }
    r = client.post("/api/v1/datasets/upload", headers=AUTH, files=files, data=data)
    assert r.status_code == 202


def test_upload_requires_accuracy_score_when_legend_supplied(client):
    """The inverse rule: a class_legend implies a real classification, so
    accuracy_score becomes required again - the frontend must mirror this
    exactly rather than invent its own rule that could drift from it."""
    files = {"file": ("scene.tif", b"fakebytes", "image/tiff")}
    data = {
        "project_name": "Classified Scene", "dataset_type": "LULC",
        "source": "Sentinel-2", "date_processed": "2026-01-01",
        "class_legend": '{"1": "Forest", "2": "Water"}',
    }
    r = client.post("/api/v1/datasets/upload", headers=AUTH, files=files, data=data)
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("validation_error", "unprocessable")


def test_upload_accepts_satellite_type_without_a_legend(client):
    """'Satellite / Raw Imagery' is a UI-only convenience label over the same
    legend-driven ingestion path as LULC - no legend required, same as any
    other type."""
    files = {"file": ("scene.tif", b"fakebytes", "image/tiff")}
    data = {
        "project_name": "Raw Satellite Scene", "dataset_type": "Satellite / Raw Imagery",
        "source": "Sentinel-2", "date_processed": "2026-01-01",
    }
    r = client.post("/api/v1/datasets/upload", headers=AUTH, files=files, data=data)
    assert r.status_code == 202


def test_upload_requires_accuracy_score_when_legend_supplied_for_satellite_type(client):
    """Same accuracy_score/class_legend rule applies regardless of dataset_type -
    there's no separate validation path for this type."""
    files = {"file": ("scene.tif", b"fakebytes", "image/tiff")}
    data = {
        "project_name": "Classified Satellite Scene", "dataset_type": "Satellite / Raw Imagery",
        "source": "Sentinel-2", "date_processed": "2026-01-01",
        "class_legend": '{"1": "Forest", "2": "Water"}',
    }
    r = client.post("/api/v1/datasets/upload", headers=AUTH, files=files, data=data)
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("validation_error", "unprocessable")


def test_job_not_found_or_not_owned_is_a_uniform_404(client):
    r = client.get(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=AUTH
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def _tile_url(layer_id, z, x, y, token=None, ext="png"):
    url = f"/api/v1/tiles/{layer_id}/{z}/{x}/{y}.{ext}"
    return f"{url}?token={token}" if token else url


def test_tile_authorized_request_returns_real_png(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    token = create_tile_token(test_settings, layer_id=str(_LAYER_ID))

    r = client.get(_tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y, token))

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"  # real render, not a stub
    assert r.headers["cache-control"] == "public, max-age=86400, immutable"
    assert "etag" in r.headers


def test_tile_conditional_request_returns_304_without_rerendering(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    token = create_tile_token(test_settings, layer_id=str(_LAYER_ID))
    url = _tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y, token)

    first = client.get(url)
    etag = first.headers["etag"]
    second = client.get(url, headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""


def test_tile_missing_token_is_422(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    r = client.get(_tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y))
    assert r.status_code == 422


def test_tile_garbage_token_is_401(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    r = client.get(_tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y, "not-a-real-token"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_tile_expired_token_is_401(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    expired_settings = test_settings.model_copy(update={"tile_token_ttl_seconds": -1})
    token = create_tile_token(expired_settings, layer_id=str(_LAYER_ID))

    r = client.get(_tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y, token))
    assert r.status_code == 401


def test_tile_token_for_a_different_layer_is_401(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    other_layer = UUID("99999999-9999-9999-9999-999999999999")
    token = create_tile_token(test_settings, layer_id=str(other_layer))  # wrong layer

    r = client.get(_tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y, token))
    assert r.status_code == 401


def test_tile_for_layer_with_no_cog_is_404(client, test_settings, real_cog):
    unregistered_layer = UUID("88888888-8888-8888-8888-888888888888")
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    token = create_tile_token(test_settings, layer_id=str(unregistered_layer))

    r = client.get(_tile_url(unregistered_layer, real_cog.z, real_cog.x, real_cog.y, token))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_tile_unsupported_extension_is_422(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    token = create_tile_token(test_settings, layer_id=str(_LAYER_ID))
    r = client.get(_tile_url(_LAYER_ID, real_cog.z, real_cog.x, real_cog.y, token, ext="jpg"))
    assert r.status_code == 422


def test_tile_outside_raster_bounds_is_404(client, test_settings, real_cog):
    client.app.dependency_overrides[deps.get_tile_service] = lambda: FakeTileService(
        test_settings, real_cog.path
    )
    token = create_tile_token(test_settings, layer_id=str(_LAYER_ID))
    z, x, y = real_cog.out_of_bounds

    r = client.get(_tile_url(_LAYER_ID, z, x, y, token))
    assert r.status_code == 404
