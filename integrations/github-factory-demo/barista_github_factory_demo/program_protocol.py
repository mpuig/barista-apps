"""Independent closed parser for Factory feature-plan output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from barista_app_sdk.content import canonical_bytes, content_id
from barista_app_sdk.sensitive import assert_no_high_confidence_secrets

_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


class FeaturePlanError(ValueError):
    pass


@dataclass(frozen=True)
class FeaturePlan:
    program: str
    approved_commit: str
    features: tuple[dict, ...]

    @classmethod
    def parse_bytes(cls, raw: bytes) -> FeaturePlan:
        if not raw or len(raw) > 64 * 1024:
            raise FeaturePlanError("feature plan size is invalid")
        try:
            text = raw.decode("utf-8")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeaturePlanError("feature plan must be UTF-8 JSON") from exc
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "program",
            "approved_commit",
            "features",
        }:
            raise FeaturePlanError("feature plan fields are invalid")
        program = document.get("program")
        commit = document.get("approved_commit")
        values = document.get("features")
        if (
            document.get("schema_version") != "v1alpha1"
            or not isinstance(program, str)
            or not program
            or len(program) > 160
            or not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            or not isinstance(values, list)
            or not 1 <= len(values) <= 16
        ):
            raise FeaturePlanError("feature plan identity is invalid")
        identities: set[str] = set()
        parsed: list[dict] = []
        for feature in values:
            if not isinstance(feature, dict) or set(feature) != {
                "id",
                "title",
                "summary",
                "acceptance_criteria",
                "dependencies",
            }:
                raise FeaturePlanError("feature fields are invalid")
            feature_id = feature.get("id")
            criteria = feature.get("acceptance_criteria")
            dependencies = feature.get("dependencies")
            if (
                not isinstance(feature_id, str)
                or _ID.fullmatch(feature_id) is None
                or feature_id in identities
                or not isinstance(feature.get("title"), str)
                or not feature["title"].strip()
                or len(feature["title"]) > 200
                or not isinstance(feature.get("summary"), str)
                or not feature["summary"].strip()
                or len(feature["summary"]) > 4000
                or not isinstance(criteria, list)
                or not 1 <= len(criteria) <= 12
                or any(
                    not isinstance(item, str) or not item.strip() or len(item) > 1000
                    for item in criteria
                )
                or not isinstance(dependencies, list)
                or len(dependencies) > 15
                or any(
                    not isinstance(item, str) or _ID.fullmatch(item) is None
                    for item in dependencies
                )
                or len(dependencies) != len(set(dependencies))
            ):
                raise FeaturePlanError("feature is invalid")
            identities.add(feature_id)
            parsed.append(dict(feature))
        if any(
            dependency not in identities or dependency == feature["id"]
            for feature in parsed
            for dependency in feature["dependencies"]
        ):
            raise FeaturePlanError("feature dependency is unknown or self-referential")
        graph = {feature["id"]: feature["dependencies"] for feature in parsed}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise FeaturePlanError("feature dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        if raw != canonical_bytes(document):
            raise FeaturePlanError("feature plan is not canonical")
        assert_no_high_confidence_secrets(text)
        return cls(program, commit, tuple(parsed))

    def to_document(self) -> dict:
        return {
            "schema_version": "v1alpha1",
            "program": self.program,
            "approved_commit": self.approved_commit,
            "features": [dict(feature) for feature in self.features],
        }

    def content_id(self) -> str:
        return content_id(self.to_document())
