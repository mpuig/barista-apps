"""Thin Host API client used by the conformance suite.

Deliberately minimal: it speaks the published wire contract and exposes the
raw ``httpx.Response`` where cases need status codes and headers. The transport
is injectable so the suite can self-test against an in-process mock without a
running server.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator, Optional

import httpx

BASE = "/v1alpha1"
MANIFEST_MEDIA_TYPE = "application/vnd.barista.app-manifest.v1alpha1+json"


def new_idempotency_key() -> str:
    return "idem-" + uuid.uuid4().hex


class HostAPIClient:
    def __init__(
        self,
        endpoint: str,
        token: Optional[str] = None,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 30.0,
    ):
        headers = {"accept": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=endpoint,
            headers=headers,
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HostAPIClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def _idem(key: Optional[str]) -> dict[str, str]:
        return {"Idempotency-Key": key} if key else {}

    # -- discovery -------------------------------------------------------- #
    def discovery(self) -> httpx.Response:
        return self._client.get(f"{BASE}/discovery")

    # -- apps ------------------------------------------------------------- #
    def install_app(self, manifest: dict, key: Optional[str] = None) -> httpx.Response:
        return self._client.post(
            f"{BASE}/apps",
            content=json.dumps(manifest),
            headers={"content-type": MANIFEST_MEDIA_TYPE, **self._idem(key)},
        )

    # -- sessions --------------------------------------------------------- #
    def ensure_session(self, body: dict, key: Optional[str] = None) -> httpx.Response:
        return self._client.post(f"{BASE}/sessions", json=body, headers=self._idem(key))

    def get_session(self, session_id: str) -> httpx.Response:
        return self._client.get(f"{BASE}/sessions/{session_id}")

    def list_sessions(self, **params: Any) -> httpx.Response:
        return self._client.get(f"{BASE}/sessions", params=params or None)

    def delete_session(self, session_id: str, key: Optional[str] = None) -> httpx.Response:
        return self._client.delete(f"{BASE}/sessions/{session_id}", headers=self._idem(key))

    def pause(self, session_id: str, key: Optional[str] = None) -> httpx.Response:
        return self._client.post(f"{BASE}/sessions/{session_id}/pause", headers=self._idem(key))

    def resume(self, session_id: str, key: Optional[str] = None) -> httpx.Response:
        return self._client.post(f"{BASE}/sessions/{session_id}/resume", headers=self._idem(key))

    def exec(self, session_id: str, body: dict, key: Optional[str] = None) -> httpx.Response:
        return self._client.post(
            f"{BASE}/sessions/{session_id}/exec", json=body, headers=self._idem(key)
        )

    def attach(self, session_id: str, mode: str = "raw") -> httpx.Response:
        return self._client.get(f"{BASE}/sessions/{session_id}/attach", params={"mode": mode})

    # -- artifacts -------------------------------------------------------- #
    def register_artifact(self, session_id: str, body: dict, key: Optional[str] = None) -> httpx.Response:
        return self._client.post(
            f"{BASE}/sessions/{session_id}/artifacts", json=body, headers=self._idem(key)
        )

    def list_artifacts(self, session_id: str) -> httpx.Response:
        return self._client.get(f"{BASE}/sessions/{session_id}/artifacts")

    # -- operations ------------------------------------------------------- #
    def get_operation(self, operation_id: str) -> httpx.Response:
        return self._client.get(f"{BASE}/operations/{operation_id}")

    # -- events (SSE) ----------------------------------------------------- #
    def events(
        self,
        session_id: str,
        cursor: Optional[str] = None,
        *,
        max_events: int = 100,
    ) -> Iterator[dict]:
        """Yield parsed SSE event payloads. Resumes from ``cursor`` via the
        Last-Event-ID header, per the contract."""
        headers = {"accept": "text/event-stream"}
        if cursor:
            headers["Last-Event-ID"] = cursor
        params = {"cursor": cursor} if cursor else None
        count = 0
        with self._client.stream(
            "GET", f"{BASE}/sessions/{session_id}/events", params=params, headers=headers
        ) as resp:
            resp.raise_for_status()
            data_lines: list[str] = []
            for line in resp.iter_lines():
                if line == "":
                    if data_lines:
                        yield json.loads("\n".join(data_lines))
                        data_lines = []
                        count += 1
                        if count >= max_events:
                            return
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[len("data:") :].lstrip())
