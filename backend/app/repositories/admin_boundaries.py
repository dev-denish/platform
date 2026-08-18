"""Admin Boundaries registry persistence (Wave: Admin Boundaries).

`admin_village_registry`/`admin_district_registry` hold the OFFICIAL LGD
name+code lists (no geometry) - see 0019_admin_boundaries' docstring for why
they exist separately from vector_feature. Both are bulk-loaded once by the
ingestion script (COPY, same shape as VectorFeatureRepository's staging
pattern) and read here to (a) populate the district picker that scopes a
Village layer's queries and (b) compute the "boundary not available" gap for
a selected district."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import psycopg


class AdminBoundaryRegistryRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def load_district_registry(self, rows: Iterable[tuple[int, str, int, str]]) -> int:
        """rows: (district_lgd_code, district_name, state_lgd_code, state_name).
        Replaces the table wholesale - this is reference data re-derived
        from the same source CSV each time the ingestion script runs, never
        hand-edited, so there is nothing to preserve across a re-run."""
        self.cur.execute("TRUNCATE admin_district_registry")
        with self.cur.copy(
            "COPY admin_district_registry "
            "(district_lgd_code, district_name, state_lgd_code, state_name) FROM STDIN"
        ) as copy:
            n = 0
            for row in rows:
                copy.write_row(row)
                n += 1
        return n

    def load_village_registry(
        self, rows: Iterable[tuple[int, str, int | None, str | None, int, str, int, str]]
    ) -> int:
        """rows: (village_lgd_code, village_name, block_lgd_code, block_name,
        district_lgd_code, district_name, state_lgd_code, state_name)."""
        self.cur.execute("TRUNCATE admin_village_registry")
        with self.cur.copy(
            "COPY admin_village_registry (village_lgd_code, village_name, "
            "block_lgd_code, block_name, district_lgd_code, district_name, "
            "state_lgd_code, state_name) FROM STDIN"
        ) as copy:
            n = 0
            for row in rows:
                copy.write_row(row)
                n += 1
        return n

    def list_districts(self) -> list[dict[str, Any]]:
        self.cur.execute(
            "SELECT district_lgd_code, district_name, state_lgd_code, state_name "
            "FROM admin_district_registry ORDER BY state_name, district_name"
        )
        return list(self.cur.fetchall())

    def village_coverage_for_district(
        self, layer_id: str, district_lgd_code: str
    ) -> dict[str, Any]:
        """Joins the official registry for one district against whichever
        of those villages actually have a boundary polygon in the given
        Village layer - `missing` is exactly the "boundary not available"
        list the UI surfaces (see LayersPanel's district-scoped Village
        row), not an approximation from the aggregate coverage percentage
        the research pass reported."""
        self.cur.execute(
            """
            SELECT r.village_lgd_code, r.village_name,
                   (vf.feature_id IS NOT NULL) AS has_boundary
            FROM admin_village_registry r
            LEFT JOIN vector_feature vf
              ON vf.layer_id = %s
             AND vf.properties->>'village_lgd_code' = r.village_lgd_code::text
            WHERE r.district_lgd_code = %s
            ORDER BY r.village_name
            """,
            (layer_id, district_lgd_code),
        )
        rows = list(self.cur.fetchall())
        missing = [
            {"village_lgd_code": r["village_lgd_code"], "village_name": r["village_name"]}
            for r in rows
            if not r["has_boundary"]
        ]
        return {
            "total_registered": len(rows),
            "with_boundary": len(rows) - len(missing),
            "missing": missing,
        }
