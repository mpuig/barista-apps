"""Least-scope bootstrap for the optional presentation project."""

from __future__ import annotations

import json
import re

import httpx

from .projects import ProjectProjectionError

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_FIELDS = (
    ("Program", "TEXT"),
    ("Feature", "TEXT"),
    ("Attempt", "NUMBER"),
    ("Dependency", "TEXT"),
    ("Result", "TEXT"),
    ("PR", "TEXT"),
)


def setup_project(
    *,
    token: str,
    owner: str,
    owner_kind: str,
    title: str,
    project_number: int | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """Create or validate one project and its bounded presentation fields."""
    if not token or _LOGIN.fullmatch(owner) is None:
        raise ValueError("project token and owner are required")
    if owner_kind not in {"user", "organization"}:
        raise ValueError("project owner kind is invalid")
    if not title.strip() or len(title) > 100:
        raise ValueError("project title is invalid")
    if project_number is not None and not 1 <= project_number <= 10000:
        raise ValueError("project number is outside the supported bound")

    owned = client is None
    endpoint = "https://api.github.com/graphql"
    api = client or httpx.Client(
        timeout=15,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "barista-github-factory-demo-bootstrap",
        },
    )

    def graphql(query: str, variables: dict) -> dict:
        try:
            response = api.post(endpoint, json={"query": query, "variables": variables})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProjectProjectionError(
                "GitHub Projects setup request failed"
            ) from exc
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ProjectProjectionError("GitHub Projects setup was refused")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProjectProjectionError("GitHub Projects setup response was invalid")
        return data

    try:
        root = "user" if owner_kind == "user" else "organization"
        selected = graphql(
            f"""query ProjectOwner($owner: String!) {{
              {root}(login: $owner) {{ id }}
            }}""",
            {"owner": owner},
        ).get(root)
        if not isinstance(selected, dict) or not isinstance(selected.get("id"), str):
            raise ProjectProjectionError("GitHub Project owner was not found")
        project = None
        created = False
        if project_number is None:
            data = graphql(
                """mutation CreateProject($owner: ID!, $title: String!) {
                  createProjectV2(input: {ownerId: $owner, title: $title}) {
                    projectV2 { id number title url fields(first: 100) { nodes {
                      ... on ProjectV2Field { id name }
                      ... on ProjectV2SingleSelectField { id name }
                    } } }
                  }
                }""",
                {"owner": selected["id"], "title": title},
            )
            project = data.get("createProjectV2", {}).get("projectV2")
            created = True
        else:
            project = (
                graphql(
                    f"""query ExistingProject($owner: String!, $number: Int!) {{
                  {root}(login: $owner) {{
                    projectV2(number: $number) {{
                      id number title url
                      fields(first: 100) {{ nodes {{
                        ... on ProjectV2Field {{ id name }}
                        ... on ProjectV2SingleSelectField {{ id name }}
                      }} }}
                    }}
                  }}
                }}""",
                    {"owner": owner, "number": project_number},
                )
                .get(root, {})
                .get("projectV2")
            )
        if not isinstance(project, dict) or not isinstance(project.get("id"), str):
            raise ProjectProjectionError("configured GitHub Project was not found")
        fields = project.get("fields", {}).get("nodes", [])
        if not isinstance(fields, list) or len(fields) > 100:
            raise ProjectProjectionError("GitHub Project fields were invalid")
        names = {
            field.get("name")
            for field in fields
            if isinstance(field, dict) and isinstance(field.get("name"), str)
        }
        created_fields: list[str] = []
        if "Work Type" not in names:
            graphql(
                """mutation CreateTypeField($project: ID!) {
                  createProjectV2Field(input: {
                    projectId: $project,
                    dataType: SINGLE_SELECT,
                    name: "Work Type",
                    singleSelectOptions: [
                      {name: "Program", color: PURPLE, description: "Product program"},
                      {name: "BRD", color: BLUE, description: "Product brief approval"},
                      {name: "Feature", color: GREEN, description: "Planned feature"}
                    ]
                  }) { projectV2Field {
                    ... on ProjectV2SingleSelectField { id name }
                  } }
                }""",
                {"project": project["id"]},
            )
            created_fields.append("Work Type")
        for name, data_type in _FIELDS:
            if name in names:
                continue
            graphql(
                f"""mutation CreatePresentationField($project: ID!) {{
                  createProjectV2Field(input: {{
                    projectId: $project, dataType: {data_type}, name: {json.dumps(name)}
                  }}) {{ projectV2Field {{ ... on ProjectV2Field {{ id name }} }} }}
                }}""",
                {"project": project["id"]},
            )
            created_fields.append(name)
        return {
            "created": created,
            "owner": owner,
            "owner_kind": owner_kind,
            "number": int(project["number"]),
            "title": str(project["title"]),
            "url": str(project["url"]),
            "created_fields": created_fields,
        }
    finally:
        if owned:
            api.close()
