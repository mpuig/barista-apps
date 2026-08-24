"""Load the open contract schemas and validate payloads against them.

The conformance suite proves behavior through the *published* contract, so it
validates real provider responses against ``contracts/host-api`` and
``contracts/app-manifest``. OpenAPI component schemas are lifted into a
standalone JSON Schema document (``#/components/schemas/X`` rewritten to
``#/$defs/X``) so any named component can be validated directly.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from jsonschema import Draft202012Validator


def _contracts_dir() -> Path:
    override = os.environ.get("BARISTA_CONTRACTS_DIR")
    if override:
        return Path(override)
    # repo layout: conformance/barista_conformance/schemas.py -> repo/contracts
    return Path(__file__).resolve().parents[2] / "contracts"


@functools.lru_cache(maxsize=1)
def _openapi() -> dict[str, Any]:
    path = _contracts_dir() / "host-api" / "v1alpha1" / "openapi.yaml"
    return yaml.safe_load(path.read_text())


def _rewrite_refs(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/components/schemas/"):
                out[k] = v.replace("#/components/schemas/", "#/$defs/")
            else:
                out[k] = _rewrite_refs(v)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(x) for x in node]
    return node


@functools.lru_cache(maxsize=None)
def component_validator(name: str) -> Draft202012Validator:
    """A validator for a named Host API component schema (e.g. 'Session')."""
    components = _rewrite_refs(_openapi()["components"]["schemas"])
    root = {"$defs": components, "$ref": f"#/$defs/{name}"}
    return Draft202012Validator(root, format_checker=Draft202012Validator.FORMAT_CHECKER)


@functools.lru_cache(maxsize=None)
def _json_schema(*parts: str) -> Draft202012Validator:
    path = _contracts_dir().joinpath(*parts)
    schema = json.loads(path.read_text())
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def manifest_validator() -> Draft202012Validator:
    return _json_schema("app-manifest", "v1alpha1", "schema.json")


@functools.lru_cache(maxsize=1)
def manifest_rules() -> ModuleType:
    """The App Manifest rules JSON Schema cannot express, loaded from the same
    contracts tree the suite reads its schemas from.

    The subset rule between ``child_sessions.actions`` and
    ``permissions.actions`` is not in ``schema.json`` and cannot be — so the
    suite reuses the contract's reference implementation instead of keeping a
    second copy that could disagree with it.
    """
    path = _contracts_dir() / "app-manifest" / "v1alpha1" / "rules.py"
    spec = importlib.util.spec_from_file_location("barista_manifest_rules", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load manifest rules from {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the module defines dataclasses, and @dataclass
    # resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def event_validator() -> Draft202012Validator:
    return _json_schema("host-api", "v1alpha1", "streaming", "event.schema.json")


def attach_frame_validator() -> Draft202012Validator:
    return _json_schema("host-api", "v1alpha1", "streaming", "attach-frame.schema.json")


def assert_valid(validator: Draft202012Validator, payload: Any, what: str) -> None:
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise AssertionError(f"{what}: schema violation at {loc}: {first.message}")
