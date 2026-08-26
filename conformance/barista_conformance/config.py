"""Provider endpoint and credential configuration.

Configuration selects *which* provider to test and how to authenticate. It is
the only thing that differs between running against a local provider and running
against Barista Cloud — the cases themselves never branch on provider name.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProbeWorkload:
    """The workload the suite installs when a case needs a session that *runs*.

    This is deliberately not the contract's ``examples/minimal.json``. That file
    documents the manifest shape, and its digest is a readable placeholder — so a
    provider that genuinely resolves images (any VMM-backed one) can never boot
    it, while a provider that fakes the workload passes. Reusing a documentation
    fixture as a runnable workload therefore made the core cases *easier* to pass
    the less real the provider was, which inverts what conformance is for.

    The default is a small public multi-arch image pinned by its index digest, so
    the suite works out of the box against a provider with registry egress. A
    provider that pulls from somewhere else — an air-gapped fleet, a private
    mirror, a loopback registry — supplies its own; nothing here assumes any
    particular registry is reachable.

    The entrypoint only has to stay alive: these cases exec into the session and
    pause/resume it, so a workload that exits immediately is not a session.
    """

    image: str = "docker.io/library/alpine:3.20"
    digest: str = "sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
    architectures: tuple[str, ...] = ("aarch64", "x86_64")
    entrypoint: tuple[str, ...] = ("/bin/sh", "-c", "sleep infinity")

    @classmethod
    def from_env(cls) -> "ProbeWorkload":
        d = cls()
        arches = os.environ.get("BARISTA_CONFORMANCE_PROBE_ARCHITECTURES")
        entry = os.environ.get("BARISTA_CONFORMANCE_PROBE_ENTRYPOINT")
        return cls(
            image=os.environ.get("BARISTA_CONFORMANCE_PROBE_IMAGE") or d.image,
            digest=os.environ.get("BARISTA_CONFORMANCE_PROBE_DIGEST") or d.digest,
            architectures=tuple(a.strip() for a in arches.split(",") if a.strip())
            if arches
            else d.architectures,
            # shlex, not split(): an entrypoint argument can contain spaces, and
            # the default ("sleep infinity" as one -c argument) is itself an
            # example of that.
            entrypoint=tuple(shlex.split(entry)) if entry else d.entrypoint,
        )

    def manifest(self, name: str = "conformance-probe") -> dict:
        return {
            "schema_version": "v1alpha1",
            "name": name,
            "version": "0.1.0",
            "workload": {
                "image": self.image,
                "digest": self.digest,
                "architectures": list(self.architectures),
                "entrypoint": list(self.entrypoint),
                "working_dir": "/work",
                "readiness": {"type": "none"},
            },
        }


@dataclass
class DelegatedProbe:
    """Credentials and handles for the child-session authority cases.

    A child's credential is minted by the provider and delivered into the child
    session (a ``grant://`` secret reference resolved into its environment).
    Supplying it here is still supported and still takes precedence — an
    operator installs an app that declares child authority, lets the provider
    create a coordinator and a worker from it, and hands the suite the two
    credentials the provider minted.

    Since apps-003 it is no longer the *only* way. A provider that advertises
    ``grants.delegated`` offers grant refresh, which is the contract's only
    positive proof that a client holds a live delegated grant — so the suite can
    stand up its own probe sessions, read the credential the provider resolved
    into them, confirm it by refreshing it, and run the cases unattended. See
    ``AcquiredDelegation``. Absent both, the cases skip with that reason rather
    than pass vacuously.

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
class AcquiredDelegation:
    """What the suite obtained for *itself*, and how. Filled in at run time.

    apps-002 could only take delegated credentials from an operator, because no
    client could obtain one and nothing could confirm one. Grant refresh changes
    both halves: the credential a provider resolves into a probe session is
    readable through the published contract (exec + events, under the env var
    name the manifest declares), and refresh accepts a live delegated grant while
    refusing anything that is not one. So the suite creates a sacrificial
    coordinator, lets the provider mint a child beneath it, confirms both
    credentials by refreshing them, and runs the delegation cases unattended.

    The probe sessions are sacrificial by design: refreshing their grants rotates
    the secret their own workload was given, so they are named ``conf-probe-*``
    and deleted when the run ends. ``sessions`` is that cleanup list.
    """

    probe: Optional["DelegatedProbe"]
    reason: str
    """Why the suite does or does not hold credentials — reported either way."""
    sessions: list[str] = field(default_factory=list)


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

    probe_workload: ProbeWorkload = field(default_factory=ProbeWorkload)
    """The workload installed by cases that need a running session; see
    ProbeWorkload for why this is not the contract's documentation example."""

    grant_env_var: Optional[str] = None
    """Env var a delegated grant is resolved into inside a session. Left None,
    the suite reads the name from the manifest it installs — which is where a
    portable app gets it from too. Set it only for a provider that delivers the
    credential under a different name than the manifest declares."""

    unbound_grant: Optional[str] = None
    """A delegated grant bound to no session, if the provider can mint one.

    Refresh must refuse it: the session is what ends a refresh chain, so an
    unbound grant would renew past any maximum-lifetime ceiling in steps that
    never individually exceed it — and a ceiling exists to force a re-issue,
    which is a re-decision. A black-box client cannot produce such a grant (every
    credential it can obtain arrives inside a session), so this is supplied the
    way operator credentials are, and the assertion is made only when it is.
    """

    expiry_wait_seconds: float = 30.0
    """How long the suite is willing to wait for a grant to expire in order to
    prove that an expired grant cannot be refreshed. Expiry is the one half of
    that requirement no request can produce — it happens by the clock — so a
    provider whose grants outlive this budget leaves the case unproven and the
    profile uncertified. Run against a short-lifetime tenant, or raise this."""

    acquired: Optional[AcquiredDelegation] = None
    """Filled in by the suite at run time; see AcquiredDelegation."""

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
            probe_workload=ProbeWorkload.from_env(),
            grant_env_var=os.environ.get("BARISTA_CONFORMANCE_GRANT_ENV") or None,
            unbound_grant=os.environ.get("BARISTA_CONFORMANCE_UNBOUND_GRANT") or None,
            expiry_wait_seconds=float(
                os.environ.get("BARISTA_CONFORMANCE_EXPIRY_WAIT_SECONDS", "30")
            ),
        )
