"""Production Node backend: a gRPC client for the Barista Node Agent (Contract A).

Requires the `grpc` extra (grpcio + the generated `barista-proto` package) and a
reachable Node Agent — typically a loopback TCP address or a Unix socket owned
by the local user. Imports are lazy so the provider and its offline tests do not
need the kernel's generated stubs installed.

Async lifecycle RPCs return an Operation; this client polls GetOperation to
completion and raises NodeUnsupported on a capability/spec refusal.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from .client import (
    ExecResult,
    InstanceRequest,
    NodeCapabilities,
    NodeInstance,
    NodeNotFound,
    NodeUnsupported,
)

# Host API session states keyed by Contract A InstanceState enum name.
_STATE = {
    "INSTANCE_STATE_CREATING": "creating",
    "INSTANCE_STATE_CREATED": "creating",
    "INSTANCE_STATE_STARTING": "creating",
    "INSTANCE_STATE_RUNNING": "running",
    "INSTANCE_STATE_CHECKPOINTING": "running",
    "INSTANCE_STATE_PAUSING": "paused",
    "INSTANCE_STATE_PAUSED": "paused",
    "INSTANCE_STATE_RESUMING": "paused",
    "INSTANCE_STATE_STOPPING": "stopped",
    "INSTANCE_STATE_STOPPED": "stopped",
    "INSTANCE_STATE_DESTROYING": "stopped",
    "INSTANCE_STATE_DESTROYED": "stopped",
    "INSTANCE_STATE_FAILED": "error",
}


class GrpcNodeClient:
    def __init__(self, target: str, *, poll_interval: float = 0.05, poll_timeout: float = 60.0):
        import grpc  # lazy
        from barista.node.v1alpha1 import node_pb2, node_pb2_grpc  # lazy

        self._pb = node_pb2
        self._grpc = grpc
        self._channel = grpc.insecure_channel(target)
        self._stub = node_pb2_grpc.NodeAgentStub(self._channel)
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    # -- helpers ---------------------------------------------------------- #
    def _await_op(self, op) -> None:
        pb = self._pb
        deadline = time.time() + self._poll_timeout
        while op.state not in (pb.OPERATION_STATE_DONE, pb.OPERATION_STATE_FAILED):
            if time.time() > deadline:
                raise TimeoutError(f"operation {op.op_id} did not finish")
            time.sleep(self._poll_interval)
            op = self._stub.GetOperation(pb.GetOperationRequest(op_id=op.op_id))
        if op.state == pb.OPERATION_STATE_FAILED:
            reason = pb.ErrorReason.Name(op.error.reason)
            if reason in ("ERROR_REASON_CAPABILITY_MISSING", "ERROR_REASON_INVALID_SPEC"):
                raise NodeUnsupported(f"{reason}: {op.error.message}")
            raise RuntimeError(f"node operation failed: {reason}: {op.error.message}")

    def _state(self, instance) -> str:
        name = self._pb.InstanceState.Name(instance.state)
        return _STATE.get(name, "error")

    # -- NodeClient ------------------------------------------------------- #
    def node_info(self) -> NodeCapabilities:
        info = self._stub.GetNodeInfo(self._pb.GetNodeInfoRequest())
        caps = NodeCapabilities()
        if info.runtimes:
            c = info.runtimes[0].capabilities
            caps = NodeCapabilities(
                pause_resume=True,  # every runtime supports at least stop/start
                memory_snapshot=c.memory_snapshot,
                disk_snapshot=c.disk_snapshot,
                cow_fork=c.cow_fork,
                guest_agent=c.guest_agent,
            )
        return caps

    def create_and_start(self, request: InstanceRequest) -> NodeInstance:
        pb = self._pb
        spec = pb.InstanceSpec(
            instance_id=request.instance_id,
            template=pb.TemplateRef(
                oci=pb.OciImageRef(image=request.image, digest=request.digest),
                arch=request.arch,
            ),
            process=pb.Process(start_cmd=request.start_cmd, env=request.env, workdir=request.workdir or ""),
        )
        create = self._stub.CreateInstance(
            pb.CreateInstanceRequest(spec=spec, idempotency_key=request.instance_id + ":create")
        )
        self._await_op(create)
        start = self._stub.StartInstance(
            pb.StartInstanceRequest(instance_id=request.instance_id, idempotency_key=request.instance_id + ":start")
        )
        self._await_op(start)
        got = self.get(request.instance_id)
        if got is None:
            raise NodeNotFound(request.instance_id)
        return got

    def get(self, instance_id: str) -> Optional[NodeInstance]:
        try:
            instance = self._stub.GetInstance(self._pb.GetInstanceRequest(instance_id=instance_id))
        except self._grpc.RpcError as exc:  # type: ignore[attr-defined]
            if exc.code() == self._grpc.StatusCode.NOT_FOUND:
                return None
            raise
        return NodeInstance(instance_id=instance_id, state=self._state(instance), ready=instance.ready)

    def destroy(self, instance_id: str) -> None:
        op = self._stub.DestroyInstance(
            self._pb.DestroyInstanceRequest(instance_id=instance_id, idempotency_key=instance_id + ":destroy")
        )
        self._await_op(op)

    def pause(self, instance_id: str) -> None:
        op = self._stub.PauseInstance(
            self._pb.PauseInstanceRequest(instance_id=instance_id, idempotency_key=instance_id + ":pause")
        )
        self._await_op(op)

    def resume(self, instance_id: str) -> None:
        op = self._stub.ResumeInstance(
            self._pb.ResumeInstanceRequest(instance_id=instance_id, idempotency_key=instance_id + ":resume")
        )
        self._await_op(op)

    def exec(
        self,
        instance_id: str,
        command: list[str],
        env: Optional[dict[str, str]] = None,
        workdir: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ExecResult:
        pb = self._pb

        def outbound():
            yield pb.ExecFrame(
                start=pb.ExecStart(
                    instance_id=instance_id, cmd=command, env=env or {}, workdir=workdir or ""
                )
            )

        stdout, stderr, code = bytearray(), bytearray(), 0
        for frame in self._stub.Exec(outbound()):
            which = frame.WhichOneof("frame")
            if which == "stdout":
                stdout += frame.stdout
            elif which == "stderr":
                stderr += frame.stderr
            elif which == "exit":
                code = frame.exit.code
        return ExecResult(exit_code=code, stdout=bytes(stdout), stderr=bytes(stderr))

    def close(self) -> None:
        self._channel.close()
