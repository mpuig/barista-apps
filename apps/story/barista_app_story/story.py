"""Session Story builder.

Selects and canonically assembles knowledge records into a deterministic,
redacted, non-executable bundle conforming to the open Session Story schema. The
story id is the digest of the canonical bundle bytes; the same records and
redaction-policy version always produce the same id. A story never contains a
capsule object, writable filesystem, bearer grant, secret value, or executable
capability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

# One canonical serialization for the whole ecosystem lives in the SDK; re-export
# it so a story id, a factory receipt digest, and any other content id are
# computed byte-for-byte identically.
from barista_app_sdk.content import canonical_bytes, content_id  # noqa: F401

from . import redaction

SCHEMA_VERSION = "v1alpha1"
RECORD_TYPES = {
    "event", "decision", "command", "diff", "commit", "receipt", "evaluation", "artifact_ref",
}


def record_digest(record: dict) -> str:
    return content_id(record)


class StoryError(ValueError):
    pass


@dataclass
class Source:
    app: Optional[str] = None
    app_version: Optional[str] = None

    def to_dict(self) -> dict:
        d = {}
        if self.app:
            d["app"] = self.app
        if self.app_version:
            d["app_version"] = self.app_version
        return d


class StoryBuilder:
    def __init__(self, *, policy_name: str = redaction.POLICY_NAME, policy_version: str = redaction.POLICY_VERSION):
        self.policy_name = policy_name
        self.policy_version = policy_version

    def build(
        self,
        records: Iterable[dict],
        *,
        created_at: str,
        title: Optional[str] = None,
        source: Optional[Source] = None,
    ) -> dict:
        """Assemble a canonical story bundle. ``created_at`` is an input so the
        story id is deterministic for identical selections."""
        assembled: list[dict] = []
        removed = redaction.Counter()

        for seq, raw in enumerate(sorted(records, key=_selection_key)):
            rtype = raw.get("type")
            if rtype not in RECORD_TYPES:
                raise StoryError(f"unknown record type {rtype!r}")
            rec: dict = {"seq": seq, "type": rtype}
            if "time" in raw:
                rec["time"] = raw["time"]
            if "text" in raw and raw["text"] is not None:
                result = redaction.redact_text(str(raw["text"]))
                redaction.assert_no_residual_secret(result.text)
                rec["text"] = result.text
                removed.update(result.removed)
            if rtype == "artifact_ref":
                art = raw.get("artifact") or {}
                redaction.check_media(art.get("media_type", ""))
                if "digest" not in art:
                    raise StoryError("artifact_ref requires a digest")
                rec["artifact"] = {
                    "name": art.get("name"),
                    "digest": art["digest"],
                    "media_type": art["media_type"],
                }
                rec["artifact"] = {k: v for k, v in rec["artifact"].items() if v is not None}
            if "data" in raw and raw["data"] is not None:
                # Data is inert metadata only; scan its JSON text for secrets too.
                blob = json.dumps(raw["data"], sort_keys=True)
                redaction.assert_no_residual_secret(blob)
                rec["data"] = raw["data"]
            assembled.append(rec)

        bundle: dict = {
            "schema_version": SCHEMA_VERSION,
            "redaction_policy": {"name": self.policy_name, "version": self.policy_version},
            "created_at": created_at,
            "records": assembled,
        }
        if title:
            bundle["title"] = title
        if source and source.to_dict():
            bundle["source"] = source.to_dict()
        if removed:
            bundle["removed"] = [
                {"category": c, "count": n} for c, n in sorted(removed.items())
            ]

        bundle["story_id"] = content_id({k: v for k, v in bundle.items() if k != "story_id"})
        _assert_non_executable(bundle)
        return bundle

    # -- signing / provenance -------------------------------------------- #
    @staticmethod
    def sign(bundle: dict, signer: Callable[[bytes], str], *, algorithm: str, key_id: Optional[str] = None) -> dict:
        payload = canonical_bytes({k: v for k, v in bundle.items() if k not in ("story_id", "signature")})
        signed = dict(bundle)
        signed["signature"] = {"algorithm": algorithm, "value": signer(payload)}
        if key_id:
            signed["signature"]["key_id"] = key_id
        return signed

    @staticmethod
    def verify(bundle: dict, verifier: Callable[[bytes, str], bool]) -> bool:
        sig = bundle.get("signature")
        if not sig:
            return False
        payload = canonical_bytes({k: v for k, v in bundle.items() if k not in ("story_id", "signature")})
        return verifier(payload, sig["value"])

    # -- pseudonymization ------------------------------------------------ #
    @staticmethod
    def pseudonymize(bundle: dict, name_map: dict[str, str]) -> dict:
        """Replace tenant-private names with stable public pseudonyms and mint a
        NEW story id. Record content (and thus record digests) is unchanged."""
        text = json.dumps(bundle, sort_keys=True)
        for private, public in name_map.items():
            text = text.replace(private, public)
        new = json.loads(text)
        new.pop("signature", None)  # signed content changed -> a new envelope
        new["story_id"] = content_id({k: v for k, v in new.items() if k != "story_id"})
        return new


def _selection_key(record: dict) -> tuple:
    # Deterministic ordering: by explicit seq if present, else by (time, type).
    return (record.get("seq", 1 << 30), str(record.get("time", "")), str(record.get("type", "")))


_FORBIDDEN_KEYS = {"capsule_object", "capsule", "grant", "bearer", "filesystem", "exec", "command_exec"}


def _assert_non_executable(bundle: dict) -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _FORBIDDEN_KEYS:
                    raise StoryError(f"story must not contain executable/grant field {k!r}")
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(bundle)
