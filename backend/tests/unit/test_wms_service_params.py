"""Unit test for WmsService.build_get_map_params - pure, no DB/network,
covering the client-param-stripping half of the WMS SSRF guard (the other
half, safe_fetch's allow-list/private-IP/DNS-pinning, is covered by
test_external_fetch.py)."""
from __future__ import annotations

from app.services.wms_service import build_get_map_params


def test_sld_and_sld_body_are_stripped_not_forwarded():
    """SLD/SLD_BODY can carry a second, attacker-chosen URL for the upstream
    server to fetch - a request isn't safe just because its own host passed
    the allow-list if a param inside it points elsewhere."""
    params = build_get_map_params(
        {
            "bbox": "0,0,1,1", "width": "256", "height": "256",
            "SLD": "http://169.254.169.254/latest/meta-data/",
            "sld_body": "<StyledLayerDescriptor>evil</StyledLayerDescriptor>",
        },
        layer_name="demo:layer",
    )
    assert "sld" not in {k.lower() for k in params}
    assert "sld_body" not in {k.lower() for k in params}
    assert params["bbox"] == "0,0,1,1"  # legitimate rendering params still pass through


def test_layers_and_token_cannot_be_overridden_by_the_client():
    """A client must never redirect this proxy to a different layer on the
    approved server, or smuggle its own `token` into the forwarded request -
    both always come from stored/verified state, never the request."""
    params = build_get_map_params(
        {"layers": "some:other-layer", "token": "attacker-supplied"},
        layer_name="demo:layer",
    )
    assert params["layers"] == "demo:layer"
    assert "token" not in params


def test_service_version_request_are_pinned_after_client_params_merge():
    """Even if the client supplies its own service/version/request, this
    proxy must never become a general GetCapabilities/GetFeatureInfo proxy
    against the approved server - the fixed values win."""
    params = build_get_map_params(
        {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
        layer_name="demo:layer",
    )
    assert params["request"] == "GetMap"
    assert params["version"] == "1.1.1"


def demo() -> None:
    test_sld_and_sld_body_are_stripped_not_forwarded()
    test_layers_and_token_cannot_be_overridden_by_the_client()
    test_service_version_request_are_pinned_after_client_params_merge()
    print("test_wms_service_params.demo: all checks passed")


if __name__ == "__main__":
    demo()
