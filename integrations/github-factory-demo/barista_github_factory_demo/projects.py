"""Non-authoritative GitHub Projects v2 projection.

The adapter only writes controller-derived presentation fields. It never reads a
Project field as workflow input or authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

_ID = re.compile(r"^[A-Za-z0-9_:\-=]{1,256}$")


class ProjectProjectionError(RuntimeError):
    """A deliberately non-sensitive Projects API failure."""


@dataclass(frozen=True)
class ProjectProjection:
    item_id: str
    status: str


class Projector(Protocol):
    def sync(
        self, issue_uri: str, status: str, details: dict | None = None
    ) -> ProjectProjection: ...

    def close(self) -> None: ...


class GitHubProjector:
    """Project one issue into one configured Projects v2 board."""

    def __init__(
        self,
        *,
        token: str,
        owner: str,
        owner_kind: str,
        project_number: int,
        status_field: str,
        status_options: dict[str, str],
        endpoint: str = "https://api.github.com/graphql",
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ):
        if not token:
            raise ValueError("project token is required")
        if owner_kind not in {"user", "organization"}:
            raise ValueError("project owner kind is invalid")
        if not (1 <= project_number <= 10000):
            raise ValueError("project number is outside the supported bound")
        if not status_field or len(status_field) > 64:
            raise ValueError("project status field is invalid")
        self._owner = owner
        self._owner_kind = owner_kind
        self._number = project_number
        self._status_field = status_field
        self._status_options = dict(status_options)
        self._owned_client = client is None
        self._endpoint = endpoint
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "barista-github-factory-demo",
            },
        )

    def close(self) -> None:
        if self._owned_client:
            self._client.close()

    def sync(
        self, issue_uri: str, status: str, details: dict | None = None
    ) -> ProjectProjection:
        desired_option = self._status_options.get(status)
        if desired_option is None:
            raise ProjectProjectionError("canonical status has no project mapping")
        document = self._resolve(issue_uri)
        project = document.get(self._owner_kind)
        if not isinstance(project, dict) or not isinstance(
            project.get("projectV2"), dict
        ):
            raise ProjectProjectionError("configured GitHub Project was not found")
        project = project["projectV2"]
        project_id = self._id(project.get("id"), "project")
        resource = document.get("resource")
        if not isinstance(resource, dict):
            raise ProjectProjectionError("projected issue was not found")
        content_id = self._id(resource.get("id"), "issue")

        field_id = None
        option_id = None
        fields_by_name: dict[str, dict] = {}
        fields = project.get("fields", {}).get("nodes", [])
        if not isinstance(fields, list) or len(fields) > 100:
            raise ProjectProjectionError("project field response is invalid")
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                continue
            fields_by_name[field["name"]] = field
            if field.get("name") != self._status_field:
                continue
            field_id = self._id(field.get("id"), "status field")
            options = field.get("options")
            if not isinstance(options, list) or len(options) > 100:
                raise ProjectProjectionError("project status options are invalid")
            for option in options:
                if isinstance(option, dict) and option.get("name") == desired_option:
                    option_id = self._id(option.get("id"), "status option")
                    break
        if field_id is None or option_id is None:
            raise ProjectProjectionError(
                "configured project status mapping was not found"
            )

        item_id = None
        items = project.get("items", {}).get("nodes", [])
        if not isinstance(items, list) or len(items) > 100:
            raise ProjectProjectionError("project item response is invalid")
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, dict) and content.get("url") == issue_uri:
                item_id = self._id(item.get("id"), "project item")
                break
        if item_id is None:
            added = self._graphql(
                """mutation AddProjectItem($project: ID!, $content: ID!) {
                  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
                    item { id }
                  }
                }""",
                {"project": project_id, "content": content_id},
            )
            item = added.get("addProjectV2ItemById", {}).get("item")
            if not isinstance(item, dict):
                raise ProjectProjectionError("project item creation was not confirmed")
            item_id = self._id(item.get("id"), "project item")

        self._graphql(
            """mutation UpdateProjectStatus(
              $project: ID!, $item: ID!, $field: ID!, $option: String!
            ) {
              updateProjectV2ItemFieldValue(input: {
                projectId: $project,
                itemId: $item,
                fieldId: $field,
                value: {singleSelectOptionId: $option}
              }) { projectV2Item { id } }
            }""",
            {
                "project": project_id,
                "item": item_id,
                "field": field_id,
                "option": option_id,
            },
        )
        if details:
            self._update_details(project_id, item_id, fields_by_name, details)
        return ProjectProjection(item_id=item_id, status=status)

    def _resolve(self, issue_uri: str) -> dict:
        root = "user" if self._owner_kind == "user" else "organization"
        return self._graphql(
            f"""query ResolveProject($owner: String!, $number: Int!, $url: URI!) {{
              {root}(login: $owner) {{
                projectV2(number: $number) {{
                  id
                  fields(first: 100) {{ nodes {{
                    ... on ProjectV2Field {{ id name dataType }}
                    ... on ProjectV2SingleSelectField {{ id name options {{ id name }} }}
                  }} }}
                  items(first: 100) {{ nodes {{
                    id
                    content {{ ... on Issue {{ id url }} }}
                  }} }}
                }}
              }}
              resource(url: $url) {{ ... on Issue {{ id url }} }}
            }}""",
            {"owner": self._owner, "number": self._number, "url": issue_uri},
        )

    def _update_details(
        self,
        project_id: str,
        item_id: str,
        fields: dict[str, dict],
        details: dict,
    ) -> None:
        names = {
            "work_type": "Work Type",
            "program": "Program",
            "feature": "Feature",
            "attempt": "Attempt",
            "dependency": "Dependency",
            "result": "Result",
            "pr": "PR",
        }
        if set(details) - set(names):
            raise ProjectProjectionError("project details contain unknown fields")
        for key, value in details.items():
            if value is None:
                continue
            field = fields.get(names[key])
            if field is None:
                raise ProjectProjectionError(
                    "configured presentation field was not found"
                )
            selected_field = self._id(field.get("id"), "presentation field")
            if key == "work_type":
                if not isinstance(value, str) or value not in {
                    "Program",
                    "BRD",
                    "Feature",
                }:
                    raise ProjectProjectionError("project work type is invalid")
                options = field.get("options")
                selected_option = next(
                    (
                        option.get("id")
                        for option in options or []
                        if isinstance(option, dict) and option.get("name") == value
                    ),
                    None,
                )
                option_id = self._id(selected_option, "presentation option")
                self._graphql(
                    """mutation UpdateProjectSelect($project: ID!, $item: ID!, $field: ID!, $option: String!) {
                      updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}) { projectV2Item { id } }
                    }""",
                    {
                        "project": project_id,
                        "item": item_id,
                        "field": selected_field,
                        "option": option_id,
                    },
                )
            elif key == "attempt":
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not 1 <= value <= 100
                ):
                    raise ProjectProjectionError("project attempt is invalid")
                self._graphql(
                    """mutation UpdateProjectNumber($project: ID!, $item: ID!, $field: ID!, $number: Float!) {
                      updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {number: $number}}) { projectV2Item { id } }
                    }""",
                    {
                        "project": project_id,
                        "item": item_id,
                        "field": selected_field,
                        "number": float(value),
                    },
                )
            else:
                if not isinstance(value, str) or len(value) > 500:
                    raise ProjectProjectionError("project text value is invalid")
                self._graphql(
                    """mutation UpdateProjectText($project: ID!, $item: ID!, $field: ID!, $text: String!) {
                      updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {text: $text}}) { projectV2Item { id } }
                    }""",
                    {
                        "project": project_id,
                        "item": item_id,
                        "field": selected_field,
                        "text": value,
                    },
                )

    def _graphql(self, query: str, variables: dict) -> dict:
        try:
            response = self._client.post(
                self._endpoint, json={"query": query, "variables": variables}
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProjectProjectionError("GitHub Projects request failed") from exc
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ProjectProjectionError("GitHub Projects operation was refused")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProjectProjectionError("GitHub Projects response was invalid")
        return data

    @staticmethod
    def _id(value: object, label: str) -> str:
        if not isinstance(value, str) or _ID.fullmatch(value) is None:
            raise ProjectProjectionError(f"{label} identity was invalid")
        return value
