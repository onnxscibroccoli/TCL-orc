"""Task state machine: PENDING → ALLOCATED → EXECUTING → VERIFYING → MERGED | FAILED."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class TaskState(str, Enum):
    PENDING = "PENDING"
    ALLOCATED = "ALLOCATED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    MERGED = "MERGED"
    FAILED = "FAILED"


TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.ALLOCATED, TaskState.FAILED}),
    TaskState.ALLOCATED: frozenset({TaskState.EXECUTING, TaskState.FAILED}),
    TaskState.EXECUTING: frozenset({TaskState.VERIFYING, TaskState.FAILED}),
    TaskState.VERIFYING: frozenset({TaskState.MERGED, TaskState.FAILED}),
    TaskState.MERGED: frozenset(),
    TaskState.FAILED: frozenset(),
}
TERMINAL = frozenset({TaskState.MERGED, TaskState.FAILED})


class IllegalTransition(ValueError):
    pass


@dataclass
class Task:
    task_id: str
    branch: str
    state: TaskState = TaskState.PENDING
    history: list[tuple[str, float]] = field(default_factory=list)
    error: str | None = None

    def transition(self, to: TaskState, error: str | None = None) -> None:
        to = TaskState(to)
        if to not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.task_id}: {self.state.value} -> {to.value} not allowed")
        self.history.append((self.state.value, time.time()))
        self.state = to
        if to is TaskState.FAILED:
            self.error = error

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "branch": self.branch, "state": self.state.value,
                "history": self.history, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(d["task_id"], d["branch"], TaskState(d["state"]),
                   [tuple(h) for h in d.get("history", [])], d.get("error"))
