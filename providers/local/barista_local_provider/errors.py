"""Standard Host API error responses."""

from __future__ import annotations

from starlette.responses import JSONResponse


def error(status: int, cls: str, code: str, message: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        {"class": cls, "code": code, "message": message, "retryable": retryable},
        status_code=status,
    )


def not_found(message: str = "not found") -> JSONResponse:
    return error(404, "terminal", "not_found", message)


def capability_unsupported(message: str) -> JSONResponse:
    return error(501, "capability", "capability.unsupported", message)


def invalid_request(message: str, code: str = "invalid_request") -> JSONResponse:
    return error(422, "invalid_request", code, message)
