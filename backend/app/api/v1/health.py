"""
Health probes are wired in main.create_app() via a small router that reads the live
DB pool from app.state at call time (so /readyz checks the SAME pooled connection the
app serves from, not a throwaway). This module holds the shared version constant.

Kubernetes model:
  * GET /livez   - process alive? (no dependencies) -> restart signal
  * GET /readyz  - can it serve? (checks DB pool)    -> remove from rotation on 503
  * GET /healthz - human/dashboard summary
Failures never leak internals; detail goes to structured logs.
"""
from __future__ import annotations

VERSION = "1.0.0"
