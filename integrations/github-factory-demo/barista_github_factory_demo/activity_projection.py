"""Project Factory state into a provider's generic tenant activity API.

This module owns the Factory-to-generic mapping. The receiving service never
needs to know what a BRD, feature dependency, GitHub Project, or program means.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import ControllerConfig

_MAX_RESPONSE = 64 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _time(value: int | None, fallback: int) -> str:
    return datetime.fromtimestamp(value if value is not None else fallback, UTC).isoformat()


def _link(rel: str, label: str, url: str | None) -> dict | None:
    return {"rel": rel, "label": label, "url": url} if url else None


def _event(
    identity: str,
    event_type: str,
    title: str,
    occurred_at: str,
    *,
    phase: str | None = None,
    summary: str | None = None,
    actor: str | None = None,
    links: list[dict | None] | None = None,
    attributes: dict[str, str] | None = None,
) -> dict:
    return {
        "id": identity,
        "type": event_type,
        "title": title,
        **({"summary": summary} if summary else {}),
        **({"phase": phase} if phase else {}),
        "occurred_at": occurred_at,
        **({"actor": actor} if actor else {}),
        "links": [link for link in (links or []) if link is not None],
        "attributes": attributes or {},
    }


def _phase(status: str) -> str:
    if status == "accepted":
        return "succeeded"
    if status == "failed":
        return "failed"
    if status in {"brd_needs_input", "awaiting_brd_merge"}:
        return "waiting"
    return "running"


def program_activity(
    program: dict,
    delivery: dict | None,
    config: ControllerConfig,
    deployment: dict | None = None,
    journal: list[dict] | None = None,
) -> dict:
    """Map one authoritative program snapshot to the generic activity envelope."""
    created = int(program["created_at"])
    updated = max(
        int(program["updated_at"]),
        int(deployment["updated_at"]) if deployment is not None else 0,
    )
    repository = str(program["repository"])
    issue_uri = str(program["issue_uri"])
    status = str(program["status"])
    status_label = status.replace("_", " ").capitalize()
    events = [
        _event(
            "program-created",
            "work.created",
            "Product program opened",
            _time(created, created),
            phase="queued",
            summary="The source controller accepted the root issue as a new program.",
            links=[_link("issue", "Root issue", issue_uri)],
        )
    ]
    if delivery and delivery.get("question_digest"):
        events.append(
            _event(
                "clarification-requested",
                "decision.requested",
                "Clarification requested",
                _time(int(delivery["updated_at"]), updated),
                phase="waiting",
                summary="The source ended the bounded attempt and requested a fresh human answer.",
                links=[_link("issue", "Question and answer", issue_uri)],
            )
        )
    if delivery and int(delivery.get("answer_count") or 0) > 0:
        events.append(
            _event(
                "clarification-received",
                "decision.received",
                "Clarification received",
                _time(int(delivery["updated_at"]), updated),
                phase="running",
                summary="An authorized responder supplied the missing product decision.",
                links=[_link("issue", "Clarification", issue_uri)],
            )
        )
    brd = program["brd"]
    if brd.get("pr_uri"):
        events.append(
            _event(
                "brd-published",
                "proposal.published",
                "BRD published for review",
                _time(brd.get("approved_at"), updated),
                phase="waiting" if not brd.get("approved_commit") else "running",
                summary="Factory verified and published a draft; merge remained a human gate.",
                links=[_link("pull-request", "BRD pull request", brd.get("pr_uri"))],
                attributes={"head_commit": str(brd.get("head_commit") or "")},
            )
        )
    if brd.get("approved_commit"):
        events.append(
            _event(
                "brd-approved",
                "decision.approved",
                "BRD approved by merge",
                _time(brd.get("approved_at"), updated),
                phase="succeeded",
                actor=str(brd.get("approved_by") or ""),
                links=[
                    _link("pull-request", "Merged BRD", brd.get("pr_uri")),
                    _link(
                        "commit",
                        "Approved commit",
                        f"{repository}/commit/{brd['approved_commit']}",
                    ),
                ],
                attributes={"commit": str(brd["approved_commit"])},
            )
        )
    if program.get("plan_digest"):
        events.append(
            _event(
                "plan-validated",
                "plan.validated",
                "Dependency plan validated",
                _time(brd.get("approved_at"), updated),
                phase="succeeded",
                summary="The source independently parsed a bounded acyclic plan.",
                attributes={"digest": str(program["plan_digest"])},
            )
        )
    for feature in program.get("features", []):
        feature_id = str(feature["id"])
        links = [
            _link("issue", "Feature issue", feature.get("issue_uri")),
            _link("pull-request", "Feature pull request", feature.get("pr_uri")),
        ]
        if feature.get("issue_uri"):
            events.append(
                _event(
                    f"feature-{feature_id}-published",
                    "work.published",
                    f"{feature['title']} published",
                    _time(None, updated),
                    phase="waiting" if feature.get("dependencies") else "queued",
                    summary=(
                        "Dependencies: " + ", ".join(feature["dependencies"])
                        if feature.get("dependencies")
                        else "No feature dependencies."
                    ),
                    links=links,
                    attributes={"feature": feature_id},
                )
            )
        if feature.get("pr_uri"):
            events.append(
                _event(
                    f"feature-{feature_id}-verified",
                    "change.verified",
                    f"{feature_id} candidate verified",
                    _time(None, updated),
                    phase="waiting" if not feature.get("merged_commit") else "running",
                    summary="Factory published a draft change for independent human review.",
                    links=links,
                    attributes={"head_commit": str(feature.get("head_commit") or "")},
                )
            )
        if feature.get("merged_commit"):
            events.append(
                _event(
                    f"feature-{feature_id}-merged",
                    "change.merged",
                    f"{feature_id} merged",
                    _time(None, updated),
                    phase="succeeded",
                    links=[
                        *links,
                        _link(
                            "commit",
                            "Merged commit",
                            f"{repository}/commit/{feature['merged_commit']}",
                        ),
                    ],
                    attributes={"commit": str(feature["merged_commit"])},
                )
            )
    acceptance = program.get("acceptance")
    if acceptance and acceptance.get("accepted") is True:
        assembled = str(acceptance["assembled_commit"])
        events.append(
            _event(
                "program-accepted",
                "work.accepted",
                "Exact assembled commit accepted",
                _time(None, updated),
                phase="succeeded",
                summary="Authority-stripped acceptance verified the assembled product.",
                links=[
                    _link("commit", "Accepted commit", f"{repository}/commit/{assembled}")
                ],
                attributes={"commit": assembled},
            )
        )
    if deployment is not None and deployment.get("result"):
        deployed = deployment["result"]
        events.append(
            _event(
                "product-deployed",
                "service.deployed",
                "Accepted product deployed",
                _time(int(deployment["updated_at"]), updated),
                phase="succeeded",
                summary="The source-side runner independently verified the public endpoint.",
                links=[
                    _link("endpoint", "Open application", deployed.get("endpoint"))
                ],
                attributes={
                    "deployment_id": str(deployed.get("deployment_id") or ""),
                    "session_name": str(deployed.get("session_name") or ""),
                },
            )
        )
    if program.get("error"):
        events.append(
            _event(
                "program-failed",
                "work.failed",
                "Program failed",
                _time(None, updated),
                phase="failed",
                summary=str(program["error"])[:1000],
                links=[_link("issue", "Root issue", issue_uri)],
            )
        )

    links = [
        _link("repository", "Repository", repository),
        _link("issue", "Root issue", issue_uri),
        _link("pull-request", "BRD pull request", brd.get("pr_uri")),
    ]
    if config.project_enabled:
        owner_kind = "orgs" if config.github_project_owner_kind == "organization" else "users"
        links.append(
            _link(
                "project",
                "Project",
                f"https://github.com/{owner_kind}/{config.project_owner}/projects/{config.github_project_number}",
            )
        )
    for feature in program.get("features", []):
        links.extend(
            [
                _link("issue", f"{feature['id']} issue", feature.get("issue_uri")),
                _link("pull-request", f"{feature['id']} PR", feature.get("pr_uri")),
            ]
        )
    if deployment is not None and deployment.get("result"):
        links.append(
            _link(
                "endpoint",
                "Generated application",
                deployment["result"].get("endpoint"),
            )
        )

    artifacts: list[dict[str, Any]] = []
    for identity, kind, label, digest, url in (
        ("brd", "product-requirements", "Approved BRD", brd.get("digest"), brd.get("pr_uri")),
        ("plan", "dependency-plan", "Feature plan", program.get("plan_digest"), None),
    ):
        if digest:
            artifacts.append(
                {
                    "id": identity,
                    "kind": kind,
                    "label": label,
                    "digest": digest,
                    **({"url": url} if url else {}),
                }
            )
    if acceptance:
        assembled = str(acceptance.get("assembled_commit") or "")
        if assembled:
            artifacts.append(
                {
                    "id": "accepted-source",
                    "kind": "git-commit",
                    "label": "Accepted source",
                    "url": f"{repository}/commit/{assembled}",
                }
            )
        acceptance_raw = (
            json.dumps(
                acceptance, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            + b"\n"
        )
        artifacts.append(
            {
                "id": "acceptance-result",
                "kind": "acceptance-result",
                "label": "Acceptance result",
                "digest": "sha256:" + hashlib.sha256(acceptance_raw).hexdigest(),
            }
        )
        command_digest = acceptance.get("command_digest")
        if isinstance(command_digest, str):
            artifacts.append(
                {
                    "id": "acceptance-command",
                    "kind": "command-identity",
                    "label": "Acceptance command",
                    "digest": command_digest,
                }
            )
    if deployment is not None and deployment.get("result"):
        deployed_digest = deployment["result"].get("image_digest")
        if isinstance(deployed_digest, str):
            artifacts.append(
                {
                    "id": "deployed-image",
                    "kind": "oci-image",
                    "label": "Deployed image",
                    "digest": deployed_digest,
                    "url": deployment["result"].get("endpoint"),
                }
            )

    journal_ids = {item["id"] for item in (journal or [])}
    journal_types = {item["type"] for item in (journal or [])}
    projected_events = [
        *(journal or []),
        *(
            item
            for item in events
            if item["id"] not in journal_ids
            and not (
                item["type"] in {"decision.requested", "decision.received", "work.failed"}
                and item["type"] in journal_types
            )
        ),
    ]
    projected_events.sort(key=lambda item: (item["occurred_at"], item["id"]))

    return {
        "schema_version": "v1alpha1",
        "source": {
            "id": "software-factory",
            "label": "Software Factory",
            **({"url": config.activity_source_url} if config.activity_source_url else {}),
        },
        "kind": "product-program",
        "title": f"{repository.rsplit('/', 1)[-1]} · Program #{program['issue_number']}",
        "summary": "Product work assembled through bounded clarification, human approval, dependency-gated changes, and independent acceptance.",
        "phase": _phase(status),
        "status_label": status_label,
        "started_at": _time(created, created),
        "updated_at": _time(updated, updated),
        **(
            {"completed_at": _time(updated, updated)}
            if status in {"accepted", "failed"}
            else {}
        ),
        "links": [link for link in links if link is not None],
        "artifacts": artifacts,
        "events": projected_events[:100],
        "actions": [
            {
                "id": "deploy",
                "label": "Deploy",
                "description": (
                    "The accepted artifact is deployed and its endpoint was verified."
                    if deployment is not None
                    else (
                        "Request verified deployment of the accepted artifact."
                        if config.activity_deploy_enabled
                        else "A trusted deployment runner has not been configured for this source."
                    )
                ),
                "available": bool(
                    config.activity_deploy_enabled
                    and status == "accepted"
                    and deployment is None
                ),
                "confirmation": "Request deployment of the exact accepted commit?",
            }
        ]
        if status == "accepted"
        else [],
    }


class DeploymentRunner:
    """Run one fixed, operator-installed deployment adapter with bounded I/O."""

    def __init__(self, command: tuple[str, ...], timeout_seconds: int) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def deploy(self, request_id: str, program: dict) -> dict:
        acceptance = program.get("acceptance") or {}
        payload = {
            "schema_version": "v1alpha1",
            "operation_id": request_id,
            "program_id": program["program_id"],
            "repository": program["repository"],
            "accepted_commit": acceptance.get("assembled_commit"),
            "acceptance": acceptance,
        }
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode() + b"\n"
        if len(raw) > _MAX_RESPONSE:
            raise ValueError("deployment request exceeded 64 KiB")
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(  # noqa: S603 - fixed trusted operator argv
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    **({"HOME": os.environ["HOME"]} if "HOME" in os.environ else {}),
                },
            )
            try:
                process.communicate(input=raw, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise TimeoutError("deployment adapter exceeded its bounded timeout") from exc
            stdout.seek(0, os.SEEK_END)
            stderr.seek(0, os.SEEK_END)
            if stdout.tell() > _MAX_RESPONSE or stderr.tell() > _MAX_RESPONSE:
                raise ValueError("deployment adapter output exceeded 64 KiB")
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read()
            error = " ".join(stderr.read().decode(errors="replace").split())[:1000]
        if process.returncode != 0:
            raise RuntimeError(
                f"deployment adapter exited {process.returncode}"
                + (f": {error}" if error else "")
            )
        try:
            result = json.loads(output)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("deployment adapter returned invalid JSON") from exc
        return _deployment_result(result, request_id)


def _deployment_result(value: Any, request_id: str) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "operation_id",
        "deployment_id",
        "endpoint",
        "image_digest",
        "session_name",
    }:
        raise ValueError("deployment result has an invalid shape")
    if value["schema_version"] != "v1alpha1" or value["operation_id"] != request_id:
        raise ValueError("deployment result changed operation identity")
    if not isinstance(value["deployment_id"], str) or _ID.fullmatch(
        value["deployment_id"]
    ) is None:
        raise ValueError("deployment identity is invalid")
    if not isinstance(value["session_name"], str) or _ID.fullmatch(
        value["session_name"]
    ) is None:
        raise ValueError("deployment session identity is invalid")
    if not isinstance(value["image_digest"], str) or _DIGEST.fullmatch(
        value["image_digest"]
    ) is None:
        raise ValueError("deployment image digest is invalid")
    endpoint = value["endpoint"]
    parsed = urlparse(endpoint) if isinstance(endpoint, str) else None
    if (
        parsed is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("deployment endpoint must be credential-free HTTPS")
    return {
        "message": "Deployment verified healthy by the source runner.",
        "links": [{"rel": "endpoint", "label": "Open application", "url": endpoint}],
        "artifacts": [
            {
                "id": "deployed-image",
                "kind": "oci-image",
                "label": "Deployed image",
                "digest": value["image_digest"],
            }
        ],
        "deployment_id": value["deployment_id"],
        "session_name": value["session_name"],
        "endpoint": endpoint,
        "image_digest": value["image_digest"],
    }


class ActivityPublisher:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=10.0
        )

    def publish(self, program_id: str, document: dict) -> None:
        response = self._client.put(
            f"{self.endpoint}/v1/activity/streams/{program_id}", json=document
        )
        if len(response.content) > _MAX_RESPONSE:
            raise ValueError("activity API response exceeded 64 KiB")
        response.raise_for_status()
        payload = response.json()
        if payload.get("stream_id") != program_id:
            raise ValueError("activity API changed stream identity")

    def action_requests(self, state: str) -> list[dict]:
        response = self._client.get(
            f"{self.endpoint}/v1/activity/action-requests",
            params={"state": state, "limit": 100},
        )
        if len(response.content) > _MAX_RESPONSE:
            raise ValueError("activity API response exceeded 64 KiB")
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("activity API returned invalid action requests")
        return items

    def resolve_action(
        self,
        request_id: str,
        source_id: str,
        state: str,
        *,
        message: str | None = None,
        links: list[dict] | None = None,
        artifacts: list[dict] | None = None,
    ) -> None:
        response = self._client.post(
            f"{self.endpoint}/v1/activity/action-requests/{request_id}/resolve",
            json={
                "source_id": source_id,
                "state": state,
                "message": message,
                "links": links or [],
                "artifacts": artifacts or [],
            },
        )
        if len(response.content) > _MAX_RESPONSE:
            raise ValueError("activity API response exceeded 64 KiB")
        response.raise_for_status()
        if response.json().get("request_id") != request_id:
            raise ValueError("activity API changed action request identity")

    def close(self) -> None:
        self._client.close()


def content_digest(document: dict) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
