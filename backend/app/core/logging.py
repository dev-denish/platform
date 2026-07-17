"""
Structured logging.

Existing implementation (MVP): none. No logger anywhere. Every failure was
invisible and undiagnosable in production.

Enterprise solution: structlog emitting JSON (one event per line, ingestible by
CloudWatch / Loki / ELK), with request_id and correlation_id bound per request via
contextvars so every log line inside a request is automatically correlated. Human
console rendering in dev, JSON in prod.
"""
from __future__ import annotations

import contextvars
import logging
import sys

import structlog

# Bound once per request by RequestContextMiddleware; read by the processor below.
request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
correlation_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def _inject_context(_logger, _method, event_dict):
    rid = request_id_ctx.get()
    cid = correlation_id_ctx.get()
    if rid:
        event_dict["request_id"] = rid
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=level.upper()
    )

    shared = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "dmrv") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
