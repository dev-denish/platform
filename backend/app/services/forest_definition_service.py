"""
Forest-definition threshold (Wave: permission grants, Part 2 - the first
real use case for has_permission()).

GET is open to any authenticated user - this value feeds reports everyone
sees. PUT is gated by has_permission(actor, "edit_forest_definition"):
Administrator, or anyone individually granted it (app.domain.permissions).
Every change is audited old-value -> new-value, since this feeds real
compliance reporting.
"""
from __future__ import annotations

from app.core.db import Database
from app.core.errors import ForbiddenError
from app.domain.dtos import CurrentUser, ForestDefinitionOut, UpdateForestDefinitionRequest
from app.domain.enums import AuditAction
from app.domain.permissions import has_permission
from app.repositories.audit import AuditRepository
from app.repositories.forest_definition import ForestDefinitionRepository

_PERMISSION = "edit_forest_definition"


class ForestDefinitionService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, actor: CurrentUser) -> ForestDefinitionOut:
        with self.db.connection() as conn, conn.cursor() as cur:
            row = ForestDefinitionRepository(cur).get()
            can_edit = has_permission(cur, actor, _PERMISSION)
        return ForestDefinitionOut(**row, can_edit=can_edit)

    def update(
        self, body: UpdateForestDefinitionRequest, actor: CurrentUser
    ) -> ForestDefinitionOut:
        with self.db.transaction() as cur:
            if not has_permission(cur, actor, _PERMISSION):
                raise ForbiddenError(
                    "Editing the forest definition requires the "
                    "'edit_forest_definition' permission."
                )
            repo = ForestDefinitionRepository(cur)
            before = repo.get()
            row = repo.update(
                canopy_cover_pct=body.canopy_cover_pct,
                min_height_m=body.min_height_m,
                min_area_ha=body.min_area_ha,
                updated_by=actor.user_id,
            )
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.UPDATE_FOREST_DEFINITION, target="forest_definition",
                detail=(
                    f"Canopy cover {before['canopy_cover_pct']}% -> {body.canopy_cover_pct}%, "
                    f"min height {before['min_height_m']}m -> {body.min_height_m}m, "
                    f"min area {before['min_area_ha']}ha -> {body.min_area_ha}ha."
                ),
            )
        return ForestDefinitionOut(**row, updated_by_username=actor.username, can_edit=True)
