"""Unit tests for the SSRF guard (Wave: multi-format layers, Part B) - real
HTTP round trips against a genuine local `http.server` (stdlib, no DB, no
external network), not just the pure `_is_blocked_ip` table already covered
by `external_fetch.demo()`. These prove `safe_fetch` itself blocks a request
before any TCP connection is made, and that the mechanics (timeout,
redirect-refusal, response-size cap, DNS pinning) actually work end-to-end
through `httpx`, not just in isolated helper functions.

127.0.0.1/169.254.169.254 are exactly the addresses this guard exists to
block, so there is no way to stand up a "real successful fetch" against them
in this test suite - by design, nothing reachable from a sandboxed backend
that ISN'T loopback/private is available here either. The success-path tests
below therefore monkeypatch `_is_blocked_ip` to a no-op for that one
assertion only, with a comment explaining why - proving the actual HTTP
fetch/timeout/cap/redirect logic works when the guard isn't the thing under
test, never that the guard itself can be bypassed (see the block tests, which
never touch this patch).
"""
from __future__ import annotations

import http.server
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from app.services import external_fetch as EF


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    """Every subclass attribute below is set per-test on the class itself
    (see `local_server`), since `http.server.HTTPServer` instantiates a fresh
    handler object per request - only class-level state survives across
    requests within one test."""

    hits: int = 0
    body: bytes = b"hello"
    content_type: str = "text/plain"
    delay_s: float = 0.0
    redirect_to: str | None = None
    # Drip-feeds `trickle_chunks` chunks of `trickle_chunk_size` bytes, each
    # separated by `trickle_delay_s` - every individual gap stays well under
    # a per-operation read timeout, but the sum across all chunks does not.
    trickle_chunks: int = 0
    trickle_chunk_size: int = 8
    trickle_delay_s: float = 0.0

    def do_GET(self) -> None:  # noqa: N802 - stdlib-mandated method name
        type(self).hits += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        if self.trickle_chunks:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(self.trickle_chunks * self.trickle_chunk_size))
            self.end_headers()
            for _ in range(self.trickle_chunks):
                self.wfile.write(b"x" * self.trickle_chunk_size)
                self.wfile.flush()
                time.sleep(self.trickle_delay_s)
            return
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:  # silence stdlib access logs
        pass


@pytest.fixture
def local_server() -> Iterator[tuple[http.server.HTTPServer, type[_CountingHandler]]]:
    handler_cls = type(f"Handler{id(object())}", (_CountingHandler,), {"hits": 0})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, handler_cls
    finally:
        server.shutdown()
        thread.join(timeout=2)


# --------------------------------------------------------------- the guard actually blocks


def test_safe_fetch_blocks_loopback_without_ever_connecting(local_server):
    server, handler_cls = local_server
    port = server.server_address[1]

    with pytest.raises(EF.ExternalFetchError, match="blocked"):
        EF.safe_fetch(
            f"http://127.0.0.1:{port}/", allowed_domains={"127.0.0.1"},
            timeout_s=2.0, max_bytes=1024,
        )

    assert handler_cls.hits == 0, "the block must happen before any TCP connection"


def test_safe_fetch_blocks_a_real_hostname_that_resolves_to_loopback(local_server):
    """Same block, but through a REAL hostname (not an IP literal) - proves
    the DNS-resolution step, not just a string comparison against '127.0.0.1',
    is what's catching this."""
    server, handler_cls = local_server
    port = server.server_address[1]

    with pytest.raises(EF.ExternalFetchError, match="blocked"):
        EF.safe_fetch(
            f"http://localhost:{port}/", allowed_domains={"localhost"},
            timeout_s=2.0, max_bytes=1024,
        )

    assert handler_cls.hits == 0


def test_safe_fetch_blocks_the_cloud_metadata_address_even_if_allowlisted():
    """The textbook SSRF target (external_fetch.py's own docstring) - even a
    domain string identical to this IP, freshly added to the allow-list,
    must still be blocked at the resolved-IP check. No live server needed:
    169.254.169.254 is an IP literal, so this never touches the network."""
    with pytest.raises(EF.ExternalFetchError, match="blocked"):
        EF.safe_fetch(
            "http://169.254.169.254/latest/meta-data/",
            allowed_domains={"169.254.169.254"}, timeout_s=2.0, max_bytes=1024,
        )


def test_safe_fetch_rejects_a_domain_not_on_the_allow_list(local_server):
    server, handler_cls = local_server
    port = server.server_address[1]

    with pytest.raises(EF.ExternalFetchError, match="allow-list"):
        EF.safe_fetch(
            f"http://127.0.0.1:{port}/", allowed_domains=set(), timeout_s=2.0, max_bytes=1024,
        )

    assert handler_cls.hits == 0


def test_safe_fetch_rejects_non_http_schemes():
    with pytest.raises(EF.ExternalFetchError, match="http/https"):
        EF.safe_fetch(
            "file:///etc/passwd", allowed_domains={"anything"}, timeout_s=2.0, max_bytes=1024,
        )


# --------------------------------------------------------------- HTTP mechanics, guard lifted
#
# `_is_blocked_ip` is patched to a no-op ONLY in the tests below, so the real
# fetch (streaming, timeout, redirect-refusal, size cap, DNS pin) can be
# proven against a genuine local server - see module docstring for why a
# public, non-private test target isn't available in this sandbox.


@pytest.fixture
def guard_lifted(monkeypatch):
    monkeypatch.setattr(EF, "_is_blocked_ip", lambda ip: False)


def test_safe_fetch_succeeds_against_a_real_local_server(local_server, guard_lifted):
    server, handler_cls = local_server
    port = server.server_address[1]
    handler_cls.body = b'{"ok": true}'
    handler_cls.content_type = "application/json"

    body, content_type = EF.safe_fetch(
        f"http://localhost:{port}/data.json", allowed_domains={"localhost"},
        timeout_s=2.0, max_bytes=1024,
    )

    assert body == b'{"ok": true}'
    assert content_type == "application/json"
    assert handler_cls.hits == 1


def test_safe_fetch_enforces_the_response_size_cap(local_server, guard_lifted):
    server, handler_cls = local_server
    port = server.server_address[1]
    handler_cls.body = b"x" * 10_000

    with pytest.raises(EF.ExternalFetchError, match="byte cap"):
        EF.safe_fetch(
            f"http://127.0.0.1:{port}/big", allowed_domains={"127.0.0.1"},
            timeout_s=2.0, max_bytes=100,
        )


def test_safe_fetch_does_not_follow_redirects(local_server, guard_lifted):
    server, handler_cls = local_server
    port = server.server_address[1]
    handler_cls.redirect_to = "http://127.0.0.1:9/elsewhere"

    with pytest.raises(EF.ExternalFetchError, match="redirect"):
        EF.safe_fetch(
            f"http://127.0.0.1:{port}/start", allowed_domains={"127.0.0.1"},
            timeout_s=2.0, max_bytes=1024,
        )


def test_safe_fetch_times_out_rather_than_hanging(local_server, guard_lifted):
    server, handler_cls = local_server
    port = server.server_address[1]
    handler_cls.delay_s = 1.0

    with pytest.raises(EF.ExternalFetchError, match="timed out"):
        EF.safe_fetch(
            f"http://127.0.0.1:{port}/slow", allowed_domains={"127.0.0.1"},
            timeout_s=0.2, max_bytes=1024,
        )


def test_safe_fetch_enforces_a_total_wall_clock_budget_against_a_slow_trickle(
    local_server, guard_lifted
):
    """httpx's own read timeout only bounds the GAP between two reads - a
    server that drip-feeds a few bytes at a time, each gap comfortably under
    that per-operation timeout, could otherwise run far longer in total than
    the caller's intended budget. 10 chunks x 0.1s gaps = ~1.0s of real
    elapsed time, while each individual gap (0.1s) stays well under the 0.5s
    per-operation timeout below - only an explicit wall-clock deadline check
    (not the per-op timeout) can catch this."""
    server, handler_cls = local_server
    port = server.server_address[1]
    handler_cls.trickle_chunks = 10
    handler_cls.trickle_chunk_size = 8
    handler_cls.trickle_delay_s = 0.1

    with pytest.raises(EF.ExternalFetchError, match="total timeout"):
        EF.safe_fetch(
            f"http://127.0.0.1:{port}/trickle", allowed_domains={"127.0.0.1"},
            timeout_s=0.5, max_bytes=1_000_000,
        )


def test_safe_fetch_uses_a_separate_shorter_connect_timeout(local_server, guard_lifted):
    """`connect_timeout_s` bounds only the handshake, independent of the
    (longer) total request budget - proven by a server that answers
    immediately (so connect is fast) but trickles the body long enough to
    prove the generous `timeout_s` is the one actually governing the body
    read, not the short connect timeout wrongly capping the whole request."""
    server, handler_cls = local_server
    port = server.server_address[1]
    handler_cls.trickle_chunks = 3
    handler_cls.trickle_chunk_size = 4
    handler_cls.trickle_delay_s = 0.2

    body, _content_type = EF.safe_fetch(
        f"http://127.0.0.1:{port}/trickle", allowed_domains={"127.0.0.1"},
        timeout_s=5.0, connect_timeout_s=0.05, max_bytes=1024,
    )
    assert body == b"x" * 12


# --------------------------------------------------------------- real DNS rebinding, end-to-end


def test_dns_pinning_survives_a_real_mid_flight_rebind(guard_lifted, monkeypatch):
    """The harder, real version of the rebinding scenario - not just calling
    `_pin_dns` directly (see `external_fetch.demo()`), but running the actual
    `safe_fetch()` end-to-end against a hostname that GENUINELY answers
    differently between the pre-connect safety check and a later, independent
    lookup - exactly what a short-TTL DNS-rebinding record does in the wild.

    Two real local HTTP servers stand in for "the address validated at
    check-time" (127.0.0.1) and "where a rebind would redirect the
    connection" (127.0.0.2 - also loopback, so still routable in this
    sandbox; `guard_lifted` is what lets loopback stand in for a public
    address here, exactly as in the tests above - this test is isolating the
    DNS-pinning/TOCTOU mechanism specifically, not the private-IP filter,
    which is covered separately). The fake resolver answers with the FIRST
    server's IP once, then the SECOND server's IP forever after - simulating
    the DNS record actually rebinding mid-flight."""
    host = "rebind-e2e-demo.invalid"

    safe_handler = type("SafeHandler", (_CountingHandler,), {"hits": 0, "body": b"SAFE"})
    safe_server = http.server.HTTPServer(("127.0.0.1", 0), safe_handler)
    port = safe_server.server_address[1]
    safe_thread = threading.Thread(target=safe_server.serve_forever, daemon=True)
    safe_thread.start()

    rebind_handler = type("RebindHandler", (_CountingHandler,), {"hits": 0, "body": b"REBOUND"})
    rebind_server = http.server.HTTPServer(("127.0.0.2", port), rebind_handler)
    rebind_thread = threading.Thread(target=rebind_server.serve_forever, daemon=True)
    rebind_thread.start()

    try:
        original_real_getaddrinfo = EF._real_getaddrinfo
        call_count = {"n": 0}

        def flipping_getaddrinfo(h, p, *a, **kw):
            if h == host:
                call_count["n"] += 1
                ip = "127.0.0.1" if call_count["n"] == 1 else "127.0.0.2"
                return [(2, 1, 6, "", (ip, p or 0))]  # AF_INET, SOCK_STREAM, IPPROTO_TCP
            return original_real_getaddrinfo(h, p, *a, **kw)

        monkeypatch.setattr(EF, "_real_getaddrinfo", flipping_getaddrinfo)

        body, _content_type = EF.safe_fetch(
            f"http://{host}:{port}/", allowed_domains={host}, timeout_s=2.0, max_bytes=1024,
        )

        # Prove the rebind was real, not assumed: an independent lookup for
        # this exact host, made right after `safe_fetch` returned, now
        # genuinely answers with the OTHER address.
        second_lookup_ip = socket.getaddrinfo(host, port)[0][4][0]
        assert second_lookup_ip == "127.0.0.2", "test invalid - no real rebind occurred"
        assert call_count["n"] >= 2

        # The actual outcome that matters: despite the real mid-flight
        # rebind above, the pinned connection stayed on the checked address.
        assert body == b"SAFE"
        assert safe_handler.hits == 1
        assert rebind_handler.hits == 0, "pinning failed - the rebind target was actually contacted"
    finally:
        safe_server.shutdown()
        rebind_server.shutdown()
        safe_thread.join(timeout=2)
        rebind_thread.join(timeout=2)
