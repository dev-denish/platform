"""District picker data (Wave: Admin Boundaries) - list_districts is
read-only reference data (admin_district_registry), not project-scoped, so
this deliberately skips the require_project_view check every other vector
read here goes through: same visibility rule as any other reference layer
(Wave: Reference Layer Library), available to any authenticated user."""
from __future__ import annotations

from app.core.db import Database
from app.repositories.admin_boundaries import AdminBoundaryRegistryRepository


class AdminBoundaryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_districts(self) -> list[dict]:
        with self.db.connection() as conn, conn.cursor() as cur:
            return AdminBoundaryRegistryRepository(cur).list_districts()
