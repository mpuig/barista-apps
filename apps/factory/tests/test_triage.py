from __future__ import annotations

import json

import pytest

from barista_app_factory.triage import (
    MAX_DECISION_BYTES,
    TriageDecision,
    TriageDecisionError,
)
from barista_app_sdk.content import canonical_bytes
from barista_app_sdk.sensitive import SecretLeak


def encoded(**values) -> bytes:
    return canonical_bytes({"schema_version": "v1alpha1", **values})


def test_ready_decision_is_closed_canonical_and_digest_identified():
    raw = encoded(
        state="ready",
        summary="Add bounded log history.",
        acceptance_criteria=["Tail is at most 1000 lines."],
    )
    decision = TriageDecision.parse_bytes(raw)
    assert decision.state == "ready"
    assert decision.acceptance_criteria == ("Tail is at most 1000 lines.",)
    assert decision.canonical_bytes() == raw
    assert decision.content_id().startswith("sha256:")


def test_needs_input_accepts_only_one_to_five_focused_questions():
    decision = TriageDecision.parse_bytes(
        encoded(
            state="needs_input", questions=["Which API version must remain compatible?"]
        )
    )
    assert decision.questions == ("Which API version must remain compatible?",)
    with pytest.raises(TriageDecisionError, match="questions"):
        TriageDecision.parse_bytes(encoded(state="needs_input", questions=[]))
    with pytest.raises(TriageDecisionError, match="questions"):
        TriageDecision.parse_bytes(
            encoded(state="needs_input", questions=[str(i) for i in range(6)])
        )


@pytest.mark.parametrize(
    "raw, message",
    [
        (b"{", "UTF-8 JSON"),
        (b"\xff", "UTF-8 JSON"),
        (b"x" * (MAX_DECISION_BYTES + 1), "size"),
        (
            encoded(
                state="ready", summary="x", acceptance_criteria=["y"], command=["sh"]
            ),
            "unknown",
        ),
        (
            encoded(state="refused", reason_code="BAD-CODE", message="No"),
            "inconsistent",
        ),
        (
            json.dumps(
                {
                    "schema_version": "v1alpha1",
                    "state": "needs_input",
                    "questions": ["Why?"],
                }
            ).encode(),
            "canonical",
        ),
    ],
)
def test_malformed_oversized_and_policy_shaped_decisions_are_refused(raw, message):
    with pytest.raises(TriageDecisionError, match=message):
        TriageDecision.parse_bytes(raw)


def test_secret_bearing_decision_is_refused():
    raw = encoded(
        state="needs_input",
        questions=["Use token ghp_abcdefghijklmnopqrstuvwxyz1234567890ABCD?"],
    )
    with pytest.raises(SecretLeak):
        TriageDecision.parse_bytes(raw)


def test_states_cannot_smuggle_fields_from_another_variant():
    with pytest.raises(TriageDecisionError, match="inconsistent"):
        TriageDecision.parse_bytes(
            encoded(
                state="ready", summary="x", acceptance_criteria=["y"], questions=["z"]
            )
        )
    with pytest.raises(TriageDecisionError, match="inconsistent"):
        TriageDecision.parse_bytes(
            encoded(
                state="refused",
                reason_code="unsupported",
                message="No",
                acceptance_criteria=["ship it"],
            )
        )
