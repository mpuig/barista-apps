"""Mission schema loading, validation, and limits."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "mission.schema.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text())
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


class MissionError(ValueError):
    pass


@dataclass
class Task:
    id: str
    command: Optional[list[str]] = None
    prompt: Optional[str] = None
    check: Optional[list[str]] = None
    collect: bool = True
    env: dict[str, str] = field(default_factory=dict)
    workdir: Optional[str] = None

    def worker_command(self) -> list[str]:
        if self.command:
            return list(self.command)
        if self.prompt:
            return ["agent", "-p", self.prompt]
        raise MissionError(f"task '{self.id}' has neither command nor prompt")


@dataclass
class Budget:
    max_workers: Optional[int] = None
    max_exec_seconds: Optional[int] = None


@dataclass
class Mission:
    name: str
    app: str
    tasks: list[Task]
    concurrency: int = 3
    task_timeout_s: int = 3600
    max_attempts: int = 1
    deadline_s: Optional[int] = None
    budget: Budget = field(default_factory=Budget)
    permissions: dict[str, Any] = field(default_factory=dict)
    notify_url: Optional[str] = None

    @classmethod
    def load(cls, data: dict) -> "Mission":
        errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            loc = "/".join(str(p) for p in first.path) or "<root>"
            raise MissionError(f"mission invalid at {loc}: {first.message}")

        tasks = [Task(**t) for t in data["tasks"]]
        ids = [t.id for t in tasks]
        if len(ids) != len(set(ids)):
            raise MissionError("task ids must be unique")

        budget = Budget(**data.get("budget", {}))
        mission = cls(
            name=data["name"], app=data["app"], tasks=tasks,
            concurrency=data.get("concurrency", 3),
            task_timeout_s=data.get("task_timeout_s", 3600),
            max_attempts=data.get("max_attempts", 1),
            deadline_s=data.get("deadline_s"),
            budget=budget,
            permissions=data.get("permissions", {}),
            notify_url=data.get("notify_url"),
        )
        mission._enforce_budget()
        return mission

    @classmethod
    def load_file(cls, path: str | Path) -> "Mission":
        return cls.load(json.loads(Path(path).read_text()))

    def _enforce_budget(self) -> None:
        if self.budget.max_workers is not None and len(self.tasks) > self.budget.max_workers:
            raise MissionError(
                f"mission needs {len(self.tasks)} workers but budget.max_workers is "
                f"{self.budget.max_workers}"
            )
