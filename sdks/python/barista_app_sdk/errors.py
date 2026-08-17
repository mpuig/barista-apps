"""Typed Host API errors.

Maps the contract's error classes to a Python hierarchy so callers can branch on
type. ``retryable`` marks the transient class the SDK may retry with the same
idempotency key; authorization, capability, compatibility, terminal, and
invalid-request errors are never retried as transient.
"""

from __future__ import annotations

from typing import Any, Optional


class HostAPIError(Exception):
    retryable = False

    def __init__(self, message: str, *, code: str = "", status: int = 0,
                 details: Optional[dict] = None, error_class: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}
        self.error_class = error_class


class AuthenticationError(HostAPIError):
    pass


class AuthorizationError(HostAPIError):
    pass


class CapabilityError(HostAPIError):
    pass


class CompatibilityError(HostAPIError):
    pass


class ConflictError(HostAPIError):
    pass


class QuotaError(HostAPIError):
    pass


class UnavailableError(HostAPIError):
    retryable = True


class TerminalError(HostAPIError):
    pass


class InvalidRequestError(HostAPIError):
    pass


_CLASS_MAP = {
    "authentication": AuthenticationError,
    "authorization": AuthorizationError,
    "capability": CapabilityError,
    "compatibility": CompatibilityError,
    "conflict": ConflictError,
    "quota": QuotaError,
    "unavailable": UnavailableError,
    "terminal": TerminalError,
    "invalid_request": InvalidRequestError,
}


def from_response(status: int, body: Any) -> HostAPIError:
    if isinstance(body, dict) and "class" in body:
        cls = _CLASS_MAP.get(body["class"], HostAPIError)
        err = cls(
            body.get("message", "request failed"),
            code=body.get("code", ""),
            status=status,
            details=body.get("details"),
            error_class=body["class"],
        )
        if "retryable" in body:
            # Honor an explicit provider retryable hint over the class default.
            err.retryable = bool(body["retryable"]) or cls.retryable
        return err
    return HostAPIError(f"HTTP {status}", status=status)
