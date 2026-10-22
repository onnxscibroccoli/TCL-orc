import pytest

from tcl.orchestrator import IllegalTransition, Task, TaskState as S

HAPPY = [S.ALLOCATED, S.EXECUTING, S.VERIFYING, S.MERGED]


def test_happy_path():
    t = Task("t", "b")
    for s in HAPPY:
        t.transition(s)
    assert t.state is S.MERGED and t.terminal
    assert [h[0] for h in t.history] == ["PENDING", "ALLOCATED", "EXECUTING", "VERIFYING"]


@pytest.mark.parametrize("n", range(4))
def test_fail_from_any_nonterminal(n):
    t = Task("t", "b")
    for s in HAPPY[:n]:
        t.transition(s)
    t.transition(S.FAILED, error="x")
    assert t.terminal and t.error == "x"


ILLEGAL = [(S.PENDING, S.EXECUTING), (S.PENDING, S.MERGED), (S.ALLOCATED, S.VERIFYING),
           (S.EXECUTING, S.MERGED), (S.VERIFYING, S.EXECUTING), (S.MERGED, S.FAILED),
           (S.FAILED, S.PENDING), (S.EXECUTING, S.ALLOCATED), (S.PENDING, S.PENDING)]


@pytest.mark.parametrize("frm,to", ILLEGAL)
def test_illegal_rejected(frm, to):
    t = Task("t", "b", state=frm)
    with pytest.raises(IllegalTransition):
        t.transition(to)
    assert t.state is frm


def test_roundtrip_dict():
    t = Task("t", "b")
    t.transition(S.ALLOCATED)
    assert Task.from_dict(t.to_dict()) == t
