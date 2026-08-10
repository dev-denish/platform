"""Aggregates all v1 feature routers under a single APIRouter that main.py mounts at
the versioned prefix. New feature routers (carbon, workflow, reports, ...) are added
here as the platform grows - each is an isolated module, keeping the API surface
navigable."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    adhoc_layers,
    analyses,
    auth,
    datasets,
    external_layers,
    forest_definition,
    jobs,
    layers,
    memberships,
    permissions,
    projects,
    reference_layers,
    reports,
    tiles,
    users,
    wms_domains,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(analyses.router)
api_router.include_router(projects.router)
api_router.include_router(memberships.router)
api_router.include_router(datasets.router)
api_router.include_router(jobs.router)
api_router.include_router(tiles.router)
api_router.include_router(users.router)
api_router.include_router(permissions.router)
api_router.include_router(layers.router)
api_router.include_router(wms_domains.router)
api_router.include_router(external_layers.router)
api_router.include_router(reference_layers.router)
api_router.include_router(adhoc_layers.router)
api_router.include_router(forest_definition.router)
api_router.include_router(reports.router)
