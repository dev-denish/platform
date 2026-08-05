"""
Dependency injection.

Existing implementation (MVP): handlers reached for module-global DB helpers and
re-implemented the role check inline. No seam for testing, no single place to swap
implementations.

Enterprise solution: everything a handler needs is provided by a FastAPI dependency
that reads shared resources off `app.state` (wired once in the lifespan). Services
are constructed per-request from those shared resources. Tests override these
dependencies to inject fakes. RBAC is a single reusable dependency factory
(`require_role`) - the rule is defined ONCE.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AuthError, ForbiddenError
from app.domain.dtos import CurrentUser
from app.domain.enums import Role
from app.services.adhoc_layer_service import AdhocLayerService
from app.services.auth_service import AuthService
from app.services.dataset_delete_service import DatasetDeleteService
from app.services.ingestion.service import IngestionService
from app.services.ingestion.storage import Storage
from app.services.jobs_service import JobService
from app.services.layer_rename_service import LayerRenameService
from app.services.legend_service import ClassLegendService
from app.services.membership_service import MembershipService
from app.services.project_service import ProjectService
from app.services.reference_layer_service import ReferenceLayerService
from app.services.tile_service import TileService
from app.services.user_service import UserService
from app.services.vector_layer_service import VectorLayerService
from app.services.wms_service import WmsService
from app.workers.queue import TaskRunner


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_task_runner(request: Request) -> TaskRunner:
    return request.app.state.task_runner


def get_auth_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(db, settings)


def get_project_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> ProjectService:
    return ProjectService(db, settings, storage)


def get_membership_service(db: Annotated[Database, Depends(get_db)]) -> MembershipService:
    return MembershipService(db)


def get_user_service(db: Annotated[Database, Depends(get_db)]) -> UserService:
    return UserService(db)


def get_ingestion_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> IngestionService:
    return IngestionService(db, settings, storage)


def get_job_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobService:
    return JobService(db, settings)


def get_vector_layer_service(db: Annotated[Database, Depends(get_db)]) -> VectorLayerService:
    return VectorLayerService(db)


def get_wms_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WmsService:
    return WmsService(db, settings)


def get_reference_layer_service(db: Annotated[Database, Depends(get_db)]) -> ReferenceLayerService:
    return ReferenceLayerService(db)


def get_adhoc_layer_service(db: Annotated[Database, Depends(get_db)]) -> AdhocLayerService:
    return AdhocLayerService(db)


def get_class_legend_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> ClassLegendService:
    return ClassLegendService(db, settings, storage)


def get_layer_rename_service(db: Annotated[Database, Depends(get_db)]) -> LayerRenameService:
    return LayerRenameService(db)


def get_dataset_delete_service(
    db: Annotated[Database, Depends(get_db)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> DatasetDeleteService:
    return DatasetDeleteService(db, storage)


def get_tile_service(
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> TileService:
    return TileService(db, settings, storage)


def get_current_user(
    auth: Annotated[AuthService, Depends(get_auth_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header.")
    token = authorization.removeprefix("Bearer ").strip()
    return auth.current_user_from_access(token)


def require_role(*allowed: Role):
    """Reusable RBAC dependency. The single definition of 'who may do X'."""

    def checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed:
            raise ForbiddenError(
                f"This action requires one of: {', '.join(r.value for r in allowed)}."
            )
        return user

    return checker


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
