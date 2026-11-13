"""Convergent and adversarial delta-CRDT test vectors (not a network test)."""
from __future__ import annotations

from copy import deepcopy
from random import Random

import pytest

from tcl.vos.sync import DeltaReplica, SyncError


def test_concurrent_lww_updates_converge_independent_of_delivery_order():
    alice, bob, carol = (DeltaReplica(n) for n in ("alice", "bob", "carol"))
    a = alice.put("cosmetic:banner", "green")
    b = bob.put("cosmetic:banner", "blue")
    # Same Lamport counter: deterministic node-ID tie-breaker.
    for packet in (b, a):
        carol.merge(packet)
    for packet in (a, b):
        alice.merge(packet)
        bob.merge(packet)
    assert alice.snapshot() == bob.snapshot() == carol.snapshot()
    assert alice.snapshot()["register"]["cosmetic:banner"] == "blue"
    assert carol.merge(a) is False
    assert carol.merge(b) is False


def test_observed_remove_set_does_not_resurrect_deleted_elements():
    a, b, c = (DeltaReplica(n) for n in ("alice", "bob", "carol"))
    joined = a.join("town_square")
    b.merge(joined)
    left = b.leave("town_square")
    # Removal can arrive ahead of the original add.
    c.merge(left)
    c.merge(joined)
    assert c.snapshot()["members"] == []
    # Re-adding after removal uses a fresh causal dot.
    again = a.join("town_square")
    for delta in (left, joined, again):
        b.merge(delta)
        c.merge(delta)
    a.merge(left)
    assert all("town_square" in p.snapshot()["members"] for p in (a, b, c))


def test_full_delta_reconciliation_after_partition():
    nodes = [DeltaReplica(n) for n in ("alice", "bob", "carol")]
    deltas = []
    for i, node in enumerate(nodes):
        deltas.append(node.join(f"peer_{i}"))
        deltas.append(node.put("cosmetic:banner", f"color_{i}"))
    nodes[1].merge(deltas[0])
    nodes[2].merge(deltas[1])
    rng = Random(42)
    for _ in range(5):
        incoming = [x.full_delta() for x in nodes]
        rng.shuffle(incoming)
        for node in nodes:
            for delta in incoming:
                node.merge(delta)
    view = nodes[0].snapshot()
    assert all(node.snapshot() == view for node in nodes)
    assert view["members"] == ["peer_0", "peer_1", "peer_2"]
    assert view["register"]["cosmetic:banner"] == "color_2"


def test_rejected_payload_does_not_partially_modify_replica():
    node = DeltaReplica("alice")
    packet = node.put("cosmetic:sign", "Civitas 99")
    before = deepcopy(node.full_delta())
    corruptions = [
        {**packet, "maps": [{**packet["maps"][0], "key": "credits:alice"}]},
        {**packet, "maps": [{**packet["maps"][0], "value": 1.5}]},
        {**packet, "maps": [{**packet["maps"][0], "clock": True}]},
        {**packet, "adds": [{"item": "a", "node": "bob", "clock": 1}]},
        {**packet, "removes": [{"node": "bob", "clock": -1}]},
        {**packet, "clock": 10**20},
        {**packet, "extra": 1},
        {**packet, "protocol": "other"},
    ]
    for invalid in corruptions:
        with pytest.raises(SyncError):
            node.merge(invalid)
        assert node.full_delta() == before


def test_no_authoritative_game_fields_can_enter_lww_map():
    a = DeltaReplica("alice")
    for field in ("balance:alice", "market:order", "credits:alice",
                  "votes:alice", "ledger:root", "reputation:alice"):
        with pytest.raises(SyncError):
            a.put(field, "123")
    with pytest.raises(SyncError):
        a.put("cosmetic:sign", "control\ncharacter")
    with pytest.raises(SyncError):
        a.join("bad name with spaces")
    assert a.snapshot() == {"register": {}, "members": []}


def test_merge_commutativity_and_idempotence_with_random_order():
    rng = Random(91)
    a, b = DeltaReplica("alice"), DeltaReplica("bob")
    payloads = []
    for i in range(50):
        writer = a if rng.randrange(2) else b
        payloads.append(writer.put("presence:avatar", f"status-{i}"))
        payloads.append(writer.join(f"item_{i}"))
    left = DeltaReplica("carol")
    right = DeltaReplica("dana")
    for packet in payloads:
        left.merge(packet)
    rng.shuffle(payloads)
    for packet in payloads:
        right.merge(packet)
    assert left.snapshot() == right.snapshot()
    for packet in payloads:
        assert right.merge(packet) is False


def test_zero_transitive_imports_from_economic_consensus():
    import sys
    import tcl.vos.sync as sync
    assert "tcl.ledger" not in sync.__dict__
    assert "tcl.node" not in sync.__dict__
    assert "tcl.consensus" not in sync.__dict__
    assert sys.modules.get("tcl.vos.sync") is sync
