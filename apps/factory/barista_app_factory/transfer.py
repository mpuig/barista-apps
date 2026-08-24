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


def _digest(raw: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(raw).hexdigest()
