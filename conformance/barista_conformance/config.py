"""Provider endpoint and credential configuration.

Configuration selects *which* provider to test and how to authenticate. It is
the only thing that differs between running against a local provider and running
against Barista Cloud — the cases themselves never branch on provider name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DelegatedProbe:
    """Credentials and handles for the child-session authority cases.

    Host API ``v1alpha1`` has **no endpoint that hands a delegated grant to a
    client**. A child's credential is minted by the provider and delivered into
    the child session (a ``grant://`` secret reference resolved into its
    environment), so a black-box suite running *outside* any session cannot
    obtain one through the published contract. Nothing here is a private
    provider hook: the operator installs an app that declares child authority,
    lets the provider create a coordinator and a worker from it, and hands the
    suite the two credentials the provider minted. Absent this, the cases skip
    with that reason rather than pass vacuously.

    The app named here MUST declare, in its manifest:
      * ``permissions.actions`` containing ``scoped_action`` over
        ``created_sessions``, and ``session.create``;
      * ``permissions.child_sessions.actions`` **without** ``scoped_action``;
      * ``permissions.child_sessions.allow_descendants`` absent or false.

    ``contracts/app-manifest/v1alpha1/examples/factory.json`` is exactly such a
    manifest, and is what the suite installs for the manifest-level cases.
    """

    app: str
    """Installed app name the coordinator and worker sessions were created from."""

    coordinator_token: str
    """The grant the provider minted for the coordinator session."""

    coordinator_session_id: str

    worker_token: str
    """The grant the provider minted for a session the coordinator created."""

    worker_session_id: str

    scoped_action: str = "session.get"
    """An action the coordinator holds over its created sessions and the worker
    was NOT given. Read-only by design: these cases must not destroy the
    sessions they are handed."""

    foreign_session_id: Optional[str] = None
    """A live session the coordinator did NOT create. Without it, the
    'authority stops at its own children' case cannot be proven and skips."""

    @classmethod
    def from_env(cls) -> Optional["DelegatedProbe"]:
        required = {
            "app": "BARISTA_CONFORMANCE_DELEGATED_APP",
            "coordinator_token": "BARISTA_CONFORMANCE_COORDINATOR_TOKEN",
            "coordinator_session_id": "BARISTA_CONFORMANCE_COORDINATOR_SESSION",
            "worker_token": "BARISTA_CONFORMANCE_WORKER_TOKEN",
            "worker_session_id": "BARISTA_CONFORMANCE_WORKER_SESSION",
        }
        values = {field: os.environ.get(var) for field, var in required.items()}
        if not all(values.values()):
            return None
        return cls(
            **values,
            scoped_action=os.environ.get("BARISTA_CONFORMANCE_SCOPED_ACTION", "session.get"),
            foreign_session_id=os.environ.get("BARISTA_CONFORMANCE_FOREIGN_SESSION"),
        )


@dataclass
class ProviderConfig:
    """How to reach and authenticate to a Host API provider."""

    endpoint: str
    """Base URL, e.g. http://localhost:8088 or https://api.barista.sh."""

    token: Optional[str] = None
    """Bearer credential, if the provider requires one."""

    token_env: Optional[str] = None
    """Name of an env var to read the token from (avoids passing secrets on argv)."""

    provider_name: str = "unknown"
    provider_version: str = "unknown"

    standalone: bool = False
    """Run under the mandatory Cloud-absent harness (blocks Cloud DNS/endpoints,
    fails on proprietary imports or network attempts)."""

    cloud_hosts: tuple[str, ...] = field(
        default=(
            "barista.sh",
            "beta.barista.sh",
            "api.barista.sh",
            "cloud.barista.sh",
        )
    )
    """Host suffixes treated as Barista Cloud for the standalone harness."""

    proprietary_modules: tuple[str, ...] = field(default=("barista_cloud",))
    """Import names that must not be reachable in a standalone run."""

    timeout_seconds: float = 30.0

    delegated_probe: Optional[DelegatedProbe] = None
    """Operator-supplied delegated credentials; see DelegatedProbe."""

    def resolved_token(self) -> Optional[str]:
        if self.token:
            return self.token
        if self.token_env:
            return os.environ.get(self.token_env)
        return None

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        endpoint = os.environ.get("BARISTA_HOST_API_ENDPOINT")
        if not endpoint:
            raise ValueError(
                "BARISTA_HOST_API_ENDPOINT is required "
                "(e.g. http://localhost:8088)."
            )
        return cls(
            endpoint=endpoint.rstrip("/"),
            token=os.environ.get("BARISTA_HOST_API_TOKEN"),
            token_env=None,
            provider_name=os.environ.get("BARISTA_PROVIDER_NAME", "unknown"),
            provider_version=os.environ.get("BARISTA_PROVIDER_VERSION", "unknown"),
            standalone=os.environ.get("BARISTA_CONFORMANCE_STANDALONE", "") == "1",
            delegated_probe=DelegatedProbe.from_env(),
        )
