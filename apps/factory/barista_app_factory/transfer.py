"""Moving bytes in and out of a session, over the portable Host API only.

The Host API has no file-transfer verb, and it should not grow one for Factory's
sake: `exec` plus the event stream is enough, and staying inside the operations
every conformant provider already implements is what keeps Factory an ordinary
portable app rather than one with a provider-shaped dependency.

**Out** is `cat`, read back from the session's event journal — `exec.stdout`
events carry base64 chunks, which is the provider's own encoding, not one
invented here.

**In** is base64 on the command line. `exec` has no stdin, so the content travels
as an argv element and is decoded inside the session. base64's alphabet is
`A-Za-z0-9+/=`, none of which the shell treats specially, so this cannot be
broken by content that happens to contain a quote, a newline, or a `$(` — the
failure mode a heredoc or an interpolated string would have.

The bound worth knowing: content goes through an argv element, so it is subject
to the platform's argument-length limit (typically ~2 MB, well under `ARG_MAX`
but not unlimited). Planting a criterion — a test, a spec, a fixture — is
comfortably inside that. Shipping a tarball is not what this is for.
"""

from __future__ import annotations

import base64
import hashlib
import posixpath

from barista_app_sdk import BaristaClient

# Long enough for a slow VM to start a shell and decode; short enough that a
# hung write does not consume a task's whole timeout budget before anyone hears.
_TRANSFER_TIMEOUT_S = 120


class TransferError(RuntimeError):
    """A plant or capture did not complete. Never silently partial: a caller that
    believes a criterion was planted when it was not would run a check against
    whatever the worker left there, which is the exact failure this module exists
    to prevent."""


def write_file(client: BaristaClient, session_id: str, path: str, content: str | bytes) -> str:
    """Place `content` at `path` inside the session. Returns its sha256 digest.

    Idempotent by nature: writing identical bytes to the same path twice leaves
    the same file, so re-planting after a restart is free and needs no bookkeeping.
    """
    raw = content.encode() if isinstance(content, str) else content
    encoded = base64.b64encode(raw).decode()
    parent = posixpath.dirname(path) or "/"
    # `set -e` so a failed mkdir cannot leave the redirect creating a file in the
    # wrong place and reporting success.
    script = f"set -e; mkdir -p '{parent}'; printf %s {encoded} | base64 -d > '{path}'"
    handle = client.exec(session_id, ["sh", "-c", script], timeout_seconds=_TRANSFER_TIMEOUT_S)
    op = client.wait_operation(handle.operation_id, timeout=_TRANSFER_TIMEOUT_S)
    exit_code = (op.result or {}).get("exit_code", 1)
    if not op.done or exit_code != 0:
        raise TransferError(f"could not write {path} into {session_id} (exit {exit_code})")
    return _digest(raw)


def read_file(client: BaristaClient, session_id: str, path: str) -> bytes:
    """Read `path` out of the session.

    Raises `TransferError` when the file is not there. A missing produced output
    must not read as empty content: an empty spec handed to the next stage is a
    stage that runs on nothing and reports success.
    """
    handle = client.exec(
        session_id, ["cat", path], timeout_seconds=_TRANSFER_TIMEOUT_S
    )
    op = client.wait_operation(handle.operation_id, timeout=_TRANSFER_TIMEOUT_S)
    exit_code = (op.result or {}).get("exit_code", 1)
    if not op.done or exit_code != 0:
        raise TransferError(f"could not read {path} from {session_id} (exit {exit_code})")
    chunks: list[bytes] = []
    for event in client.events(session_id, cursor=handle.event_cursor):
        if event.type == "exec.stdout":
            chunk = event.data.get("chunk")
            if chunk:
                chunks.append(base64.b64decode(chunk))
        elif event.type == "exec.exit":
            # The journal is a tail: without stopping at this exec's own exit we
            # would block waiting for events that belong to nobody.
            break
    return b"".join(chunks)


def read_file_bounded(
    client: BaristaClient,
    session_id: str,
    path: str,
    *,
    max_bytes: int,
    chunk_bytes: int = 256 * 1024,
) -> bytes:
    """Capture a bounded file completely, independent of event-page limits.

    One large `cat` can exceed a provider's maximum event page and look like a
    valid truncated file. Read a small worker-computed size/digest receipt, then
    capture fixed-size ranges through separate operation cursors and verify the
    assembled bytes before returning them.
    """
    if max_bytes <= 0 or chunk_bytes <= 0:
        raise ValueError("transfer bounds must be positive")
    if not path.startswith("/") or "'" in path or "\n" in path or "\r" in path:
        raise ValueError("bounded transfer path must be a safe absolute path")
    # `path` is selected by the coordinator, never by objective content. Keep it
    # in argv where possible; this fixed metadata script only redirects it.
    metadata = _capture_exec(
        client,
        session_id,
        ["sh", "-c", f"set -e; wc -c < '{path}'; sha256sum '{path}'"],
    )
    try:
        lines = metadata.decode("ascii").splitlines()
        size = int(lines[0].strip())
        expected = lines[1].split()[0]
    except (UnicodeDecodeError, ValueError, IndexError) as exc:
        raise TransferError(f"could not validate {path} metadata from {session_id}") from exc
    if size < 0 or size > max_bytes:
        raise TransferError(
            f"{path} from {session_id} is {size} bytes (limit {max_bytes})"
        )
    chunks: list[bytes] = []
    for offset in range(0, size, chunk_bytes):
        index = offset // chunk_bytes
        chunks.append(
            _capture_exec(
                client,
                session_id,
                [
                    "dd",
                    f"if={path}",
                    f"bs={chunk_bytes}",
                    f"skip={index}",
                    "count=1",
                    "status=none",
                ],
            )
        )
    raw = b"".join(chunks)
    actual = hashlib.sha256(raw).hexdigest()
    if len(raw) != size or actual != expected:
        raise TransferError(f"incomplete or changed capture of {path} from {session_id}")
    return raw


def _capture_exec(client: BaristaClient, session_id: str, command: list[str]) -> bytes:
    handle = client.exec(session_id, command, timeout_seconds=_TRANSFER_TIMEOUT_S)
    op = client.wait_operation(handle.operation_id, timeout=_TRANSFER_TIMEOUT_S)
    exit_code = (op.result or {}).get("exit_code", 1)
    if not op.done or exit_code != 0:
        raise TransferError(f"capture command failed in {session_id} (exit {exit_code})")
    chunks: list[bytes] = []
    saw_exit = False
    for event in client.events(session_id, cursor=handle.event_cursor):
        if event.operation_id is not None and event.operation_id != handle.operation_id:
            continue
        if event.type == "exec.stdout":
            chunk = event.data.get("chunk")
            if chunk:
                chunks.append(base64.b64decode(chunk, validate=True))
        elif event.type == "exec.exit":
            saw_exit = True
            break
    if not saw_exit:
        raise TransferError(f"capture event stream ended early in {session_id}")
    return b"".join(chunks)


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()
