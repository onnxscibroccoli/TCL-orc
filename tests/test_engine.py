import sys
import threading
import time

import pytest

from tcl.orchestrator.engine import (CycleError, DAGError, Engine, Limits, TaskSpec, TaskStore,
                                     detect_cycle)
from tcl.orchestrator.state import TaskState

PY = sys.executable


def sh(code):
    return ["sh", "-c", code]


def test_detect_cycle():
    assert detect_cycle({"a": ["b"], "b": []}) is None
    assert detect_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) is not None


def test_dag_ordering(tmp_path):
    log = tmp_path / "order"
    e = Engine(workers=4, workdir=tmp_path)
    mk = lambda i, d=(): TaskSpec(i, sh(f"echo {i} >> {log}"), depends=list(d))
    res = e.run([mk("d", ["b", "c"]), mk("b", ["a"]), mk("c", ["a"]), mk("a")], timeout=20)
    assert set(res.values()) == {"MERGED"}
    order = log.read_text().split()
    assert order[0] == "a" and order[-1] == "d" and set(order[1:3]) == {"b", "c"}
    # every task walked the full state machine
    hist = [h[0] for h in e.tasks["d"].history]
    assert hist == ["PENDING", "ALLOCATED", "EXECUTING", "VERIFYING"]


def test_cycle_rejected_and_not_persisted(tmp_path):
    store = TaskStore(tmp_path / "t.db")
    e = Engine(store, workdir=tmp_path)
    with pytest.raises(CycleError):
        e.submit([TaskSpec("a", ["true"], ["b"]), TaskSpec("b", ["true"], ["a"])])
    with pytest.raises(CycleError):
        TaskSpec("s", ["true"], ["s"])
    with pytest.raises(DAGError):
        e.submit(TaskSpec("x", ["true"], ["missing"]))
    assert e.tasks == {} and store.load() == []


def test_invalid_specs():
    with pytest.raises(DAGError):
        TaskSpec("bad id", ["true"])
    with pytest.raises(DAGError):
        TaskSpec("a", "true")
    with pytest.raises(DAGError):
        TaskSpec.from_dict({"task_id": "a", "command": ["true"], "evil": 1})


def test_timeout_fails_and_dependents_fail(tmp_path):
    e = Engine(workdir=tmp_path)
    t0 = time.monotonic()
    res = e.run([TaskSpec("slow", ["sleep", "30"], timeout=0.5),
                 TaskSpec("after", ["true"], ["slow"])], timeout=20)
    assert time.monotonic() - t0 < 10
    assert res == {"slow": "FAILED", "after": "FAILED"}
    assert "timeout" in e.tasks["slow"].error
    assert e.tasks["after"].error == "dependency_failed"


def test_nonzero_exit_fails(tmp_path):
    e = Engine(workdir=tmp_path)
    assert e.run([TaskSpec("x", ["false"])], timeout=10) == {"x": "FAILED"}


def test_rlimit_memory_enforced(tmp_path):
    lim = Limits(memory_bytes=256 << 20)
    e = Engine(workdir=tmp_path)
    res = e.run([TaskSpec("hog", [PY, "-c", "b = bytearray(1 << 30)"], limits=lim),
                 TaskSpec("ok", [PY, "-c", "b = bytearray(1 << 20)"], limits=lim)], timeout=30)
    assert res == {"hog": "FAILED", "ok": "MERGED"}


def test_rlimit_cpu_enforced(tmp_path):
    e = Engine(workdir=tmp_path)
    res = e.run([TaskSpec("spin", [PY, "-c", "while True: pass"], timeout=30,
                          limits=Limits(cpu_seconds=1))], timeout=30)
    assert res == {"spin": "FAILED"} and "exit" in e.tasks["spin"].error


def test_concurrency(tmp_path):
    e = Engine(workers=4, workdir=tmp_path)
    t0 = time.monotonic()
    res = e.run([TaskSpec(f"t{i}", ["sleep", "1"]) for i in range(4)], timeout=20)
    assert set(res.values()) == {"MERGED"}
    assert time.monotonic() - t0 < 3.0  # serial would take >= 4s


def test_concurrent_submitters(tmp_path):
    e = Engine(workers=4, workdir=tmp_path).start()
    errs = []

    def sub(k):
        try:
            for j in range(10):
                e.submit(TaskSpec(f"k{k}-{j}", ["true"]))
        except Exception as ex:  # pragma: no cover
            errs.append(ex)
    ths = [threading.Thread(target=sub, args=(k,)) for k in range(5)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert not errs and e.wait(timeout=30)
    assert len(e.tasks) == 50 and set(e.status_map().values()) == {"MERGED"}
    e.stop()


def test_persistence_and_recovery(tmp_path):
    db = tmp_path / "tasks.db"
    e = Engine(TaskStore(db), workdir=tmp_path)
    e.run([TaskSpec("a", ["true"])], timeout=10)
    e.submit(TaskSpec("b", ["true"], ["a"]))  # queued but engine never started workers? (a done)
    e.stop()
    # simulate a crash mid-flight for c
    e2 = Engine(TaskStore(db), workdir=tmp_path)
    assert e2.tasks["a"].state is TaskState.MERGED
    e2.tasks["b"]  # recovered
    e2.store.put(TaskSpec("c", ["true"]), e2.tasks["a"].__class__("c", "task/c"), 99)
    t = e2.tasks["a"].__class__("c", "task/c")
    t.transition(TaskState.ALLOCATED); t.transition(TaskState.EXECUTING)
    e2.store.save(t)
    e3 = Engine(TaskStore(db), workdir=tmp_path).start()
    assert e3.tasks["c"].state is TaskState.FAILED and e3.tasks["c"].error == "engine_restart"
    assert e3.wait(["b"], timeout=10) and e3.tasks["b"].state is TaskState.MERGED
    e3.stop()


def test_env_is_sandboxed(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "s3cret")
    out = tmp_path / "env"
    e = Engine(workdir=tmp_path)
    e.run([TaskSpec("e", sh(f"env > {out}"))], timeout=10)
    assert "s3cret" not in out.read_text() and "TCL_TASK_ID=e" in out.read_text()
