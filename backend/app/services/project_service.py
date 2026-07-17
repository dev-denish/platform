"""Project + analytics read service. Assembles typed DTOs from the repositories and
translates 'absent' into domain NotFoundError. Preserves every read the MVP exposed
(projects, project detail, KPIs, layers, portfolio summary) with identical meaning,
now paginated and typed."""
from __future__ import annotations

from uuid import UUID

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import NotFoundError
from app.domain.dtos import (
    CurrentUser,
    DatasetOut,
    KpiValue,
    LayerOut,
    Page,
    ProjectDetail,
    ProjectKpis,
    ProjectLayers,
    ProjectSummary,
)
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, KpiRepository, LayerRepository
from app.repositories.projects import ProjectRepository
from app.services.ingestion.storage import Storage


class ProjectService:
    def __init__(self, db: Database, settings: Settings, storage: Storage) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage

    def list_projects(self, limit: int, offset: int) -> Page[ProjectSummary]:
        with self.db.connection() as conn, conn.cursor() as cur:
            rows, total = ProjectRepository(cur).list_paginated(limit, offset)
        return Page[ProjectSummary](
            items=[ProjectSummary(**r) for r in rows], total=total, limit=limit, offset=offset
        )

    def get_project(self, project_id: UUID) -> ProjectDetail:
        with self.db.connection() as conn, conn.cursor() as cur:
            proj = ProjectRepository(cur).get(project_id)
            if not proj:
                raise NotFoundError("Project not found.")
            datasets = DatasetRepository(cur).list_for_project(project_id)
        return ProjectDetail(
            **proj, datasets=[DatasetOut(**d) for d in datasets]
        )

    def get_kpis(self, project_id: UUID) -> ProjectKpis:
        with self.db.connection() as conn, conn.cursor() as cur:
            if not ProjectRepository(cur).get(project_id):
                raise NotFoundError("Project not found.")
            rows = KpiRepository(cur).for_project(project_id)
        return ProjectKpis(
            project_id=project_id,
            kpis={
                r["metric_name"]: KpiValue(value=float(r["value"]), unit=r["unit"])
                for r in rows
            },
        )

    def get_layers(self, project_id: UUID) -> ProjectLayers:
        with self.db.connection() as conn, conn.cursor() as cur:
            if not ProjectRepository(cur).get(project_id):
                raise NotFoundError("Project not found.")
            rows = LayerRepository(cur).list_for_project(project_id)
        layers = [
            LayerOut(
                layer_id=r["layer_id"], type=r["type"], crs=r["crs"],
                bounds=[[r["bbox_miny"], r["bbox_minx"]], [r["bbox_maxy"], r["bbox_maxx"]]],
                pixel_size_m=float(r["pixel_size_m"]),
                preview_url=self.storage.url_for(r["preview_key"]),
                date_processed=str(r["date_processed"]) if r["date_processed"] else None,
            )
            for r in rows
        ]
        return ProjectLayers(project_id=project_id, layers=layers)

    def portfolio_summary(self) -> dict:
        with self.db.connection() as conn, conn.cursor() as cur:
            totals, count = KpiRepository(cur).portfolio_totals()
        return {"portfolio": totals, "project_count": count}

    def delete_project(self, project_id: UUID, actor: CurrentUser) -> None:
        """Soft delete only - see app/repositories/projects.py. Datasets/layers/kpis
        keep their existing FK to this row; nothing physical is removed. One
        transaction: read the version to delete against, attempt the guarded
        UPDATE, and write the audit entry only if it actually took effect."""
        with self.db.transaction() as cur:
            repo = ProjectRepository(cur)
            version = repo.get_version(project_id)
            if version is None:
                raise NotFoundError("Project not found.")
            deleted = repo.soft_delete(
                project_id, expected_version=version, deleted_by=actor.user_id
            )
            if not deleted:
                # Lost a race with a concurrent delete (or someone deleted it
                # between the read above and now) - same 404 a caller would get
                # for a project that never existed.
                raise NotFoundError("Project not found.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.DELETE_PROJECT, target=str(project_id),
                detail=f"Soft-deleted project {project_id}.",
            )
