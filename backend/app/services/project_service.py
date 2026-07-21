"""Project + analytics read service. Assembles typed DTOs from the repositories and
translates 'absent' into domain NotFoundError. Preserves every read the MVP exposed
(projects, project detail, KPIs, layers, portfolio summary) with identical meaning,
now paginated and typed."""
from __future__ import annotations

from uuid import UUID

import rasterio

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import NotFoundError
from app.core.security import create_tile_token
from app.domain.dtos import (
    CurrentUser,
    DatasetOut,
    EvolutionChange,
    EvolutionClassRow,
    KpiValue,
    LayerOut,
    Page,
    ProjectDetail,
    ProjectEvolution,
    ProjectKpis,
    ProjectLayers,
    ProjectSummary,
)
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, KpiRepository, LayerRepository
from app.repositories.projects import ProjectRepository
from app.services.ingestion import raster as R
from app.services.ingestion.storage import Storage


def _evolution_change(from_date: str, to_date: str, area_by_date: dict[str, float | None]) -> EvolutionChange:
    """One date-pair's change for one class row. Division only ever happens
    when `start` is confirmed non-zero - see EvolutionChange's own docstring
    for why "new" (not Infinity) is the divide-by-zero convention, and why a
    shrink TO zero needs no special case at all."""
    start, end = area_by_date[from_date], area_by_date[to_date]
    if start is None or end is None:
        return EvolutionChange(from_date=from_date, to_date=to_date, net_change_ha=None, pct_change=None)
    net = round(end - start, 4)
    if start == 0:
        pct: float | str = "new" if end > 0 else 0.0
    else:
        pct = round((end - start) / start * 100, 2)
    return EvolutionChange(from_date=from_date, to_date=to_date, net_change_ha=net, pct_change=pct)


def compute_evolution(
    project_id: UUID, layer_rows: list[dict], kpi_rows: list[dict]
) -> ProjectEvolution:
    """The actual Landscape Evolution computation, given the exact rows
    LayerRepository.list_for_project / KpiRepository.for_project already
    return - a pure function (no DB access) so every eligibility/None-vs-
    zero/divide-by-zero edge case is unit-testable directly, not only
    through a live-database integration test."""
    # Eligible = dated AND classified (a legend). Land-class evolution
    # inherently needs classification; a project mixing raw/unclassified
    # imagery in with LULC across dates only compares the classified ones -
    # a raw-imagery date is excluded here, not errored on.
    eligible_by_date: dict[str, dict] = {}
    for r in layer_rows:
        if not r["date_processed"] or not r["class_legend"]:
            continue
        date = str(r["date_processed"])
        # ponytail: real data can have 2+ layers share a date (seen in dev
        # DB) - first one wins, same "pick one representative" convention
        # lib/timeline.js's datedLayerGroups uses on the frontend.
        # list_for_project's own ORDER BY (loaded_at DESC) makes this
        # deterministic, not arbitrary.
        eligible_by_date.setdefault(date, r)

    dates = sorted(eligible_by_date)
    if len(dates) < 2:
        return ProjectEvolution(project_id=project_id, applicable=False, dates=dates, classes=[])

    kpi_by_layer: dict[str, dict[str, float]] = {}
    for r in kpi_rows:
        if not r["metric_name"].startswith("class_area_"):
            continue  # total_area etc aren't a land class row
        kpi_by_layer.setdefault(str(r["layer_id"]), {})[r["metric_name"]] = float(r["value"])

    # The row universe is every class ANY eligible date's legend defines -
    # not just whatever KPI rows happen to exist (a legend-defined class
    # with zero matching pixels writes no KPI row at all - see
    # raster.legend_class_labels).
    legend_metrics_by_date: dict[str, set[str]] = {
        date: {R.metric_key(label) for label in R.legend_class_labels(r["class_legend"])}
        for date, r in eligible_by_date.items()
    }
    all_metric_names: set[str] = set().union(*legend_metrics_by_date.values())

    classes = []
    for metric_name in sorted(all_metric_names):
        area_by_date: dict[str, float | None] = {}
        for date in dates:
            if metric_name not in legend_metrics_by_date[date]:
                area_by_date[date] = None
            else:
                layer_id = str(eligible_by_date[date]["layer_id"])
                area_by_date[date] = kpi_by_layer.get(layer_id, {}).get(metric_name, 0.0)

        classes.append(
            EvolutionClassRow(
                metric_name=metric_name,
                area_by_date_ha=area_by_date,
                first_vs_last=_evolution_change(dates[0], dates[-1], area_by_date),
                consecutive=[
                    _evolution_change(dates[i], dates[i + 1], area_by_date)
                    for i in range(len(dates) - 1)
                ],
            )
        )

    return ProjectEvolution(project_id=project_id, applicable=True, dates=dates, classes=classes)


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
        # Phase 3 Wave G: grouped by layer_id, not flattened project-wide -
        # see KpiRepository.for_project's docstring for the bug this fixes.
        layers: dict[str, dict[str, KpiValue]] = {}
        for r in rows:
            layer_metrics = layers.setdefault(str(r["layer_id"]), {})
            layer_metrics[r["metric_name"]] = KpiValue(value=float(r["value"]), unit=r["unit"])
        return ProjectKpis(project_id=project_id, layers=layers)

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
                tile_url_template=self._tile_url_template(r["layer_id"], r["cog_key"]),
                band_count=self._band_count(r["band_count"], r["cog_key"]),
                class_legend=r["class_legend"],
            )
            for r in rows
        ]
        return ProjectLayers(project_id=project_id, layers=layers)

    def get_evolution(self, project_id: UUID) -> ProjectEvolution:
        """Phase 3 Wave G: land-class change across real classified dated
        layers. Reuses KpiRepository.for_project and LayerRepository.
        list_for_project AS-IS - no new/parallel query against the kpi
        table, so whatever this project already gets from GET
        /projects/{id}/kpis is exactly what this reads too. The actual
        computation is a pure function (`compute_evolution` below) so it's
        unit-testable without a live database."""
        with self.db.connection() as conn, conn.cursor() as cur:
            if not ProjectRepository(cur).get(project_id):
                raise NotFoundError("Project not found.")
            layer_rows = LayerRepository(cur).list_for_project(project_id)
            kpi_rows = KpiRepository(cur).for_project(project_id)
        return compute_evolution(project_id, layer_rows, kpi_rows)

    def _band_count(self, stored: int | None, cog_key: str | None) -> int | None:
        """Self-healing fallback for layers ingested before `band_count` was a
        column: a cheap header-only open of the already-converted COG, instead
        of a backfill migration. None if there's genuinely no COG to read."""
        if stored is not None:
            return stored
        if not cog_key:
            return None
        with rasterio.open(self.storage.local_path_for_processing(cog_key)) as d:
            return d.count

    def _tile_url_template(self, layer_id: UUID, cog_key: str | None) -> str | None:
        """None when there's no COG to tile from yet (conversion pending/failed -
        see workers/jobs.py); otherwise a signed, short-lived per-layer token
        (app/core/security.create_tile_token) embedded in a {z}/{x}/{y} template a
        map library's tileLayer() consumes directly."""
        if not cog_key:
            return None
        token = create_tile_token(self.settings, layer_id=str(layer_id))
        return f"{self.settings.api_v1_prefix}/tiles/{layer_id}/{{z}}/{{x}}/{{y}}.png?token={token}"

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
