"""Domain enumerations. Single source of truth for the vocabulary the platform
speaks. Used by validation (DTOs), authorization (roles), and the DB check
constraints in the Alembic migration - so an invalid value cannot enter through
the API layer OR be written directly to the database."""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMINISTRATOR = "Administrator"
    GIS_ASSOCIATE = "GIS Associate"
    ANALYST = "Analyst"
    VERIFIER = "Verifier"
    VIEWER = "Viewer"


# Roles allowed to ingest/upload datasets. Centralised so the rule is defined once
# (the MVP re-implemented this check inline and diverged from its own helper).
UPLOAD_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR, Role.GIS_ASSOCIATE})

# Roles allowed to (soft-)delete a project. Administrator only.
DELETE_PROJECT_ROLES: frozenset[Role] = frozenset({Role.ADMINISTRATOR})


class DatasetType(StrEnum):
    LULC = "LULC"
    NDVI = "NDVI"
    BIOMASS = "Biomass"
    BOUNDARY = "Boundary"


class ProjectStatus(StrEnum):
    ACTIVE = "Active"
    ARCHIVED = "Archived"
    UNDER_REVIEW = "Under Review"


class AuditAction(StrEnum):
    LOGIN = "login"
    INGEST_DATASET = "ingest_dataset"
    CREATE_PROJECT = "create_project"
    DELETE_DATASET = "delete_dataset"
    DELETE_PROJECT = "delete_project"
