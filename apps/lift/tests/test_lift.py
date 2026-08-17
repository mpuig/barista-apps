"""Lift tests: exact compatible/incompatible, semantic, interrupted transfer,
target rejection, source preservation, no-silent-fallback, auto confirmation.
All offline.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "conformance" / "tests"))

from mock_provider import MockProvider  # noqa: E402

from barista_app_sdk import BaristaClient, Config  # noqa: E402
from barista_app_sdk.adapters import (  # noqa: E402
    AdapterCapabilities,
    Attachment,
    AdapterResult,
    DetectResult,
    FidelityReport,
    LaunchSpec,
    SemanticBundle,
)
from barista_app_lift import (  # noqa: E402
    ConfirmationRequired,
    FakeCapsuleClient,
    Lift,
    LiftError,
    SourceRef,
)


def _client(name: str, capabilities: list | None = None) -> BaristaClient:
    return BaristaClient(
        Config(endpoint=f"http://{name}.invalid"),
        transport=MockProvider(name=name, capabilities=capabilities or []).transport(),
    )


# A target that advertises exact capsule transfer, as any real exact target must.
def _capsule_target() -> BaristaClient:
    return _client("target", capabilities=["capsule.export", "capsule.import"])


class FakeAdapter:
    name = "sh.barista.adapter.fake"

    def detect(self, workspace):
        return DetectResult(detected=True, native_version="1", supported=True)

    def capabilities(self):
        return AdapterCapabilities(
            name=self.name, supported_versions=["1"], semantic_export=True, continuation=True,
            native_media_types=["application/x-fake"],
        )

    def export_semantic(self, workspace):
        return SemanticBundle(
            adapter=self.name, created_at="2026-08-17T00:00:00Z",
            fidelity=FidelityReport(level="high", missing=["environment", "skills"]),
            inventory={"workspace": {"name": "w", "media_type": "text/x-workspace-path",
                                     "digest": "sha256:" + "aa" * 32, "size_bytes": 1},
                       "transcript": {"name": "t", "media_type": "application/x-fake",
                                      "digest": "sha256:" + "bb" * 32, "size_bytes": 2},
                       "continuation_prompt": "carry on"},
            native=[Attachment(name="s", media_type="application/x-fake", data=b"native")],
        )

    def continuation_launch(self, bundle):
        return LaunchSpec(command=["fake", "--resume"])

    def collect_result(self, workspace):
        return AdapterResult(exit_code=0)


# --------------------------------------------------------------------------- #
def test_exact_compatible_transfer_preserves_then_pauses_source():
    src, tgt = _client("source"), _capsule_target()
    session = src.ensure_session("pi", name="work")
    lift = Lift(src, tgt, capsule=FakeCapsuleClient())
    receipt = lift.transfer(
        SourceRef(managed=True, session_id=session.id), mode="exact", accept=lambda s: True
    )
    assert receipt.status == "completed"
    assert receipt.mode == "exact"
    assert set(receipt.transferred) == {"memory", "process", "disk", "lineage"}
    assert receipt.compatibility == "compatible"
    assert receipt.target_accepted is True
    assert receipt.source_disposition == "paused"  # not deleted by default
    src.close(); tgt.close()


def test_exact_incompatible_is_refused_and_source_preserved():
    src, tgt = _client("source"), _capsule_target()
    session = src.ensure_session("pi", name="work")
    capsule = FakeCapsuleClient(compat_key="target-cpu", export_compat_key="other-cpu")
    receipt = Lift(src, tgt, capsule=capsule).transfer(
        SourceRef(managed=True, session_id=session.id), mode="exact", accept=lambda s: True
    )
    assert receipt.status == "failed"
    assert receipt.compatibility == "incompatible"
    assert receipt.resumable_state["stage"] == "import"
    # Source still there.
    assert src.get_session(session.id).id == session.id
    src.close(); tgt.close()


def test_interrupted_upload_leaves_source_and_records_resumable_state():
    src, tgt = _client("source"), _capsule_target()
    session = src.ensure_session("pi", name="work")
    receipt = Lift(src, tgt, capsule=FakeCapsuleClient(fail_on="import")).transfer(
        SourceRef(managed=True, session_id=session.id), mode="exact", accept=lambda s: True
    )
    assert receipt.status == "failed"
    assert receipt.resumable_state["stage"] == "import"
    assert src.get_session(session.id).id == session.id  # preserved
    src.close(); tgt.close()


def test_target_rejection_does_not_delete_source():
    src, tgt = _client("source"), _capsule_target()
    session = src.ensure_session("pi", name="work")
    receipt = Lift(src, tgt, capsule=FakeCapsuleClient()).transfer(
        SourceRef(managed=True, session_id=session.id),
        mode="exact", accept=lambda s: False, delete_source_on_accept=True,
    )
    assert receipt.target_accepted is False
    assert receipt.status == "failed"
    assert receipt.source_disposition == "preserved"
    assert src.get_session(session.id).id == session.id
    src.close(); tgt.close()


def test_exact_without_capsule_client_refuses_cleanly_no_crash():
    # A capsule-capable target but no capsule client wired: exact must refuse
    # with a LiftError, never crash on an internal assert (finding 6).
    src, tgt = _client("source"), _capsule_target()
    lift = Lift(src, tgt, adapter=FakeAdapter(), target_app="pi")
    session = src.ensure_session("pi", name="work")
    with pytest.raises(LiftError):
        lift.transfer(SourceRef(managed=True, session_id=session.id), mode="exact")
    src.close(); tgt.close()


def test_exact_only_request_refuses_on_native_source_no_fallback():
    src, tgt = _client("source"), _client("target")
    lift = Lift(src, tgt, adapter=FakeAdapter(), target_app="pi")  # adapter present, but exact asked
    with pytest.raises(LiftError) as ei:
        lift.transfer(SourceRef(managed=False, workspace="/work"), mode="exact")
    assert "exact transfer not possible" in str(ei.value)
    src.close(); tgt.close()


def test_native_semantic_transfer_reports_fidelity():
    src, tgt = _client("source"), _client("target")
    lift = Lift(src, tgt, adapter=FakeAdapter(), target_app="pi")
    receipt = lift.transfer(SourceRef(managed=False, workspace="/work/project"), mode="semantic")
    assert receipt.status == "completed"
    assert receipt.mode == "semantic"
    assert "workspace" in receipt.transferred and "transcript" in receipt.transferred
    assert set(receipt.missing) == {"environment", "skills"}
    assert receipt.source_disposition == "preserved"
    assert receipt.target_session_id
    src.close(); tgt.close()


def test_auto_requires_confirmation_then_runs_semantic():
    src, tgt = _client("source"), _client("target")
    lift = Lift(src, tgt, adapter=FakeAdapter(), target_app="pi")
    src_ref = SourceRef(managed=False, workspace="/work/project")
    with pytest.raises(ConfirmationRequired):
        lift.transfer(src_ref, mode="auto", confirm=False)
    receipt = lift.transfer(src_ref, mode="auto", confirm=True)
    assert receipt.mode == "semantic" and receipt.status == "completed"
    src.close(); tgt.close()


def test_receipt_carries_no_secret_values():
    src, tgt = _client("source"), _client("target")
    lift = Lift(src, tgt, adapter=FakeAdapter(), target_app="pi")
    receipt = lift.transfer(SourceRef(managed=False, workspace="/w"), mode="semantic")
    blob = json.dumps(receipt.to_dict())
    assert "sk-" not in blob and "secret-value" not in blob


def test_manifest_is_valid():
    from jsonschema import Draft202012Validator

    manifest = json.loads((REPO / "apps" / "lift" / "manifest.json").read_text())
    schema = json.loads((REPO / "contracts" / "app-manifest" / "v1alpha1" / "schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(manifest)
