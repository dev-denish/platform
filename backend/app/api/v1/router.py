"""Aggregates all v1 feature routers under a single APIRouter that main.py mounts at
the versioned prefix. New feature routers (carbon, workflow, reports, ...) are added
here as the platform grows - each is an isolated module, keeping the API surface
navigable."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, datasets, jobs, projects, tiles

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(datasets.router)
api_router.include_router(jobs.router)
api_router.include_router(tiles.router)
