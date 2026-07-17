"""
Domain exception hierarchy.

Existing implementation (MVP): business logic raised `fastapi.HTTPException`
directly and leaked raw exception strings to clients
(`detail=f"ingestion failed: {e}"`), which discloses internals.

Enterprise solution: the service/domain layers raise *domain* exceptions that
carry intent (NotFound, Conflict, Forbidden, Validation, Unprocessable) with a
safe, client-facing message. A single global handler (see api layer) maps them to
HTTP status codes and logs the full detail server-side. Business logic never
imports FastAPI, and internal error text never reaches the client.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base class. `message` is safe to show a client; `status_code` is the map."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message or "An unexpected error occurred."


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


class AuthError(DomainError):
    status_code = 401
    code = "unauthorized"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class UnprocessableError(DomainError):
    status_code = 422
    code = "unprocessable"


class PayloadTooLargeError(DomainError):
    status_code = 413
    code = "payload_too_large"
