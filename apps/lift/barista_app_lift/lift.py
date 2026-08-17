"""The Lift orchestrator.

Moves an agent session between hosts. Exact mode requires a Barista-managed,
compatible capsule and preserves memory/process/disk/lineage; semantic mode
starts a new process from an adapter bundle and reports what transferred and
what did not. Lift NEVER silently substitutes semantic for a requested exact
transfer, and it preserves the source until the target is accepted.
"""

from __future__ import annotations

from typing import Callable, Optional

from barista_app_sdk import BaristaClient
from barista_app_sdk.adapters import Adapter
from barista_app_sdk.sensitive import assert_no_secret_values

from .capsule import Capsule, CapsuleClient, CapsuleError, CapsuleIncompatible
from .receipt import Classification, SourceRef, TransferReceipt

EXACT_CAPS = ("capsule.export", "capsule.import")


class LiftError(RuntimeError):
    pass


class ConfirmationRequired(RuntimeError):
    """auto mode resolved to semantic; the caller must confirm the downgrade."""

    def __init__(self, classification: Classification):
        super().__init__("semantic transfer requires confirmation")
        self.classification = classification


class Lift:
    def __init__(
        self,
        source_client: BaristaClient,
        target_client: BaristaClient,
        *,
        capsule: Optional[CapsuleClient] = None,
        adapter: Optional[Adapter] = None,
        target_app: Optional[str] = None,
    ):
        self.source_client = source_client
        self.target_client = target_client
        self.capsule = capsule
        self.adapter = adapter
        self.target_app = target_app

    # -- classification --------------------------------------------------- #
    def classify(self, source: SourceRef) -> Classification:
        exact = False
        reason = ""
        if not source.managed or not source.session_id:
            reason = "source is not a Barista-managed session; exact transfer is impossible"
        elif self.capsule is None and not all(self.target_client.supports(c) for c in EXACT_CAPS):
            reason = "target does not advertise exact capsule transfer"
        else:
            exact = True
            reason = "source is managed and target supports exact capsule transfer"
        semantic = self.adapter is not None and source.workspace is not None
        return Classification(exact_capable=exact, semantic_capable=semantic, reason=reason)

    # -- entry point ------------------------------------------------------ #
    def transfer(
        self,
        source: SourceRef,
        *,
        mode: str = "auto",
        confirm: bool = False,
        accept: Optional[Callable[[str], bool]] = None,
        delete_source_on_accept: bool = False,
    ) -> TransferReceipt:
        if mode not in ("exact", "semantic", "auto"):
            raise LiftError(f"unknown mode {mode!r}")
        cls = self.classify(source)

        if mode == "exact":
            if not cls.exact_capable:
                # No silent fallback: refuse with an explanation.
                raise LiftError(f"exact transfer not possible: {cls.reason}")
            return self._exact(source, accept, delete_source_on_accept)

        if mode == "semantic":
            if not cls.semantic_capable:
                raise LiftError("semantic transfer needs an adapter and a source workspace")
            return self._semantic(source, accept)

        # auto: prefer exact; otherwise semantic but require confirmation.
        if cls.exact_capable:
            return self._exact(source, accept, delete_source_on_accept)
        if not cls.semantic_capable:
            raise LiftError(f"no transfer mode available: {cls.reason}")
        if not confirm:
            raise ConfirmationRequired(cls)
        return self._semantic(source, accept)

    # -- exact ------------------------------------------------------------ #
    def _exact(
        self, source: SourceRef, accept: Optional[Callable[[str], bool]], delete_source: bool
    ) -> TransferReceipt:
        assert self.capsule is not None
        receipt = TransferReceipt(
            mode="exact", status="failed",
            source_provider=self.source_client.discovery().provider.get("name", "source"),
            target_provider=self.target_client.discovery().provider.get("name", "target"),
        )
        capsule: Optional[Capsule] = None
        try:
            capsule = self.capsule.export(source.session_id)  # type: ignore[arg-type]
            receipt.content_id = capsule.capsule_id
            receipt.lineage_id = capsule.lineage_id
            if not self.capsule.verify(capsule):
                raise CapsuleError("exported capsule failed verification")
            self.capsule.import_capsule(capsule)  # raises CapsuleIncompatible on mismatch
            receipt.compatibility = "compatible"
            target_session = self.capsule.restore(capsule, f"lift-{source.session_id}")
            receipt.target_session_id = target_session
        except CapsuleIncompatible as exc:
            receipt.compatibility = "incompatible"
            receipt.error = str(exc)
            receipt.resumable_state = {"stage": "import", "content_id": receipt.content_id}
            return receipt  # source preserved, untouched
        except CapsuleError as exc:
            receipt.error = str(exc)
            receipt.resumable_state = {
                "stage": "export" if capsule is None else "import",
                "content_id": receipt.content_id,
            }
            return receipt  # source preserved

        # Acceptance gate: only after the target is accepted may we touch source.
        accepted = accept(receipt.target_session_id) if accept else True
        receipt.target_accepted = accepted
        if accepted:
            receipt.transferred = ["memory", "process", "disk", "lineage"]
            if delete_source:
                receipt.source_disposition = "deleted"
                try:
                    self.source_client.delete_session(source.session_id)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001
                    receipt.source_disposition = "preserved"
            else:
                receipt.source_disposition = "paused"
            receipt.status = "completed"
        else:
            receipt.resumable_state = {"stage": "acceptance", "content_id": receipt.content_id}
        return receipt

    # -- semantic --------------------------------------------------------- #
    def _semantic(self, source: SourceRef, accept: Optional[Callable[[str], bool]]) -> TransferReceipt:
        assert self.adapter is not None
        receipt = TransferReceipt(
            mode="semantic", status="failed",
            source_provider=self.source_client.discovery().provider.get("name", "source"),
            target_provider=self.target_client.discovery().provider.get("name", "target"),
            adapter=self.adapter.name,
        )
        bundle = self.adapter.export_semantic(source.workspace)  # type: ignore[arg-type]
        doc = bundle.to_document()
        # A receipt must carry no secret values.
        assert_no_secret_values(doc, [])
        caps = self.adapter.capabilities()
        receipt.adapter_version = ",".join(caps.supported_versions)
        receipt.transferred = [k for k in bundle.inventory.keys()]
        receipt.missing = list(bundle.fidelity.missing)
        receipt.compatibility = f"fidelity:{bundle.fidelity.level}"

        target_app = self.target_app
        if not target_app:
            raise LiftError("semantic transfer requires a target_app to launch the continuation")
        target = self.target_client.ensure_session(
            target_app, name=f"lift-{self.adapter.name.split('.')[-1]}",
            metadata={"lift": "semantic", "adapter": self.adapter.name},
        )
        receipt.target_session_id = target.id
        accepted = accept(target.id) if accept else True
        receipt.target_accepted = accepted
        # Semantic Lift starts a NEW process; the (native) source is left intact.
        receipt.source_disposition = "preserved"
        receipt.status = "completed" if accepted else "failed"
        if not accepted:
            receipt.resumable_state = {"stage": "acceptance", "content_id": bundle.native[0].to_manifest_entry()["digest"] if bundle.native else None}
        return receipt
