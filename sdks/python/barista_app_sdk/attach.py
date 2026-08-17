"""Attach-stream frame codec.

Encodes/decodes frames on a WebSocket attach stream per the contract's
attach-frame schema. Separated from any live WebSocket transport so it is fully
testable offline; the ``open_attach`` helper (requires the ``ws`` extra) uses it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class AttachFrame:
    direction: str  # client_to_host | host_to_client
    kind: str       # stdin | stdout | stderr | resize | close | exit
    chunk: Optional[bytes] = None
    resize: Optional[tuple[int, int]] = None  # (cols, rows)
    exit_code: Optional[int] = None

    def to_wire(self) -> str:
        frame: dict = {"direction": self.direction, "kind": self.kind}
        if self.chunk is not None:
            frame["chunk"] = base64.b64encode(self.chunk).decode()
        if self.resize is not None:
            frame["resize"] = {"cols": self.resize[0], "rows": self.resize[1]}
        if self.exit_code is not None:
            frame["exit_code"] = self.exit_code
        return json.dumps(frame)

    @classmethod
    def from_wire(cls, text: str) -> "AttachFrame":
        d = json.loads(text)
        resize = None
        if "resize" in d:
            resize = (d["resize"]["cols"], d["resize"]["rows"])
        chunk = base64.b64decode(d["chunk"]) if "chunk" in d else None
        return cls(
            direction=d["direction"], kind=d["kind"], chunk=chunk,
            resize=resize, exit_code=d.get("exit_code"),
        )


def stdin(data: bytes) -> AttachFrame:
    return AttachFrame(direction="client_to_host", kind="stdin", chunk=data)


def resize(cols: int, rows: int) -> AttachFrame:
    return AttachFrame(direction="client_to_host", kind="resize", resize=(cols, rows))
