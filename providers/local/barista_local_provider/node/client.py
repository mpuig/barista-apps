"""The Node backend port.

The local provider maps the Host API onto a Barista Node Agent (Contract A)
through this narrow interface. A real gRPC client and an in-memory fake both
implement it; the provider logic never depends on which is behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class NodeCapabilities:
    """What the underlying node/runtime can actually do. The provider translates
    these into Host API profiles — it never advertises more than this reports."""

    pause_resume: bool = False
    memory_snapshot: bool = False
    disk_snapshot: bool = True
    cow_fork: bool = False
    guest_agent: bool = True


@dataclass
class NodeInstance:
    instance_id: str
    state: str  # a Host API session state: creating|running|paused|stopped|error
    ready: bool = True


@dataclass
class ExecResult:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass
class InstanceRequest:
    instance_id: str
    image: str
    digest: str
    arch: str
    start_cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)
    workdir: Optional[str] = None


class NodeUnsupported(RuntimeError):
    """The node cannot perform the requested operation (maps to a capability error)."""


class NodeNotFound(KeyError):
    """No such instance on the node."""


class NodeClient(Protocol):
    def node_info(self) -> NodeCapabilities: ...

    def create_and_start(self, request: InstanceRequest) -> NodeInstance: ...

    def get(self, instance_id: str) -> Optional[NodeInstance]: ...

    def destroy(self, instance_id: str) -> None: ...

    def pause(self, instance_id: str) -> None: ...

    def resume(self, instance_id: str) -> None: ...

    def exec(
        self,
        instance_id: str,
        command: list[str],
        env: Optional[dict[str, str]] = None,
        workdir: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ExecResult: ...

    def close(self) -> None: ...
