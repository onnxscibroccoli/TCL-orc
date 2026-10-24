"""Consensus (src/tcl/consensus) tests: safety, liveness, partition halt, slashing, sortition."""
import hashlib
import random

import pytest

from tcl.consensus import ed25519
from tcl.consensus.encoding import canonical, merkle_root
from tcl.consensus.engine import TendermintEngine
from tcl.consensus.evidence import BadEvidence, DuplicateVoteEvidence
from tcl.consensus.personhood import MockPersonhoodProvider
from tcl.consensus.sim import CHAIN_ID, Network, dev_secret
from tcl.consensus.types import (Invalid, check_tx_shape, quorum, sign_tx, sign_vote, tx_id,
                                 verify_commit)
from tcl.consensus.validators import (Reject, ValidatorParams, ValidatorRegistry,
                                      draw_without_replacement)


def agree(net):
    hs = net.honest()
    for a in hs:
        for b in hs:
            for h, bh in net.decisions[a].items():
                if h in net.decisions[b]:
                    assert net.decisions[b][h] == bh, f"FORK at height {h} between {a},{b}"


# ------------------------------------------------------------------------------ primitives
def test_ed25519_rfc8032_vector1():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = ed25519.public_key(sk)
    assert pk.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = ed25519.sign(sk, b"")
    assert sig.hex() == ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                         "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(pk, b"", sig)
    assert not ed25519.verify(pk, b"x", sig)


def test_canonical_encoding_and_merkle():
    assert canonical({"b": 1, "a": [1, "x"]}) == b'{"a":[1,"x"],"b":1}'
    assert merkle_root([]) == hashlib.sha256(b"").hexdigest()
    a, b = "11" * 32, "22" * 32
    assert merkle_root([a, b]) == hashlib.sha256(bytes.fromhex(a + b)).hexdigest()
    assert merkle_root([a]) == a


def test_quorum_is_strictly_more_than_two_thirds():
    for n in range(1, 200):
        q = quorum(n)
        assert 3 * q > 2 * n and 3 * (q - 1) <= 2 * n


def test_tx_format_no_fee_and_anon_rules():
    sk = dev_secret(0)
    tx = {"chain_id": CHAIN_ID, "module": "circulatory", "method": "transfer",
          "sender": ed25519.public_key(sk).hex(), "nonce": 0, "args": {"to": "ab", "amount": 1},
          "proof": None, "sig": None, "pow_stamp": 0}
    stx = sign_tx(tx, sk)
    check_tx_shape(stx)
    assert tx_id(stx) == tx_id(tx)  # tx_id excludes sig
    with pytest.raises(Invalid):
        check_tx_shape({**tx, "fee": 1})
    with pytest.raises(Invalid):
        check_tx_shape({**tx, "args": {"fee": 1}})
    with pytest.raises(Invalid):
        check_tx_shape({**tx, "sender": None})  # anon without proof/nullifier


# ------------------------------------------------------------------------------ BFT
def test_honest_network_commits_and_chain_links():
    net = Network(4, seed=1)
    net.start()
    net.run_until(300)
    assert net.min_honest_height() >= 5
    agree(net)
    app = net.apps[0]
    for i, (blk, c) in enumerate(zip(app.chain, app.commits)):
        assert blk["header"]["height"] == i + 1
        assert verify_commit(c, CHAIN_ID, app.vals)
        if i:
            lc = blk["last_commit"]  # sig sets may differ per node; must certify the parent
            assert lc["block_hash"] == app.commits[i - 1]["block_hash"]
            assert verify_commit(lc, CHAIN_ID, app.vals)


@pytest.mark.parametrize("seed", range(6))
def test_safety_and_liveness_with_one_equivocator_of_four(seed):
    net = Network(4, seed=seed, byzantine=frozenset({seed % 4}))
    net.start()
    net.run_until(500)
    agree(net)
    assert net.min_honest_height() >= 3


def test_safety_with_two_equivocators_of_seven():
    net = Network(7, seed=11, byzantine=frozenset({1, 4}))
    net.start()
    net.run_until(500)
    agree(net)
    assert net.min_honest_height() >= 2


def test_beyond_threshold_can_fork_documented():
    """Sanity check of the fault model: f >= n/3 is OUT of model; we only assert the sim runs.
    (Safety is claimed only for < n/3 Byzantine seats.)"""
    net = Network(4, seed=3, byzantine=frozenset({0, 1}))
    net.start()
    net.run_until(150)


def test_partition_halts_without_fork_then_heals():
    net = Network(4, seed=5)
    net.start()
    net.run_until(100)
    agree(net)
    before = {i: len(net.decisions[i]) for i in range(4)}
    top = max(before.values())
    net.partition({0, 1}, {2, 3})
    net.run_until(1500)
    # No side has > 2/3 of 4 seats: at most the in-flight height completes, then halt.
    during = {i: len(net.decisions[i]) for i in range(4)}
    assert max(during.values()) <= top + 1
    net.run_until(3000)
    assert {i: len(net.decisions[i]) for i in range(4)} == during, "progress while partitioned"
    agree(net)
    net.heal()
    net.run_until(3000 + 2000)
    agree(net)
    assert net.min_honest_height() >= max(during.values()) + 3, "no liveness after heal"


def test_minority_partition_of_seven_majority_side_still_halts_below_quorum():
    net = Network(7, seed=8)
    net.start()
    net.partition({0, 1, 2, 3}, {4, 5, 6})  # 4 < quorum(7)=5 -> nobody commits
    net.run_until(1500)
    assert all(len(net.decisions[i]) == 0 for i in range(7))
    net.heal()
    net.run_until(4000)
    agree(net)
    assert net.min_honest_height() >= 2


def test_supermajority_side_keeps_going_minority_catches_up():
    net = Network(7, seed=9)
    net.start()
    net.partition({0, 1, 2, 3, 4}, {5, 6})
    net.run_until(800)
    assert min(len(net.decisions[i]) for i in range(5)) >= 2
    assert all(len(net.decisions[i]) == 0 for i in (5, 6))
    net.heal()
    net.run_until(2500)
    agree(net)
    assert min(len(net.decisions[i]) for i in (5, 6)) >= 2


def test_simulation_is_deterministic():
    runs = []
    for _ in range(2):
        net = Network(4, seed=42, byzantine=frozenset({2}))
        net.start()
        net.run_until(250)
        runs.append((net.decisions, net.delivered))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("seed", range(100, 108))
def test_property_random_faults_never_fork(seed):
    rng = random.Random(seed)
    n = rng.choice([4, 5, 7])
    f = (n - 1) // 3
    byz = frozenset(rng.sample(range(n), rng.randint(0, f)))
    net = Network(n, seed=seed, byzantine=byz, max_delay=rng.randint(1, 15))
    net.start()
    t = 0
    for _ in range(3):
        t += rng.randint(50, 200)
        net.run_until(t)
        if rng.random() < 0.5:
            ids = list(range(n))
            rng.shuffle(ids)
            k = rng.randint(1, n - 1)
            net.partition(set(ids[:k]), set(ids[k:]))
            t += rng.randint(50, 300)
            net.run_until(t)
            net.heal()
        agree(net)
    net.run_until(t + 1500)
    agree(net)
    assert net.min_honest_height() >= 1


# ------------------------------------------------------------------------------ evidence / slashing
def _registry(n_people=10, params=None):
    ph = MockPersonhoodProvider()
    params = params or ValidatorParams(N_VALIDATORS=4, TERM_EPOCHS=2, COOLDOWN_EPOCHS=1,
                                       VALIDATOR_BOND=1000)
    reg = ValidatorRegistry(ph, params)
    for i in range(n_people):
        null = f"null-{i:03d}"
        ph.enroll(null, 2)
        reg.opt_in(null, ed25519.public_key(dev_secret(i)).hex(), 1000)
    return reg


def test_double_sign_evidence_slashes_and_bans_forever():
    reg = _registry()
    reg.advance_epoch(0, b"r0")
    seated = reg.validator_keys()
    i = next(i for i in range(10) if ed25519.public_key(dev_secret(i)).hex() in seated)
    sk = dev_secret(i)
    a = sign_vote("precommit", CHAIN_ID, 5, 0, "aa" * 32, sk)
    b = sign_vote("precommit", CHAIN_ID, 5, 0, "bb" * 32, sk)
    null = reg.submit_evidence(DuplicateVoteEvidence(a, b), CHAIN_ID)
    assert null in reg.banned and reg.slashed_pool == 1000
    assert ed25519.public_key(sk).hex() not in reg.validator_keys()
    with pytest.raises(Reject):
        reg.submit_evidence(DuplicateVoteEvidence(a, b), CHAIN_ID)  # replay
    with pytest.raises(Reject):  # banned person can't come back, even with a fresh key
        reg.opt_in(null, ed25519.public_key(dev_secret(999)).hex(), 1000)
    for e in range(1, 20):
        reg.advance_epoch(e, b"r%d" % e)
        assert null not in reg.seats


def test_bad_evidence_rejected():
    sk, sk2 = dev_secret(0), dev_secret(1)
    a = sign_vote("prevote", CHAIN_ID, 1, 0, "aa" * 32, sk)
    with pytest.raises(BadEvidence):
        DuplicateVoteEvidence(a, a).verify(CHAIN_ID)  # not conflicting
    with pytest.raises(BadEvidence):
        DuplicateVoteEvidence(a, sign_vote("prevote", CHAIN_ID, 1, 1, "bb" * 32, sk)).verify(CHAIN_ID)
    with pytest.raises(BadEvidence):
        DuplicateVoteEvidence(a, sign_vote("prevote", CHAIN_ID, 1, 0, "bb" * 32, sk2)).verify(CHAIN_ID)
    forged = sign_vote("prevote", CHAIN_ID, 1, 0, "bb" * 32, sk)
    from dataclasses import replace
    forged = replace(forged, sig=a.sig)
    with pytest.raises(BadEvidence):
        DuplicateVoteEvidence(a, forged).verify(CHAIN_ID)


def test_equivocation_in_live_network_is_detected_and_slashable():
    net = Network(4, seed=2, byzantine=frozenset({3}))
    net.start()
    net.run_until(400)
    evs = [e for node in net.nodes for e in node.evidence]
    # Some honest node saw both halves? If not via a single node, assemble from two nodes' views.
    if not evs:
        seen = {}
        for node in net.honest():
            for (kind, r), box in net.nodes[node].hs.votes.items():
                v = box.get(net.keys[3])
                if v:
                    seen.setdefault((kind, r), set()).add(v)
        pairs = [tuple(vs)[:2] for vs in seen.values() if len({v.block_hash for v in vs}) > 1]
        assert pairs, "equivocation should be observable"
        evs = [DuplicateVoteEvidence(*pairs[0])]
    reg = ValidatorRegistry(MockPersonhoodProvider({f"p{i}": 2 for i in range(4)}),
                            ValidatorParams(N_VALIDATORS=4))
    for i in range(4):
        reg.opt_in(f"p{i}", net.keys[i], reg.params.VALIDATOR_BOND)
    reg.advance_epoch(0, b"x")
    assert reg.submit_evidence(evs[0], CHAIN_ID) == "p3"
    assert "p3" in reg.banned


# ------------------------------------------------------------------------------ sortition
def test_one_seat_per_person_even_with_many_keys():
    ph = MockPersonhoodProvider({"whale": 3, "alice": 2, "bob": 2})
    reg = ValidatorRegistry(ph, ValidatorParams(N_VALIDATORS=10))
    for k in range(50):  # whale registers 50 keys: each re-opt REPLACES, never adds a seat
        reg.opt_in("whale", ed25519.public_key(dev_secret(k, "whale")).hex(), reg.params.VALIDATOR_BOND)
    reg.opt_in("alice", ed25519.public_key(dev_secret(1, "a")).hex(), reg.params.VALIDATOR_BOND)
    reg.opt_in("bob", ed25519.public_key(dev_secret(1, "b")).hex(), reg.params.VALIDATOR_BOND)
    keys = reg.advance_epoch(0, b"seed")
    assert len(keys) == 3 and len(reg.seats) == 3
    assert all(reg.weight(k) == 1 for k in keys)
    with pytest.raises(Reject):  # one key cannot be shared across persons either
        reg.opt_in("bob", ed25519.public_key(dev_secret(1, "a")).hex(), reg.params.VALIDATOR_BOND)


def test_flat_bond_and_tier_gate():
    ph = MockPersonhoodProvider({"rich": 2, "low": 1})
    reg = ValidatorRegistry(ph)
    with pytest.raises(Reject):
        reg.opt_in("rich", "aa" * 32, reg.params.VALIDATOR_BOND * 100)  # bigger bond != more weight
    with pytest.raises(Reject):
        reg.opt_in("low", "bb" * 32, reg.params.VALIDATOR_BOND)
    with pytest.raises(Reject):
        reg.opt_in("ghost", "cc" * 32, reg.params.VALIDATOR_BOND)


def test_selection_deterministic_and_seed_sensitive():
    a, b = _registry(30), _registry(30)
    assert a.advance_epoch(0, b"same") == b.advance_epoch(0, b"same")
    c = _registry(30)
    assert c.advance_epoch(0, b"other") != a.validator_keys()


def test_term_and_cooldown():
    reg = _registry(8, ValidatorParams(N_VALIDATORS=4, TERM_EPOCHS=2, COOLDOWN_EPOCHS=1))
    reg.advance_epoch(0, b"e0")
    first = set(reg.seats)
    reg.advance_epoch(1, b"e1")
    assert set(reg.seats) == first  # still in term
    reg.advance_epoch(2, b"e2")
    assert not (set(reg.seats) & first), "term expired -> cooling down, can't be reseated"
    assert len(reg.seats) == 4
    for nl in first:
        assert reg.cooldown_until[nl] == 3


def test_draw_is_unbiased_enough_and_without_replacement():
    items = [f"n{i}" for i in range(20)]
    counts = {x: 0 for x in items}
    for s in range(2000):
        out = draw_without_replacement(s.to_bytes(4, "big"), items, 5)
        assert len(set(out)) == 5
        for x in out:
            counts[x] += 1
    # expected 500 each; generous bound
    assert all(380 < c < 620 for c in counts.values()), counts


@pytest.mark.parametrize("seed", range(5))
def test_property_selection_invariants(seed):
    rng = random.Random(seed)
    reg = _registry(rng.randint(5, 40), ValidatorParams(N_VALIDATORS=rng.randint(1, 10),
                                                        TERM_EPOCHS=rng.randint(1, 4),
                                                        COOLDOWN_EPOCHS=rng.randint(0, 3)))
    for e in range(15):
        keys = reg.advance_epoch(e, rng.randbytes(8))
        assert len(keys) == len(set(keys)) <= reg.params.N_VALIDATORS
        nulls = [reg.nullifier_of(k) for k in keys]
        assert len(set(nulls)) == len(nulls)
        assert not (set(nulls) & reg.banned)


def test_engine_satisfies_interface():
    from tcl.consensus.engine import ConsensusEngine
    eng = TendermintEngine(Network(4).apps[0], dev_secret(0))
    for name in ("start", "on_message", "on_timeout"):
        assert callable(getattr(eng, name))
    assert isinstance(ConsensusEngine, type)
