"""
The only place this backend makes an outbound HTTP request to a third-party
server (WMS/WFS layers - Wave: multi-format layers).

Threat model: a GIS Associate/Analyst can name ANY domain an Administrator has
approved, and (indirectly, since we proxy the request server-side rather than
having the browser hit the third party directly) cause THIS backend to make an
HTTP request to it. Two distinct attacks follow from that:

1. Domain-approval drift: a domain that was safe when approved might not stay
   safe (compromised, re-pointed DNS, sold). So the allow-list is re-checked
   on EVERY fetch, not cached from layer-creation time - callers must pass the
   live `allowed_domains` set they just queried, not something memoized.
2. SSRF via DNS: an "approved" hostname can resolve to an internal address
   (169.254.169.254 - the AWS/GCP/Azure instance-metadata endpoint - is the
   textbook case; a compromised or misconfigured DNS record is all it takes).
   A string check on the hostname catches neither a same-request rebind nor a
   domain that resolves to a private range from day one, so the block below
   runs against the RESOLVED IP, after DNS lookup, not the hostname.
3. DNS rebinding / TOCTOU: checking the resolved IP is worthless on its own
   if the actual HTTP client then does its OWN, independent DNS lookup to
   connect - a short-TTL record can legitimately answer differently a few
   milliseconds later (one public IP for the check, then
   169.254.169.254 for the real connection), silently defeating both the
   allow-list and the private-IP block. `_pin_dns` below closes this by
   making the SAME already-validated IP the only possible answer for any
   `getaddrinfo(host, ...)` call made while the request this function issues
   is in flight - there is no second real lookup for an attacker's DNS
   server to answer differently.

Everything here is synchronous (`httpx.Client`, not `AsyncClient`) - callers
run this inside a sync repository/service layer, same convention as the
raster ingestion path's synchronous rasterio calls.
"""
from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
import time
from urllib.parse import urlparse

import httpx

# Every RFC1918/link-local/loopback/reserved range relevant to SSRF, spelled
# out explicitly per the spec (rather than relying only on ipaddress.is_private,
# which is broader/vaguer than what was asked for) - 169.254.0.0/16 is listed
# first because it's the one with a real, named exploit (cloud instance
# metadata), not because the check order matters.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. 169.254.169.254
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local (RFC1918 equivalent)
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6 - unwrapped below too
]


class ExternalFetchError(Exception):
    """Client-safe: message is always safe to surface as a 4xx, never leaks
    internals beyond the host/URL the caller itself supplied."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # An IPv4-mapped IPv6 address (::ffff:10.0.0.1) must be evaluated as its
    # unwrapped IPv4 form, or a v4-mapped private address would sail through
    # every v4-specific network check above.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _assert_host_resolves_safely(host: str) -> str:
    """Returns the one resolved IP every subsequent connection for this
    request is pinned to (see `_pin_dns`) - not just a validation step.
    Every address the DNS answer offered must be safe, not merely the first
    one checked: a resolver could otherwise hand back a mix of one public
    and one private address hoping the "wrong" one gets used somewhere."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ExternalFetchError(f"Could not resolve host: {host}") from e
    if not infos:
        raise ExternalFetchError(f"Could not resolve host: {host}")
    resolved_ips = [sockaddr[0] for *_rest, sockaddr in infos]
    for raw_ip in resolved_ips:
        if _is_blocked_ip(ipaddress.ip_address(raw_ip)):
            raise ExternalFetchError(
                f"Host '{host}' resolves to a blocked, non-public address ({raw_ip})."
            )
    return resolved_ips[0]


_pinned = threading.local()


@contextlib.contextmanager
def _pin_dns(host: str, ip: str):
    """Scopes every `socket.getaddrinfo(host, ...)` call made on THIS thread,
    for the duration of this `with` block, to resolve to the single IP that
    was already validated - closing the TOCTOU/DNS-rebinding gap between
    `_assert_host_resolves_safely` and the HTTP client's own connection-time
    lookup (see module docstring, point 3). Thread-local (not global) so
    concurrent requests for different hosts on other threads are unaffected;
    scoped to exactly one (host, ip) pair so it can't accidentally pin an
    unrelated lookup (e.g. a redirect target, though those are already
    rejected outright before any such lookup would happen).
    """
    previous = getattr(_pinned, "value", None)
    _pinned.value = (host, ip)
    try:
        yield
    finally:
        _pinned.value = previous


_real_getaddrinfo = socket.getaddrinfo


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    pin = getattr(_pinned, "value", None)
    if pin is not None and host == pin[0]:
        return _real_getaddrinfo(pin[1], port, *args, **kwargs)
    return _real_getaddrinfo(host, port, *args, **kwargs)


socket.getaddrinfo = _pinned_getaddrinfo


def assert_domain_allowed(host: str, allowed_domains: set[str]) -> None:
    """`allowed_domains` must be freshly queried from allowed_wms_domain by
    the caller for THIS request - never cached across requests or read off
    the layer's own stored `domain` column alone, or a domain removed from
    the allow-list after a layer was created would keep working forever."""
    if host.lower() not in allowed_domains:
        raise ExternalFetchError(f"Domain not on the approved allow-list: {host}")


def safe_fetch(
    url: str,
    *,
    allowed_domains: set[str],
    timeout_s: float,
    max_bytes: int,
    connect_timeout_s: float | None = None,
) -> tuple[bytes, str]:
    """Fetch `url` after validating it against the allow-list and the
    private-IP block, with a hard timeout and response-size cap. Returns
    (body, content_type). Never follows redirects - re-validating a redirect
    target would just be this same function again, so a 3xx is treated as a
    rejection rather than silently chased (an approved domain redirecting to
    an internal address must not bypass the block by one hop).

    `timeout_s` is the TOTAL wall-clock budget for the whole request
    (connect + headers + full body), enforced twice: once via httpx's own
    read/write timeout, and again via an explicit deadline check across the
    streaming loop below. The second check is not redundant - httpx's read
    timeout only bounds the gap BETWEEN two reads, so a server that drip-feeds
    a few bytes every N seconds (each individual gap well under the timeout)
    would otherwise run far longer than `timeout_s` in total. `connect_timeout_s`
    (defaults to `timeout_s` if not given) bounds only the initial TCP+TLS
    handshake, kept short and separate so a slow/black-holed connect attempt
    fails fast without being allowed the full request budget."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ExternalFetchError("Only http/https URLs are allowed.")
    host = parsed.hostname
    if not host:
        raise ExternalFetchError("URL has no host.")

    assert_domain_allowed(host, allowed_domains)
    safe_ip = _assert_host_resolves_safely(host)

    connect_timeout = connect_timeout_s if connect_timeout_s is not None else timeout_s
    timeout = httpx.Timeout(
        connect=connect_timeout, read=timeout_s, write=timeout_s, pool=connect_timeout
    )
    deadline = time.monotonic() + timeout_s

    try:
        with (
            _pin_dns(host, safe_ip),
            httpx.Client(timeout=timeout, follow_redirects=False) as client,
            client.stream("GET", url) as resp,
        ):
            if 300 <= resp.status_code < 400:
                raise ExternalFetchError(
                    f"Server responded with a redirect ({resp.status_code}); redirects "
                    "are not followed."
                )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "application/octet-stream")
            body = bytearray()
            for chunk in resp.iter_bytes():
                if time.monotonic() > deadline:
                    raise ExternalFetchError(
                        f"Request to {host} exceeded the total timeout of {timeout_s}s."
                    )
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ExternalFetchError(
                        f"Response exceeded the {max_bytes} byte cap for this fetch."
                    )
            return bytes(body), content_type
    except httpx.TimeoutException as e:
        raise ExternalFetchError(f"Request to {host} timed out after {timeout_s}s.") from e
    except httpx.HTTPStatusError as e:
        raise ExternalFetchError(
            f"Server returned {e.response.status_code} for this request."
        ) from e
    except httpx.HTTPError as e:
        raise ExternalFetchError(f"Request to {host} failed: {e}") from e


def demo() -> None:
    """ponytail: smallest runnable check for the SSRF guard's core invariant -
    not a pytest suite, this module has no DB/service dependency to fixture."""
    for bad_ip in ("169.254.169.254", "127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5"):
        assert _is_blocked_ip(ipaddress.ip_address(bad_ip)), f"{bad_ip} should be blocked"
    for ok_ip in ("8.8.8.8", "1.1.1.1"):
        assert not _is_blocked_ip(ipaddress.ip_address(ok_ip)), f"{ok_ip} should be allowed"
    assert _is_blocked_ip(ipaddress.ip_address("::ffff:169.254.169.254"))

    try:
        assert_domain_allowed("evil.example.com", {"good.example.com"})
    except ExternalFetchError:
        pass
    else:
        raise AssertionError("non-allow-listed domain should have been rejected")

    assert_domain_allowed("good.example.com", {"good.example.com"})

    # TOCTOU/DNS-rebinding close: a hostname with no real DNS record still
    # resolves - to exactly the pinned IP - while `_pin_dns` is active, and
    # stops resolving again the instant it exits.
    fake_host = "external-fetch-demo-host.invalid"
    try:
        socket.getaddrinfo(fake_host, 80)
    except OSError:
        pass
    else:
        raise AssertionError(f"{fake_host} should not resolve without a pin")
    with _pin_dns(fake_host, "127.0.0.1"):
        assert socket.getaddrinfo(fake_host, 80)[0][4][0] == "127.0.0.1"
    try:
        socket.getaddrinfo(fake_host, 80)
    except OSError:
        pass
    else:
        raise AssertionError("pin leaked past its context manager")

    print("external_fetch.demo: all checks passed")


if __name__ == "__main__":
    demo()
