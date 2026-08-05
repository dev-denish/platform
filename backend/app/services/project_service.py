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
from app.domain.authz import require_project_view
from app.domain.dtos import (
    ActivityFeed,
    ActivityItem,
    BulkDeleteItemResult,
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
from app.domain.enums import AuditAction, Role
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, KpiRepository, LayerRepository
from app.repositories.projects import ProjectRepository
from app.repositories.wms_domains import ExternalLayerRepository
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

    def list_projects(
        self, user: CurrentUser, limit: int, offset: int, search: str | None = None
    ) -> Page[ProjectSummary]:
        # Administrator: every project, no filter. Everyone else: only
        # projects they have a live membership row on (Wave: project-level
        # RBAC) - never all of them, and never decided client-side.
        member_id = None if user.role == Role.ADMINISTRATOR else user.user_id
        with self.db.connection() as conn, conn.cursor() as cur:
            rows, total = ProjectRepository(cur).list_paginated(
                limit, offset, member_id=member_id, search=search
            )
        return Page[ProjectSummary](
            items=[ProjectSummary(**r) for r in rows], total=total, limit=limit, offset=offset
        )

    def get_project(self, project_id: UUID, user: CurrentUser) -> ProjectDetail:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            proj = ProjectRepository(cur).get(project_id)
            datasets = DatasetRepository(cur).list_for_project(project_id)
        return ProjectDetail(
            **proj, datasets=[DatasetOut(**d) for d in datasets]
        )

    def get_kpis(self, project_id: UUID, user: CurrentUser) -> ProjectKpis:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            rows = KpiRepository(cur).for_project(project_id)
        # Phase 3 Wave G: grouped by layer_id, not flattened project-wide -
        # see KpiRepository.for_project's docstring for the bug this fixes.
        layers: dict[str, dict[str, KpiValue]] = {}
        for r in rows:
            layer_metrics = layers.setdefault(str(r["layer_id"]), {})
            layer_metrics[r["metric_name"]] = KpiValue(value=float(r["value"]), unit=r["unit"])
        return ProjectKpis(project_id=project_id, layers=layers)

    def get_layers(self, project_id: UUID, user: CurrentUser) -> ProjectLayers:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            rows = LayerRepository(cur).list_for_project(project_id)
            # Wave: multi-format layers. One extra row lookup per external
            # layer (never more than a handful per project) to get the
            # service_kind the URL-template needs - not worth a JOIN in
            # list_for_project's main query, which every OTHER layer_kind
            # doesn't need at all.
            external_by_layer: dict[str, dict] = {}
            ext_repo = ExternalLayerRepository(cur)
            for r in rows:
                if r["layer_kind"] in ("external_wms", "external_wfs"):
                    source = ext_repo.get(r["layer_id"])
                    if source is not None:
                        external_by_layer[str(r["layer_id"])] = source
        layers = [
            LayerOut(
                layer_id=r["layer_id"], type=r["type"], layer_kind=r["layer_kind"], crs=r["crs"],
                bounds=[[r["bbox_miny"], r["bbox_minx"]], [r["bbox_maxy"], r["bbox_maxx"]]],
                pixel_size_m=float(r["pixel_size_m"]) if r["pixel_size_m"] is not None else None,
                preview_url=self.storage.url_for(r["preview_key"]) if r["preview_key"] else None,
                date_processed=str(r["date_processed"]) if r["date_processed"] else None,
                tile_url_template=self._tile_url_template_for(r, external_by_layer),
                features_url=self._features_url(r["layer_id"], r["layer_kind"]),
                band_count=self._band_count(r["band_count"], r["cog_key"]),
                class_legend=r["class_legend"],
                source=r["source"],
                display_name=r["display_name"],
                accuracy_score=(
                    float(r["accuracy_score"]) if r["accuracy_score"] is not None else None
                ),
                is_reference=r["is_reference"],
                is_adhoc=r["is_adhoc"],
                needs_reingestion=self._needs_reingestion(r["layer_kind"], r["cog_key"]),
            )
            for r in rows
        ]
        return ProjectLayers(project_id=project_id, layers=layers)

    def get_evolution(self, project_id: UUID, user: CurrentUser) -> ProjectEvolution:
        """Phase 3 Wave G: land-class change across real classified dated
        layers. Reuses KpiRepository.for_project and LayerRepository.
        list_for_project AS-IS - no new/parallel query against the kpi
        table, so whatever this project already gets from GET
        /projects/{id}/kpis is exactly what this reads too. The actual
        computation is a pure function (`compute_evolution` below) so it's
        unit-testable without a live database."""
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            layer_rows = LayerRepository(cur).list_for_project(project_id)
            kpi_rows = KpiRepository(cur).for_project(project_id)
        # Wave: Reference Layer Library - list_for_project now also returns
        # every OTHER project's reference layers (that's the whole point, for
        # the Layers panel). Evolution is inherently a THIS-project temporal
        # comparison, though - a classified, dated reference layer must never
        # sneak an unrelated date into it, so those rows are excluded here,
        # not inside compute_evolution itself (which stays a pure function
        # over whatever rows it's handed, unit-tested separately). Wave 3
        # (Added Layers) - an ad-hoc layer is excluded the same way (its
        # kpi_rows are already filtered out at the source, see
        # KpiRepository.for_project, but its layer_row must also never seed
        # an eligible `date` on its own).
        own_layer_rows = [
            r for r in layer_rows if not r["is_reference"] and not r["is_adhoc"]
        ]
        return compute_evolution(project_id, own_layer_rows, kpi_rows)

    # Which repository (if any) can turn this action's `target` into prose,
    # and which id space `target` is actually in for that action - RENAME_LAYER/
    # UPDATE_CLASS_LEGEND/CREATE_EXTERNAL_LAYER record a spatial_layer.layer_id;
    # INGEST_DATASET/DELETE_DATASET record a dataset.dataset_id instead (see
    # each service's own AuditRepository.record call). Membership actions
    # (ADD/REMOVE_PROJECT_MEMBER, UPDATE_PROJECT_MEMBER_ROLE) and every
    # genuinely-global action are deliberately absent - their `detail` is
    # already plain English (a username, never a raw id), nothing to resolve.
    _LAYER_TARGET_ACTIONS = frozenset(
        {AuditAction.RENAME_LAYER, AuditAction.UPDATE_CLASS_LEGEND, AuditAction.CREATE_EXTERNAL_LAYER}
    )
    _DATASET_TARGET_ACTIONS = frozenset({AuditAction.INGEST_DATASET, AuditAction.DELETE_DATASET})

    def get_activity(self, project_id: UUID, limit: int, user: CurrentUser) -> ActivityFeed:
        """Recent-activity feed for this project's dashboard: audit_log rows
        already tagged with THIS project_id at write time (migration 0014) -
        see AuditRepository.list_for_project's own docstring for why that's
        read-time-join-free and for which actions never carry one (WMS
        domain allow-list, user management, login - genuinely global, not
        "this project's" activity).

        `target_label` is resolved HERE, at read time, rather than snapshotted
        at write time - a rename's own audit entry should keep reflecting
        whatever the layer is called NOW (if it's renamed again later), same
        as this feed already does for every other "what is this called"
        question in the app. A row this can't resolve (unknown action, or the
        referenced row is genuinely gone) just gets target_label=None - the
        frontend already falls back to `detail` alone in that case."""
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            rows = AuditRepository(cur).list_for_project(project_id, limit)
            layer_repo, dataset_repo = LayerRepository(cur), DatasetRepository(cur)
            # DELETE_PROJECT's target IS this project - one lookup covers
            # every such row in the page, not one per row.
            project_name: str | None = None
            project_name_looked_up = False
            items = []
            for r in rows:
                action, target = r["action"], r["target"]
                label = None
                # `target`'s shape is convention, not a schema - membership
                # actions record a "project_id:user_id" composite, and
                # nothing stops a future action from writing something else
                # non-UUID for a type in these two sets. A malformed value
                # hitting spatial_layer/dataset's UUID columns would raise
                # at the DB rather than just "not found" - validate first so
                # one odd row degrades to target_label=None, not a 500 for
                # every row after it.
                if target and (action in self._LAYER_TARGET_ACTIONS or action in self._DATASET_TARGET_ACTIONS):
                    try:
                        UUID(target)
                    except ValueError:
                        target = None
                if target:
                    if action in self._LAYER_TARGET_ACTIONS:
                        label = layer_repo.get_label(target)
                    elif action in self._DATASET_TARGET_ACTIONS:
                        label = dataset_repo.get_label(target)
                    elif action == AuditAction.DELETE_PROJECT:
                        if not project_name_looked_up:
                            proj = ProjectRepository(cur).get(project_id)
                            project_name = proj["name"] if proj else None
                            project_name_looked_up = True
                        label = project_name
                items.append(ActivityItem(**r, target_label=label))
        return ActivityFeed(items=items)

    def _needs_reingestion(self, layer_kind: str, cog_key: str | None) -> bool:
        """True when this raster layer's stored COG predates the geometric
        padding-mask fix (raster.reproject_to_4326's `add_alpha`/`write_mask`)
        - a real per-pixel warp mask never got baked into the file, so
        rendered padding can't be told apart from real data. The only fix is
        re-ingesting from the original source (see raster.has_real_mask's
        docstring); this just makes which layers need it visible instead of
        silently serving a stale render. False (not an error) whenever
        there's no COG yet to check."""
        if layer_kind != "raster" or not cog_key:
            return False
        return not R.has_real_mask(self.storage.local_path_for_processing(cog_key))

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

    def _tile_url_template_for(self, row: dict, external_by_layer: dict[str, dict]) -> str | None:
        """Wave: multi-format layers. Raster keeps its existing COG-tile
        template. An external_wms layer instead gets this backend's own
        SSRF-guarded proxy base URL (app/api/v1/external_layers.py), never
        the third-party server's real URL - the frontend passes this
        straight to react-leaflet's <WMSTileLayer url=...>, which appends
        the standard WMS bbox/width/height/srs query params itself; the
        proxy reads those off the request and forwards them, ignoring any
        client-supplied `layers` param in favor of what was stored at
        creation (see ExternalLayerRepository). external_wfs has no tile
        grid (fetched as one GeoJSON response instead - see
        _features_url) so it returns None here."""
        if row["layer_kind"] == "raster":
            return self._tile_url_template(row["layer_id"], row["cog_key"])
        if row["layer_kind"] == "external_wms":
            if str(row["layer_id"]) not in external_by_layer:
                return None
            token = create_tile_token(self.settings, layer_id=str(row["layer_id"]))
            layer_id = row["layer_id"]
            return f"{self.settings.api_v1_prefix}/external-layers/{layer_id}/wms?token={token}"
        return None

    def _features_url(self, layer_id: UUID, layer_kind: str) -> str | None:
        """Wave: multi-format layers. A vector layer's real geometries are
        fetched once via this authenticated endpoint (GET /layers/{id}/geojson,
        plain Bearer auth - no signed token needed, this is a normal fetch()
        call, not an <img>/tileLayer request). An external_wfs layer reuses
        the identical shape, backed by the SSRF-guarded proxy instead."""
        if layer_kind == "vector":
            return f"{self.settings.api_v1_prefix}/layers/{layer_id}/geojson"
        if layer_kind == "external_wfs":
            return f"{self.settings.api_v1_prefix}/external-layers/{layer_id}/wfs"
        return None

    def portfolio_summary(self, user: CurrentUser) -> dict:
        # Wave: project-level RBAC (follow-up fix) - Administrator sees every
        # project's totals, exactly as before; everyone else only sees
        # totals from projects they have a live membership row on, so this
        # can never leak the existence/scale of a project the caller
        # couldn't otherwise open (get_project/kpis/layers/evolution already
        # enforce that; this endpoint had been left out of that wave).
        member_id = None if user.role == Role.ADMINISTRATOR else user.user_id
        with self.db.connection() as conn, conn.cursor() as cur:
            totals, count = KpiRepository(cur).portfolio_totals(member_id=member_id)
        return {"portfolio": totals, "project_count": count}

    def delete_project(self, project_id: UUID, actor: CurrentUser) -> None:
        """Soft delete only - see app/repositories/projects.py. Datasets/layers/kpis
        keep their existing FK to this row; nothing physical is removed. One
        transaction: read the version to delete against, attempt the guarded
        UPDATE, and write the audit entry only if it actually took effect."""
        with self.db.transaction() as cur:
            repo = ProjectRepository(cur)
            # Read before the version check purely for its `name` - a 404 on
            # a project that never existed skips this and the audit write
            # below entirely, so there's no cost paid on the failure path.
            proj = repo.get(project_id)
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
                detail=f"Soft-deleted project '{proj['name'] if proj else project_id}'.",
                project_id=project_id,
            )

    def bulk_delete_projects(
        self, project_ids: list[UUID], actor: CurrentUser
    ) -> list[BulkDeleteItemResult]:
        """Same soft-delete + audit shape as `delete_project`, per id, in ONE
        transaction for the whole batch - not N separate transactions/HTTP
        calls. A per-id failure (already deleted, lost the optimistic-lock
        race, never existed) is reported back for that id only; it does not
        abort or roll back the ids that DID succeed, since each one's
        success is already committed at the row level (a 0-row UPDATE is a
        no-op, not an exception) by the time this returns."""
        results: list[BulkDeleteItemResult] = []
        with self.db.transaction() as cur:
            repo = ProjectRepository(cur)
            audit = AuditRepository(cur)
            for project_id in project_ids:
                proj = repo.get(project_id)
                name = proj["name"] if proj else str(project_id)
                version = repo.get_version(project_id)
                if version is None:
                    results.append(
                        BulkDeleteItemResult(
                            id=project_id, name=name, success=False,
                            error="Project not found.",
                        )
                    )
                    continue
                deleted = repo.soft_delete(
                    project_id, expected_version=version, deleted_by=actor.user_id
                )
                if not deleted:
                    results.append(
                        BulkDeleteItemResult(
                            id=project_id, name=name, success=False,
                            error="Project not found.",
                        )
                    )
                    continue
                audit.record(
                    actor_id=actor.user_id, actor_name=actor.username,
                    action=AuditAction.DELETE_PROJECT, target=str(project_id),
                    detail=f"Soft-deleted project '{name}' (bulk delete).",
                    project_id=project_id,
                )
                results.append(BulkDeleteItemResult(id=project_id, name=name, success=True))
        return results
