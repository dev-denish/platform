"""Admin-managed WMS/WFS domain allow-list + external layer metadata
(Wave: multi-format layers, Part B).

`WmsDomainRepository.list_domains()` is deliberately the ONLY read path any
caller uses to decide "is this domain currently approved" - both the
external-layer creation endpoint and, critically, the proxy endpoints that
re-fetch on every single tile/feature request (see app.services.external_fetch
and app/api/v1/external_layers.py) call this fresh every time rather than
caching the set, so a domain removed by an Administrator stops working on the
very next request, not just for new layers."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from app.core.errors import ConflictError


class WmsDomainRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def list_domains(self) -> list[dict[str, Any]]:
        self.cur.execute(
            "SELECT domain_id, domain, added_by, created_at FROM allowed_wms_domain "
            "ORDER BY domain"
        )
        return list(self.cur.fetchall())

    def domain_set(self) -> set[str]:
        """The live allow-list as a plain set of lowercase hostnames, for the
        SSRF guard's per-request check - see module docstring."""
        self.cur.execute("SELECT domain FROM allowed_wms_domain")
        return {r["domain"] for r in self.cur.fetchall()}

    def add(self, domain: str, added_by: UUID) -> dict[str, Any]:
        try:
            self.cur.execute(
                """
                INSERT INTO allowed_wms_domain (domain, added_by)
                VALUES (%s, %s)
                RETURNING domain_id, domain, added_by, created_at
                """,
                (domain, str(added_by)),
            )
        except psycopg.errors.UniqueViolation as e:
            raise ConflictError(f"Domain '{domain}' is already on the allow-list.") from e
        return self.cur.fetchone()  # type: ignore[return-value]

    def remove(self, domain_id: UUID) -> bool:
        self.cur.execute("DELETE FROM allowed_wms_domain WHERE domain_id = %s", (str(domain_id),))
        return self.cur.rowcount > 0


class ExternalLayerRepository:
    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur

    def insert(
        self, *, layer_id: UUID, domain: str, base_url: str, layer_name: str, service_kind: str,
    ) -> None:
        self.cur.execute(
            """
            INSERT INTO external_layer_source (layer_id, domain, base_url, layer_name, service_kind)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(layer_id), domain, base_url, layer_name, service_kind),
        )

    def get(self, layer_id: UUID | str) -> dict[str, Any] | None:
        self.cur.execute(
            "SELECT layer_id, domain, base_url, layer_name, service_kind "
            "FROM external_layer_source WHERE layer_id = %s",
            (str(layer_id),),
        )
        return self.cur.fetchone()
