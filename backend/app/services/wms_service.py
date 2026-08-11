"""
WMS/WFS domain allow-list management + external layer creation/proxying
(Wave: multi-format layers, Part B).

The proxy methods (`fetch_wms_tile`, `fetch_wfs_features`) are the
security-critical path: both re-query the LIVE allow-list
(`WmsDomainRepository.domain_set()`) and run it through
`app.services.external_fetch.safe_fetch` on EVERY call - never once at
layer-creation time and never cached - because a domain an Administrator
approved yesterday is not guaranteed to still be safe today (compromised
server, re-pointed DNS, or simply removed from the list since). See
external_fetch.py's own docstring for the DNS-resolution/private-IP side of
this; this module only owns "is the domain still approved" and "build the
correct GetMap/GetFeature URL to fetch."
"""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode
from uuid import UUID

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AuthError, ForbiddenError, NotFoundError, ValidationError
from app.core.security import decode_token
from app.domain.dtos import CurrentUser, WmsDomainOut
from app.domain.enums import AuditAction, LayerKind
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, LayerRepository
from app.repositories.wms_domains import ExternalLayerRepository, WmsDomainRepository
from app.services.external_fetch import ExternalFetchError, safe_fetch
from app.services.project_access import (
    resolve_project_for_upload,
    resolve_reference_library_project,
)

# Bare hostname, e.g. "mapserver.example.com" - no scheme, no path, no
# port trickery. Deliberately stricter than "anything urlparse accepts",
# since this is the string every fetch's allow-list check compares against.
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(\.{_LABEL})+$")

# WMS's SLD/SLD_BODY params let a CLIENT hand the server a styling document
# (inline, or a URL for the server to fetch) - a real, named vector (e.g.
# GeoServer's remote-SLD advisories) for smuggling a second, attacker-chosen
# URL past a domain allow-list that only ever inspected the request's own
# host. This app has no feature that depends on client-supplied styling, so
# these are stripped outright rather than parsed/validated - never forwarded
# to the upstream server at all. `layers`/`token` are stripped for the
# unrelated reason documented at their use below (identity/auth values that
# must come from stored state, never the request).
_STRIPPED_CLIENT_PARAMS = {"layers", "token", "sld", "sld_body"}


def build_get_map_params(query_params: dict[str, str], layer_name: str) -> dict[str, str]:
    """Pure so the client-param-stripping is unit-testable without a DB or
    network - see `_STRIPPED_CLIENT_PARAMS` and `fetch_wms_tile`'s own
    docstring for why each key is excluded/pinned."""
    return {
        "format": "image/png", "transparent": "true", "srs": "EPSG:3857",
        **{k: v for k, v in query_params.items() if k.lower() not in _STRIPPED_CLIENT_PARAMS},
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": layer_name,
    }


class WmsService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # ---------------------------------------------------------------- allow-list

    def list_domains(self) -> list[WmsDomainOut]:
        with self.db.connection() as conn, conn.cursor() as cur:
            rows = WmsDomainRepository(cur).list_domains()
        return [WmsDomainOut(**r) for r in rows]

    def add_domain(self, domain: str, actor: CurrentUser) -> WmsDomainOut:
        domain = domain.strip().lower()
        if not _HOSTNAME_RE.match(domain):
            raise ValidationError(
                f"'{domain}' doesn't look like a bare hostname (no scheme/path/port)."
            )
        with self.db.transaction() as cur:
            row = WmsDomainRepository(cur).add(domain, actor.user_id)
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.ADD_WMS_DOMAIN, target=domain,
                detail=f"Added '{domain}' to the WMS/WFS allow-list.",
            )
        return WmsDomainOut(**row)

    def remove_domain(self, domain_id: UUID, actor: CurrentUser) -> None:
        with self.db.transaction() as cur:
            removed = WmsDomainRepository(cur).remove(domain_id)
            if not removed:
                raise NotFoundError("Domain not found on the allow-list.")
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.REMOVE_WMS_DOMAIN, target=str(domain_id),
                detail=f"Removed domain {domain_id} from the WMS/WFS allow-list.",
            )

    # ---------------------------------------------------------------- layer creation

    def create_external_layer(
        self, *, project_name: str, region: str, domain: str, service_kind: str,
        path: str, layer_name: str, actor: CurrentUser, is_reference: bool = False,
        create_new_project: bool = True,
    ) -> UUID:
        """Returns the new layer_id. Domain is checked against the allow-list
        HERE too (not just at proxy-fetch time) so a non-approved domain is
        rejected immediately with a clear error, rather than silently saved
        and only failing later on first render - but this check is a
        courtesy, not the enforcement boundary: see fetch_wms_tile/
        fetch_wfs_features, which re-check unconditionally on every fetch
        regardless of what passed here at creation time.

        Wave: Reference Layer Library - `is_reference=True` skips per-project
        membership entirely (same reasoning as IngestionService._resolve_project)
        and always lands in the one shared library project, regardless of
        `project_name`/`region`.

        `create_new_project` (Wave: WMS project-name footgun fix, same shape as
        upload's - see project_access.resolve_project_for_upload) defaults True
        here so any other/future caller of this method is unaffected; the real
        HTTP entry point (api/v1/external_layers.py) is the one place that
        passes False, since that endpoint's project_name always comes from the
        project the caller already has open (ProjectDetailPage), never free
        text - a stale/mismatched name there should error, not fork a
        duplicate."""
        domain = domain.strip().lower()
        path = path if path.startswith("/") or not path else f"/{path}"
        base_url = f"https://{domain}{path}"

        with self.db.transaction() as cur:
            if domain not in WmsDomainRepository(cur).domain_set():
                raise ForbiddenError(f"Domain '{domain}' is not on the approved allow-list.")

            project_id = (
                resolve_reference_library_project(cur)
                if is_reference
                else resolve_project_for_upload(
                    cur, project_name=project_name, region=region, actor=actor,
                    create_new_project=create_new_project,
                )
            )
            batch_id = uuid.uuid4()
            dataset_id = DatasetRepository(cur).insert(
                project_id=project_id, dataset_type="Boundary", source=domain,
                accuracy_score=None, date_processed=None, batch_id=batch_id,
                is_reference=is_reference,
            )
            layer_kind = LayerKind.EXTERNAL_WMS if service_kind == "wms" else LayerKind.EXTERNAL_WFS
            # Full-earth placeholder extent - the real bbox for an external
            # service isn't known until GetCapabilities is parsed, which
            # this endpoint deliberately doesn't attempt (Part B's own
            # verification only requires that an approved-domain layer
            # renders; capability introspection is out of scope for this
            # wave). Leaflet's WMSTileLayer requests tiles for whatever
            # viewport bbox is on screen regardless of this stored extent.
            layer_id = LayerRepository(cur).insert_non_raster(
                dataset_id=dataset_id, layer_kind=layer_kind.value,
                crs="EPSG:3857", bounds=(-180.0, -85.0, 180.0, 85.0),
            )
            ExternalLayerRepository(cur).insert(
                layer_id=layer_id, domain=domain, base_url=base_url,
                layer_name=layer_name, service_kind=service_kind,
            )
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.CREATE_EXTERNAL_LAYER, target=str(layer_id),
                detail=f"{service_kind.upper()} layer '{layer_name}' from {domain}.",
                project_id=project_id,
            )
        return layer_id

    # ------------------------------------------------------------ proxy (fetch-time enforcement)

    def verify_token(self, layer_id: UUID, token: str) -> None:
        payload = decode_token(self.settings, token, expected_type="tile")
        if payload.get("sub") != str(layer_id):
            raise AuthError("This token is not valid for this layer.")

    def _get_source(self, layer_id: UUID, expected_kind: str) -> dict:
        with self.db.connection() as conn, conn.cursor() as cur:
            source = ExternalLayerRepository(cur).get(layer_id)
        if not source or source["service_kind"] != expected_kind:
            raise NotFoundError("No such external layer.")
        return source

    def fetch_wms_tile(
        self, layer_id: UUID, token: str, query_params: dict[str, str]
    ) -> tuple[bytes, str]:
        """`query_params` is whatever Leaflet's WMSTileLayer appended to the
        proxy URL for this one tile (bbox/width/height/srs/...) - passed
        through for those rendering-only values, but every
        security/identity-relevant param (`service`, `version`, `request`,
        `layers`) is pinned to a fixed/stored value AFTER the client's own
        params are merged in, not before: a client must never be able to
        turn this tile proxy into a general GetCapabilities/GetFeatureInfo
        proxy against the approved server, or redirect it to a different
        layer on that server (see ExternalLayerRepository - `layers` always
        comes from what was stored at creation time, never the request)."""
        self.verify_token(layer_id, token)
        source = self._get_source(layer_id, "wms")

        with self.db.connection() as conn, conn.cursor() as cur:
            allowed = WmsDomainRepository(cur).domain_set()

        params = build_get_map_params(query_params, source["layer_name"])
        url = f"{source['base_url']}?{urlencode(params)}"
        try:
            return safe_fetch(
                url, allowed_domains=allowed,
                timeout_s=self.settings.wms_request_timeout_s,
                connect_timeout_s=self.settings.wms_connect_timeout_s,
                max_bytes=self.settings.wms_max_response_bytes,
            )
        except ExternalFetchError as e:
            # Translated to a DomainError here (not left to propagate as a
            # bare exception) so a blocked/failed fetch is a clean 4xx to the
            # client, not an unhandled-exception 500 - the SSRF guard itself
            # already did its job by raising at all; this is purely about
            # surfacing that outcome correctly over HTTP.
            raise ForbiddenError(str(e)) from e

    def fetch_wfs_features(self, layer_id: UUID, token: str) -> bytes:
        self.verify_token(layer_id, token)
        source = self._get_source(layer_id, "wfs")

        with self.db.connection() as conn, conn.cursor() as cur:
            allowed = WmsDomainRepository(cur).domain_set()

        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": source["layer_name"], "outputFormat": "application/json",
        }
        url = f"{source['base_url']}?{urlencode(params)}"
        try:
            body, _content_type = safe_fetch(
                url, allowed_domains=allowed,
                timeout_s=self.settings.wms_request_timeout_s,
                connect_timeout_s=self.settings.wms_connect_timeout_s,
                max_bytes=self.settings.wms_max_response_bytes,
            )
        except ExternalFetchError as e:
            raise ForbiddenError(str(e)) from e
        return body
