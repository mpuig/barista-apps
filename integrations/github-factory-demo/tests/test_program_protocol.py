from __future__ import annotations

import pytest

from barista_app_sdk.content import canonical_bytes
from barista_github_factory_demo.program_protocol import FeaturePlan, FeaturePlanError


def _plan() -> dict:
    return {
        "schema_version": "v1alpha1",
        "program": "program-8",
        "approved_commit": "a" * 40,
        "features": [
            {
                "id": "api",
                "title": "API",
                "summary": "Build API.",
                "acceptance_criteria": ["Pass."],
                "dependencies": [],
            },
            {
                "id": "web",
                "title": "Web",
                "summary": "Build web.",
                "acceptance_criteria": ["Pass."],
                "dependencies": ["api"],
            },
        ],
    }


def test_controller_independently_accepts_canonical_acyclic_plan():
    plan = FeaturePlan.parse_bytes(canonical_bytes(_plan()))
    assert plan.program == "program-8"
    assert plan.features[1]["dependencies"] == ["api"]


def test_controller_independently_rejects_cycles_and_authority_fields():
    cycle = _plan()
    cycle["features"][0]["dependencies"] = ["web"]
    with pytest.raises(FeaturePlanError, match="cycle"):
        FeaturePlan.parse_bytes(canonical_bytes(cycle))
    authority = _plan()
    authority["features"][0]["command"] = ["publish"]
    with pytest.raises(FeaturePlanError, match="fields"):
        FeaturePlan.parse_bytes(canonical_bytes(authority))
