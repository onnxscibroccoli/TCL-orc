#!/usr/bin/env python3
"""TCL orchestrator (compat entrypoint). Real implementation: ``tcl.orchestrator.engine``.

``PLAN`` and ``run()`` are kept; ``run()`` now validates the plan as a DAG
(cycles/unknown deps rejected) and prints it in topological order.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tcl.orchestrator.engine import CycleError, DAGError, detect_cycle  # noqa: E402

PLAN = {
    "tasks": [
        {"id": "task-03-math", "agent": "agent-math", "output": "docs/02_math_spec.md"},
        {"id": "task-04-tokenomics", "agent": "agent-tokenomics", "output": "docs/03_orchestration_redistribution.md"},
        {"id": "task-05-quality", "agent": "agent-qa", "depends": ["task-03-math", "task-04-tokenomics"]}
    ]
}


def topo_order(plan: dict = PLAN) -> list[str]:
    graph = {t["id"]: list(t.get("depends", [])) for t in plan["tasks"]}
    for tid, deps in graph.items():
        if missing := [d for d in deps if d not in graph]:
            raise DAGError(f"{tid}: unknown dependencies {missing}")
    if cyc := detect_cycle(graph):
        raise CycleError(" -> ".join(cyc))
    order, seen = [], set()

    def visit(n):
        if n not in seen:
            seen.add(n)
            for d in graph[n]:
                visit(d)
            order.append(n)
    for n in graph:
        visit(n)
    return order


def run():
    print(json.dumps(PLAN, indent=2))
    print(json.dumps({"order": topo_order(PLAN)}))
    print("Orchestrator skeleton ready. Assign agents to worktrees.")


if __name__ == "__main__":
    run()
