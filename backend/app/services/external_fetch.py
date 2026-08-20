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
4. IDNA/punycode mismatch: everything above only holds if the exact string
   validated, resolved and pinned is byte-for-byte the same string the HTTP
   client itself uses to open the connection. httpx normalizes a URL's host
   to lowercase + IDNA/punycode (`request.url.raw_host`) before connecting;
   `urlparse(url).hostname` does NOT do that IDNA step. For a raw-Unicode
   hostname (e.g. "中国.icom.museum") those two strings differ, so the
   validated/pinned string and the connect-time string would silently
   disagree - the pin's own equality check would then think it was looking
   at an unrelated host and fall through to a live, unvalidated DNS lookup,
   defeating point 3 above for exactly the hosts that need it most. Fixed by
   normalizing the host through `httpx.URL` itself (see `_normalize_host`)
   before any validation/pinning happens, rather than reimplementing IDNA by
   hand - `str.encode("idna")` is the older stdlib IDNA2003 codec and does
   not even agree with httpx's own (IDNA2008/UTS46, via the `idna` package)
   normalization on plain-ASCII case-folding, let alone real IDNA inputs.

Everything here is synchronous (`httpx.Client`, not `AsyncClient`) - callers
run this inside a sync repository/service layer, same convention as the
raster ingestion path's synchronous rasterio calls.
"""
from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
import threading
import time

import httpx

logger = logging.getLogger(__name__)

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

# RFC 6052 NAT64 well-known prefix - a stateless IPv6 representation of an
# IPv4 address, embedded in the low 32 bits (64:ff9b::a9fe:a9fe embeds
# 169.254.169.254, the cloud metadata endpoint). Unwrapped below, same as
# IPv4-mapped IPv6, rather than added to `_BLOCKED_NETWORKS` outright:
# blocking the whole /96 unconditionally would also reject NAT64-translated
# requests to entirely public addresses, which isn't what this guard is for.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class ExternalFetchError(Exception):
    """Client-safe: message is always safe to surface as a 4xx, never leaks
    internals beyond the host/URL the caller itself supplied."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        # An IPv4-mapped IPv6 address (::ffff:10.0.0.1) must be evaluated as
        # its unwrapped IPv4 form, or a v4-mapped private address would sail
        # through every v4-specific network check below.
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64_PREFIX:
            # Recurse on the embedded IPv4 address specifically (not a
            # blanket block of the whole prefix) so a NAT64-encoded
            # 169.254.169.254 is blocked because THAT address is the cloud
            # metadata endpoint, exactly like the IPv4-mapped case above.
            embedded_v4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
            return _is_blocked_ip(embedded_v4)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _normalize_host(bare_host: str) -> str:
    """Normalizes a bare hostname (no scheme, no path - a URL's host
    component, or one raw entry of an allow-list) exactly the way httpx
    normalizes the host of a URL it's about to connect to: lowercased, and
    IDNA/punycode-encoded for any non-ASCII label. This is the ONE
    normalization used everywhere in this module a host is validated,
    resolved, pinned, or compared against the allow-list (`safe_fetch`,
    `assert_domain_allowed`, `_pinned_getaddrinfo`'s defense-in-depth check)
    - so the string checked is always identical to the string
    `request.url.raw_host` will be at actual connect time.

    Deliberately delegates to `httpx.URL` rather than reimplementing IDNA
    with the stdlib `str.encode("idna")` codec: that codec is IDNA2003, is
    NOT what httpx itself uses (httpx uses the `idna` PyPI package - IDNA2008
    with UTS46 mapping - already a required httpx dependency), and doesn't
    even agree with httpx on the plain-ASCII case, since it does not
    lowercase ASCII labels (`"EXAMPLE.com".encode("idna") == b"EXAMPLE.com"`,
    not `b"example.com"`) - reimplementing IDNA by hand here would just
    swap one host/connect-string mismatch for another.

    IPv6 literals are bracket-wrapped first because that's how httpx's own
    URL grammar recognises them (an unbracketed `host:port`-shaped string is
    otherwise parsed as a hostname plus a port and rejected).

    Rejects (rather than silently truncating) anything that isn't a bare
    hostname: `httpx.URL` happily parses "good.example.com@evil.com" down to
    a host of just `evil.com` (userinfo truncation), "evil.com#good.example.com"
    down to `evil.com` (fragment), and "good.example.com/wms" down to just
    the host part (path) - each of those would otherwise let a single
    delimiter-bearing string quietly resolve to a DIFFERENT, narrower or
    unintended host than what was written, which is exactly wrong for an
    allow-list entry (a malformed entry must fail closed, not get
    reinterpreted as some other, possibly-attacker-chosen host). A bare
    hostname parses with empty userinfo/query/fragment and a `raw_path` of
    either empty or the default "/" and no port - anything else here means
    the input carried a delimiter it shouldn't have."""
    candidate = bare_host
    if ":" in bare_host and not bare_host.startswith("["):
        candidate = f"[{bare_host}]"
    try:
        parsed = httpx.URL(f"http://{candidate}")
    except httpx.InvalidURL as e:
        raise ExternalFetchError(f"Malformed host: {bare_host!r}") from e
    raw_host = parsed.raw_host
    if not raw_host:
        raise ExternalFetchError(f"Malformed host: {bare_host!r}")
    if (
        parsed.userinfo
        or parsed.port is not None
        or parsed.raw_path not in (b"", b"/")
        or parsed.query
        or parsed.fragment
    ):
        raise ExternalFetchError(
            f"Malformed host: {bare_host!r} (expected a bare hostname, not a "
            "URL with userinfo, a port, a path, a query, or a fragment)."
        )
    return raw_host.decode("ascii")


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
    if pin is None:
        # No `safe_fetch` call has a pin active on this thread right now -
        # an entirely unrelated `getaddrinfo` elsewhere in the app (or one
        # made after a prior pin's `with` block already exited) must resolve
        # normally.
        return _real_getaddrinfo(host, port, *args, **kwargs)

    pinned_host, pinned_ip = pin
    if host == pinned_host:
        return _real_getaddrinfo(pinned_ip, port, *args, **kwargs)

    # `host` doesn't match the pinned string byte-for-byte while a pin IS
    # active. Before treating this as a genuinely unrelated lookup (e.g. an
    # HTTP(S)_PROXY hostname resolved separately from the fetch target -
    # httpx.Client trusts the environment by default), check whether it's
    # actually THE SAME target under a different string form - raw Unicode
    # vs. IDNA/punycode, different case, ... - the exact class of bug this
    # guards against (see module docstring, point 4). `safe_fetch` already
    # normalizes `host` before ever calling `_pin_dns`, so this branch
    # should be unreachable in practice; it exists purely as defense in
    # depth against a future regression that reintroduces a normalization
    # mismatch. Failing closed here (instead of silently falling through to
    # a live, unvalidated DNS lookup, as before) is the actual fix.
    try:
        same_target = _normalize_host(host) == _normalize_host(pinned_host)
    except ExternalFetchError:
        same_target = False
    if same_target:
        logger.error(
            "SSRF guard fail-closed: getaddrinfo(%r) was called while a DNS "
            "pin for %r was active. The two hosts normalize to the same "
            "target but do not match byte-for-byte, so this is either a "
            "DNS-rebinding attempt or a host-normalization regression in "
            "this module - refusing to fall back to a live, unvalidated DNS "
            "lookup.",
            host,
            pinned_host,
        )
        raise ExternalFetchError(
            f"DNS pin mismatch: {host!r} normalizes to the same host as the "
            f"already-pinned target {pinned_host!r} but does not match it "
            "exactly; refusing to fall back to a live, unvalidated DNS "
            "lookup."
        )
    return _real_getaddrinfo(host, port, *args, **kwargs)


socket.getaddrinfo = _pinned_getaddrinfo


def assert_domain_allowed(host: str, allowed_domains: set[str]) -> None:
    """`allowed_domains` must be freshly queried from allowed_wms_domain by
    the caller for THIS request - never cached across requests or read off
    the layer's own stored `domain` column alone, or a domain removed from
    the allow-list after a layer was created would keep working forever.

    Both `host` and every entry of `allowed_domains` are independently
    normalized (see `_normalize_host`) before comparison - not just
    `.lower()`'d - so a domain an Administrator typed in as raw Unicode
    still matches an incoming IDNA/punycode-encoded hostname (and vice
    versa), rather than the allow-list silently rejecting an approved
    domain, or two spellings of the same domain comparing unequal, purely
    because of representation rather than identity."""
    normalized_host = _normalize_host(host)
    normalized_allowed: set[str] = set()
    for domain in allowed_domains:
        try:
            normalized_allowed.add(_normalize_host(domain))
        except ExternalFetchError:
            # A malformed allow-list entry can never legitimately match any
            # real host - skip it rather than let one bad row 500 every
            # fetch that checks this allow-list.
            continue
    if normalized_host not in normalized_allowed:
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
    # Parsed with `httpx.URL` (not `urllib.parse.urlparse`) specifically so
    # `host` below is IDENTICAL to what httpx's own `request.url.raw_host`
    # will be when `client.stream(...)` connects further down - lowercased,
    # IDNA/punycode-encoded - see module docstring, point 4. `urlparse(...)
    # .hostname` does not IDNA-encode, so it can silently disagree with the
    # connect-time string for a non-ASCII hostname.
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as e:
        raise ExternalFetchError(f"Invalid URL: {url}") from e
    if parsed.scheme not in ("http", "https"):
        raise ExternalFetchError("Only http/https URLs are allowed.")
    if not parsed.raw_host:
        raise ExternalFetchError("URL has no host.")
    host = parsed.raw_host.decode("ascii")

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
    # RFC 6052 NAT64 embedding of the same metadata address.
    assert _is_blocked_ip(ipaddress.ip_address("64:ff9b::a9fe:a9fe"))

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
