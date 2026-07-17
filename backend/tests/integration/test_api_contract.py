"""API-contract tests. These drive the REAL FastAPI app (routing, the auth
dependency, the shared require_role RBAC, response models, the global exception
handlers, pagination) with the service layer faked - so they exercise the HTTP
contract end-to-end WITHOUT a database."""
from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families

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


def test_job_not_found_or_not_owned_is_a_uniform_404(client):
    r = client.get(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=AUTH
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
