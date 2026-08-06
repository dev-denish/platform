"""Forest-definition threshold persistence (Wave: permission grants, Part 2).

Singleton row - see migration 0016's `id BOOLEAN PRIMARY KEY DEFAULT TRUE
CHECK (id)` trick, which makes a second row impossible at the schema level.
There is deliberately no user-supplied id anywhere in this file: `get`/
`update` always target the one row that can exist.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg


class ForestDefinitionRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def get(self) -> dict[str, Any]:
        self.cur.execute(
            """
            SELECT s.canopy_cover_pct, s.min_height_m, s.min_area_ha,
                   s.updated_at, s.updated_by, u.username AS updated_by_username
            FROM forest_definition_setting s
            LEFT JOIN app_user u ON u.user_id = s.updated_by
            """
        )
        row = self.cur.fetchone()
        assert row is not None  # seeded by migration 0016, never absent
        return row

    def update(
        self,
        *,
        canopy_cover_pct: float,
        min_height_m: float,
        min_area_ha: float,
        updated_by: UUID | str,
    ) -> dict[str, Any]:
        self.cur.execute(
            """
            UPDATE forest_definition_setting
            SET canopy_cover_pct = %s, min_height_m = %s, min_area_ha = %s,
                updated_at = now(), updated_by = %s
            RETURNING canopy_cover_pct, min_height_m, min_area_ha, updated_at, updated_by
            """,
            (canopy_cover_pct, min_height_m, min_area_ha, str(updated_by)),
        )
        row = self.cur.fetchone()
        assert row is not None
        return row
