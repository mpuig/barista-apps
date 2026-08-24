"""Mission schema loading, validation, and limits."""

from __future__ import annotations

import json
import posixpath
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


def _norm(path: str) -> str:
    """One spelling per path, so `/work/t.js` and `/work/./t.js` compare equal."""
    return posixpath.normpath(path)


def _looks_like_a_path(arg: str) -> bool:
    """Is this argv element naming a path in the session?

    Deliberately conservative and syntactic: absolute, or explicitly relative.
    A bare word (`node`, `true`, `pytest`) is a program resolved on PATH from the
    image, not workspace content, so it is not a path for this rule's purposes.

    **The stated limitation** (design D3, task 1.4): a path can be hidden from any
    such rule by burying it in a string the shell expands — `sh -c "node t.js"`
    has no argv element that begins with a slash. This rule is a guard against
    the mission that forges a gate by accident, which is the case that has
    actually occurred; it is not a sandbox against one that sets out to. Trying
    to parse shell strings here would trade a rule an author can predict for a
    heuristic that refuses valid missions and still misses the determined case.
    """
    return arg.startswith(("/", "./", "../"))


@dataclass
class Task:
    id: str
    command: Optional[list[str]] = None
    prompt: Optional[str] = None
    check: Optional[list[str]] = None
    collect: bool = True
    env: dict[str, str] = field(default_factory=dict)
    workdir: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    produces: dict[str, str] = field(default_factory=dict)
    consumes: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)

    def worker_command(self) -> list[str]:
        if self.command:
            return list(self.command)
        if self.prompt:
            return ["agent", "-p", self.prompt]
        raise MissionError(f"task '{self.id}' has neither command nor prompt")

    def fixed_paths(self) -> set[str]:
        """Paths in this task's session that the task itself did not author.

        Planted content and content received from a dependency. These are the
        only paths a `check` may name (design D3) — the criterion a task is
        judged by must not be written by the task being judged.
        """
        return {_norm(p) for p in self.files} | {_norm(p) for p in self.consumes.values()}


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
    strict_gates: bool = False
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
            strict_gates=data.get("strict_gates", False),
            task_timeout_s=data.get("task_timeout_s", 3600),
            max_attempts=data.get("max_attempts", 1),
            deadline_s=data.get("deadline_s"),
            budget=budget,
            permissions=data.get("permissions", {}),
            notify_url=data.get("notify_url"),
        )
        mission._enforce_budget()
        # Order matters only for the quality of the message: the graph is checked
        # first so a mission with both a cycle and a forged gate is told about the
        # cycle, which is the one that makes the rest unanalysable.
        mission._enforce_graph()
        mission._enforce_gates()
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

    def by_id(self) -> dict[str, Task]:
        return {t.id: t for t in self.tasks}

    def _enforce_graph(self) -> None:
        """Refuse a dependency graph that cannot be run, before any worker exists.

        Every refusal names the offending task and id. A message that says only
        "invalid graph" leaves the operator reading JSON by hand, which for a
        forty-task mission is the difference between a fixed typo and a rewrite.
        """
        tasks = self.by_id()
        for task in self.tasks:
            for dep in task.depends_on:
                if dep == task.id:
                    raise MissionError(f"task '{task.id}' depends on itself")
                if dep not in tasks:
                    raise MissionError(f"task '{task.id}' depends on unknown task '{dep}'")

        # Iterative DFS with an explicit stack: a mission is data from outside, so
        # its depth is not ours to trust, and recursion would turn a deep chain
        # into a RecursionError — an error about the interpreter, not about the
        # mission, arriving where a clear refusal belongs.
        WHITE, GREY, BLACK = 0, 1, 2
        colour = dict.fromkeys(tasks, WHITE)
        for root in tasks:
            if colour[root] != WHITE:
                continue
            stack: list[tuple[str, bool]] = [(root, False)]
            path: list[str] = []
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    colour[node] = BLACK
                    path.pop()
                    continue
                colour[node] = GREY
                path.append(node)
                stack.append((node, True))
                for dep in tasks[node].depends_on:
                    if colour[dep] == GREY:
                        cycle = path[path.index(dep) :] + [dep]
                        raise MissionError(
                            "dependency cycle: " + " -> ".join(cycle)
                        )
                    if colour[dep] == WHITE:
                        stack.append((dep, False))

        # A consumed output must be produced by something this task waits for.
        # Produced-by-a-non-dependency is refused too: it would be a race dressed
        # as a data flow, since nothing orders the producer before the consumer.
        for task in self.tasks:
            for name in task.consumes:
                producers = [d for d in task.depends_on if name in tasks[d].produces]
                if producers:
                    continue
                elsewhere = [t.id for t in self.tasks if name in t.produces]
                if elsewhere:
                    raise MissionError(
                        f"task '{task.id}' consumes '{name}', produced by "
                        f"{', '.join(repr(e) for e in elsewhere)} — which it does not "
                        f"depend on, so nothing orders the producer first"
                    )
                raise MissionError(
                    f"task '{task.id}' consumes '{name}', which no task produces"
                )

    def _enforce_gates(self) -> None:
        """Under `strict_gates`, refuse a check whose subject the task could have written.

        `check` is the coordinator's independent verification. A check that reads
        a path the task's own command produced is not independent — it is the
        task marking its own work, and it passes exactly when the task says so.

        **Why this is opt-in rather than always on.** An argv element that names a
        path is not necessarily the criterion. `git -C /work diff --quiet` names
        `/work` as the *location* to inspect while the criterion is git's own
        notion of a clean tree — a perfectly sound check that this rule cannot
        distinguish from `node /work/its-own-test.js`, because syntactically they
        are the same shape. This repo's own `missions/example.json` is the case in
        point: an early always-on version of this rule refused it. Refusing valid
        missions to catch invalid ones is the wrong trade for a portable app, so
        a mission that wants the guarantee asks for it, and the guarantee that
        needs no opt-in is the runtime one — planted content is re-asserted before
        the check runs, so the criterion is the mission's copy whatever the worker
        did to it.

        argv[0] is exempt. It is the program being run — `node`, `pytest`,
        `/usr/local/bin/rspec` — which comes from the worker's image, not from
        the workspace, and is no more the subject of the check than the shell is.
        """
        if not self.strict_gates:
            return
        for task in self.tasks:
            if not task.check:
                continue
            fixed = task.fixed_paths()
            for arg in task.check[1:]:
                if not _looks_like_a_path(arg):
                    continue
                if _norm(arg) in fixed:
                    continue
                raise MissionError(
                    f"task '{task.id}' has a check that reads '{arg}', which the task "
                    f"itself may write. A check must read only content the mission "
                    f"planted (`files`) or that the task received from a dependency "
                    f"(`consumes`) — otherwise the task is marking its own work"
                )
