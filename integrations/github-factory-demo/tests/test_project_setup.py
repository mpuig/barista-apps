from __future__ import annotations

import json

import httpx

from barista_github_factory_demo.project_setup import setup_project


def test_existing_project_with_presentation_fields_is_reused_without_mutation():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        document = json.loads(request.content)
        requests.append(document)
        if "ProjectOwner" in document["query"]:
            return httpx.Response(200, json={"data": {"user": {"id": "U_owner"}}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "user": {
                        "projectV2": {
                            "id": "PVT_project",
                            "number": 4,
                            "title": "Barista product program",
                            "url": "https://github.com/users/acme/projects/4",
                            "fields": {
                                "nodes": [
                                    {"id": f"field-{name}", "name": name}
                                    for name in (
                                        "Status",
                                        "Type",
                                        "Program",
                                        "Feature",
                                        "Attempt",
                                        "Dependency",
                                        "Result",
                                        "PR",
                                    )
                                ]
                            },
                        }
                    }
                }
            },
        )

    result = setup_project(
        token="project-token",
        owner="acme",
        owner_kind="user",
        title="Barista product program",
        project_number=4,
        client=httpx.Client(
            base_url="https://api.github.test/graphql",
            transport=httpx.MockTransport(handler),
        ),
    )

    assert result["created"] is False
    assert result["number"] == 4
    assert result["created_fields"] == []
    assert len(requests) == 2
    assert "project-token" not in repr(requests)


def test_new_project_receives_bounded_demo_fields():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        document = json.loads(request.content)
        requests.append(document)
        query = document["query"]
        if "ProjectOwner" in query:
            return httpx.Response(
                200, json={"data": {"organization": {"id": "O_owner"}}}
            )
        if "CreateProject" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "createProjectV2": {
                            "projectV2": {
                                "id": "PVT_project",
                                "number": 8,
                                "title": "Program",
                                "url": "https://github.com/orgs/acme/projects/8",
                                "fields": {
                                    "nodes": [{"id": "status", "name": "Status"}]
                                },
                            }
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={"data": {"createProjectV2Field": {"projectV2Field": {"id": "f"}}}},
        )

    result = setup_project(
        token="project-token",
        owner="acme",
        owner_kind="organization",
        title="Program",
        client=httpx.Client(
            base_url="https://api.github.test/graphql",
            transport=httpx.MockTransport(handler),
        ),
    )

    assert result["created"] is True
    assert result["number"] == 8
    assert result["created_fields"] == [
        "Type",
        "Program",
        "Feature",
        "Attempt",
        "Dependency",
        "Result",
        "PR",
    ]
    assert len(requests) == 9
    mutation_text = "\n".join(item["query"] for item in requests)
    assert "dataType: SINGLE_SELECT" in mutation_text
    assert 'name: "Attempt"' in mutation_text
    assert "dataType: NUMBER" in mutation_text
