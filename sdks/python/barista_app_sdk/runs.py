"""Provider-neutral App Run models and declaration-aware validation.

An App Run is a canonical client/app protocol over existing Host API resources,
not a second provider scheduler.  The models deep-freeze parsed JSON so the
bytes validated before launch are the bytes delivered to the workload.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for

from .errors import InvalidRequestError

APP_RUN_ENV = "BARISTA_APP_RUN"
APP_SESSION_ID_ENV = "BARISTA_APP_SESSION_ID"
APP_RUN_MEDIA_TYPE = "application/vnd.barista.app-run.v1alpha1+json"
APP_RUN_RESULT_MEDIA_TYPE = "application/vnd.barista.app-run-result.v1alpha1+json"

_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_KIND = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
_SECRET_REF = re.compile(r"^(?:secret|grant|ref|vault|env)://.+$")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _invalid(message: str, *, details: Optional[dict] = None) -> InvalidRequestError:
    return InvalidRequestError(
        message, code="app_run.invalid", details=details or {}, error_class="invalid_request"
    )


def _require_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) > 80 or not _NAME.fullmatch(value):
        raise _invalid(f"{field_name} must be a lowercase name containing letters, digits, or hyphens")
    return value


def _require_kind(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) > 214 or not _KIND.fullmatch(value):
        raise _invalid(f"{field_name} must be a namespaced kind")
    return value


def canonical_bytes(value: Any) -> bytes:
    """The ecosystem's sorted, compact, UTF-8, newline-terminated JSON."""
    return (
        json.dumps(_thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def content_id(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class SecretReference:
    name: str
    ref: str

    @classmethod
    def parse(cls, name: str, ref: Any) -> "SecretReference":
        alias = _require_name(name, "secret alias")
        if not isinstance(ref, str) or not _SECRET_REF.fullmatch(ref):
            raise _invalid(f"secret {alias} must be a provider-resolvable reference")
        return cls(name=alias, ref=ref)


@dataclass(frozen=True)
class RunBinding:
    kind: str
    uri: str
    ref: Optional[str] = None
    credential: Optional[str] = None
    options: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def parse(cls, value: Mapping[str, Any], *, name: str = "binding") -> "RunBinding":
        allowed = {"kind", "uri", "ref", "credential", "options"}
        extra = set(value) - allowed
        if extra:
            raise _invalid(f"{name} has unknown fields: {', '.join(sorted(extra))}")
        kind = _require_kind(value.get("kind"), f"{name}.kind")
        uri = value.get("uri")
        if not isinstance(uri, str) or not uri:
            raise _invalid(f"{name}.uri must be a non-empty string")
        ref = value.get("ref")
        if ref is not None and (not isinstance(ref, str) or not ref):
            raise _invalid(f"{name}.ref must be a non-empty string")
        credential = value.get("credential")
        if credential is not None:
            credential = _require_name(credential, f"{name}.credential")
        options = value.get("options", {})
        if not isinstance(options, Mapping):
            raise _invalid(f"{name}.options must be an object")
        return cls(kind=kind, uri=uri, ref=ref, credential=credential, options=_freeze(options))

    def to_document(self) -> dict:
        result = {"kind": self.kind, "uri": self.uri}
        if self.ref is not None:
            result["ref"] = self.ref
        if self.credential is not None:
            result["credential"] = self.credential
        if self.options:
            result["options"] = _thaw(self.options)
        return result


@dataclass(frozen=True)
class DeliveryRequest:
    kind: str
    target: Optional[str] = None
    credential: Optional[str] = None
    options: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def parse(cls, value: Mapping[str, Any], *, name: str = "delivery") -> "DeliveryRequest":
        allowed = {"kind", "target", "credential", "options"}
        extra = set(value) - allowed
        if extra:
            raise _invalid(f"{name} has unknown fields: {', '.join(sorted(extra))}")
        kind = _require_kind(value.get("kind"), f"{name}.kind")
        target = value.get("target")
        if target is not None and (not isinstance(target, str) or not target):
            raise _invalid(f"{name}.target must be a non-empty string")
        credential = value.get("credential")
        if credential is not None:
            credential = _require_name(credential, f"{name}.credential")
        options = value.get("options", {})
        if not isinstance(options, Mapping):
            raise _invalid(f"{name}.options must be an object")
        return cls(kind=kind, target=target, credential=credential, options=_freeze(options))

    def to_document(self) -> dict:
        result = {"kind": self.kind}
        if self.target is not None:
            result["target"] = self.target
        if self.credential is not None:
            result["credential"] = self.credential
        if self.options:
            result["options"] = _thaw(self.options)
        return result


@dataclass(frozen=True)
class AppRun:
    name: str
    app: str
    operation: str
    input_media_type: str
    input_value: Any
    bindings: Mapping[str, RunBinding] = field(default_factory=lambda: MappingProxyType({}))
    secrets: Mapping[str, SecretReference] = field(default_factory=lambda: MappingProxyType({}))
    deliveries: Mapping[str, DeliveryRequest] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    schema_version: str = "v1alpha1"

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "AppRun":
        allowed = {
            "schema_version", "name", "app", "operation", "input",
            "bindings", "secrets", "deliveries", "metadata",
        }
        extra = set(value) - allowed
        if extra:
            raise _invalid(f"run has unknown fields: {', '.join(sorted(extra))}")
        if value.get("schema_version") != "v1alpha1":
            raise _invalid("run.schema_version must be v1alpha1")
        name = _require_name(value.get("name"), "run.name")
        operation = _require_name(value.get("operation"), "run.operation")
        app = value.get("app")
        if not isinstance(app, str) or not app:
            raise _invalid("run.app must be a non-empty app identity or manifest locator")
        input_doc = value.get("input")
        if not isinstance(input_doc, Mapping) or set(input_doc) != {"media_type", "value"}:
            raise _invalid("run.input must contain exactly media_type and value")
        media_type = input_doc.get("media_type")
        if not isinstance(media_type, str) or "/" not in media_type:
            raise _invalid("run.input.media_type must be a media type")

        raw_bindings = value.get("bindings", {})
        raw_secrets = value.get("secrets", {})
        raw_deliveries = value.get("deliveries", {})
        metadata = value.get("metadata", {})
        for field_name, item in (
            ("bindings", raw_bindings), ("secrets", raw_secrets),
            ("deliveries", raw_deliveries), ("metadata", metadata),
        ):
            if not isinstance(item, Mapping):
                raise _invalid(f"run.{field_name} must be an object")

        bindings = {
            _require_name(k, "binding name"): RunBinding.parse(v, name=f"binding {k}")
            for k, v in raw_bindings.items()
        }
        secrets = {
            secret.name: secret
            for secret in (SecretReference.parse(key, ref) for key, ref in raw_secrets.items())
        }
        deliveries = {
            _require_name(k, "delivery name"): DeliveryRequest.parse(v, name=f"delivery {k}")
            for k, v in raw_deliveries.items()
        }
        referenced = {
            item.credential for item in (*bindings.values(), *deliveries.values()) if item.credential
        }
        missing = sorted(referenced - set(secrets))
        if missing:
            raise _invalid(
                f"credential aliases are not declared in run.secrets: {', '.join(missing)}",
                details={"missing_secret_aliases": missing},
            )
        return cls(
            name=name, app=app, operation=operation,
            input_media_type=media_type, input_value=_freeze(input_doc["value"]),
            bindings=MappingProxyType(bindings), secrets=MappingProxyType(secrets),
            deliveries=MappingProxyType(deliveries), metadata=_freeze(metadata),
        )

    def to_document(self) -> dict:
        result = {
            "schema_version": self.schema_version,
            "name": self.name,
            "app": self.app,
            "operation": self.operation,
            "input": {"media_type": self.input_media_type, "value": _thaw(self.input_value)},
        }
        if self.bindings:
            result["bindings"] = {k: v.to_document() for k, v in self.bindings.items()}
        if self.secrets:
            result["secrets"] = {name: secret.ref for name, secret in self.secrets.items()}
        if self.deliveries:
            result["deliveries"] = {k: v.to_document() for k, v in self.deliveries.items()}
        if self.metadata:
            result["metadata"] = _thaw(self.metadata)
        return result

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_document())

    def content_id(self) -> str:
        return content_id(self.to_document())


@dataclass(frozen=True)
class RunSlot:
    kinds: tuple[str, ...]
    required: bool = False


@dataclass(frozen=True)
class RunOperation:
    name: str
    lifecycle: str
    input_media_type: str
    input_schema: Optional[Mapping[str, Any]]
    bindings: Mapping[str, RunSlot]
    outputs: Mapping[str, RunSlot]
    deliveries: Mapping[str, RunSlot]

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any], operation: str) -> "RunOperation":
        runs = manifest.get("runs", {})
        if operation not in runs:
            raise _invalid(
                f"app does not declare run operation '{operation}'",
                details={"operation": operation, "declared": sorted(runs)},
            )
        raw = runs[operation]

        def slots(field_name: str) -> Mapping[str, RunSlot]:
            parsed = {}
            for name, slot in raw.get(field_name, {}).items():
                parsed[_require_name(name, f"{field_name} name")] = RunSlot(
                    kinds=tuple(slot["kinds"]), required=bool(slot.get("required", False))
                )
            return MappingProxyType(parsed)

        input_doc = raw["input"]
        return cls(
            name=operation,
            lifecycle=raw["lifecycle"],
            input_media_type=input_doc["media_type"],
            input_schema=_freeze(input_doc["schema"]) if "schema" in input_doc else None,
            bindings=slots("bindings"), outputs=slots("outputs"), deliveries=slots("deliveries"),
        )


def validate_run(run: AppRun, manifest: Mapping[str, Any]) -> RunOperation:
    """Validate app-specific declarations without making a transport call."""
    operation = RunOperation.from_manifest(manifest, run.operation)
    if run.input_media_type != operation.input_media_type:
        raise _invalid(
            f"input media type {run.input_media_type!r} does not match {operation.input_media_type!r}",
            details={"expected": operation.input_media_type, "actual": run.input_media_type},
        )

    _validate_slots("binding", run.bindings, operation.bindings)
    _validate_slots("delivery", run.deliveries, operation.deliveries)

    if operation.input_schema is not None:
        schema = _thaw(operation.input_schema)
        validator_for(schema).check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(_thaw(run.input_value)),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            first = errors[0]
            path = ".".join(str(p) for p in first.absolute_path) or "input"
            raise _invalid(
                f"run input is invalid at {path}: {first.message}",
                details={"path": list(first.absolute_path), "validator": first.validator},
            )
    return operation


def _validate_slots(
    label: str,
    actual: Mapping[str, RunBinding | DeliveryRequest],
    declared: Mapping[str, RunSlot],
) -> None:
    plural = "deliveries" if label == "delivery" else f"{label}s"
    undeclared = sorted(set(actual) - set(declared))
    if undeclared:
        raise _invalid(
            f"undeclared {plural}: {', '.join(undeclared)}",
            details={f"undeclared_{plural}": undeclared},
        )
    missing = sorted(name for name, slot in declared.items() if slot.required and name not in actual)
    if missing:
        raise _invalid(
            f"required {plural} are missing: {', '.join(missing)}",
            details={f"missing_{plural}": missing},
        )
    wrong = {
        name: item.kind
        for name, item in actual.items()
        if item.kind not in declared[name].kinds
    }
    if wrong:
        raise _invalid(
            f"unsupported {label} kinds: " + ", ".join(f"{name}={kind}" for name, kind in wrong.items()),
            details={f"unsupported_{label}_kinds": wrong},
        )


@dataclass(frozen=True)
class AppRunResult:
    """Deep-frozen result document; semantic validation belongs to its schema."""

    document: Mapping[str, Any]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "AppRunResult":
        if value.get("schema_version") != "v1alpha1":
            raise _invalid("result.schema_version must be v1alpha1")
        return cls(document=_freeze(value))

    def to_document(self) -> dict:
        return _thaw(self.document)

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.document)

    def content_id(self) -> str:
        return content_id(self.document)
