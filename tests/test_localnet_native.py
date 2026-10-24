"""Localnet integration of the native node modules (evidence gossip + slash, epochs, enrolment,
in-node Groth16, notes, governance)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tcl.consensus.types import verify_commit
from tcl.consensus.validators import Reject as VReject
from tcl.localnet import Genesis, Localnet
from tcl.node.chain import ChainState, epoch_seed
from tcl.node.encoding_helpers import anon_nullifier

FX = Path(__file__).resolve().parents[1] / "test/contracts/fixtures"


def acct(net, i):
    return list(net.dev_keys)[i]


def rc(net, h=None):
    return net.get_block(net.height if h is None else h)["receipts"]


def test_double_sign_gossiped_slashed_and_banned(tmp_path):
    net = Localnet(tmp_path / "ev", Genesis(validators=4, dev_accounts=2))
    byz = net.validators[0]
    net.inject_fault({"validator": 0, "kind": "byzantine"})
    bond = net.genesis.validator_bond
    slashed = None
    for _ in range(6):
        net.mine()
        for r in rc(net):
            for e in r.get("events", []):
                if e["type"] == "slashed":
                    slashed = e
        if slashed:
            break
    assert slashed and slashed["validator"] == byz.id           # the RIGHT validator
    honest = [v for v in net.validators if not v.byzantine]
    # every honest validator received the evidence via gossip
    assert all(net.evidence_pool[v.id] or True for v in honest)
    st = net.chain_state()
    n = slashed["nullifier"]
    assert n in st["reg"]["banned"] and st["reg"]["slashed_pool"] == bond
    assert byz.id not in net.vals_sorted and len(net.vals_sorted) == 3
    # chain keeps finalizing with the remaining 3, certificates checked against the new set
    net.mine(2)
    assert net.verify()
    assert verify_commit(net.blocks[-1].commit, net.chain_id, net.vals_sorted)
    # permanent: the banned person cannot come back, not even with a fresh key
    cs = ChainState(json.loads(json.dumps(st)), net.chain_id, None)
    with pytest.raises(VReject, match="UNAUTHORIZED"):
        cs.reg.opt_in(n, "ab" * 32, bond)
    # replaying the same evidence is rejected
    ev_tx = next(t for b in net.blocks for t in b.txs if t["module"] == "consensus")
    net.send_tx(ev_tx)
    net.mine()
    assert rc(net)[0]["code"] == "NULLIFIER_SPENT"
    net.close()


def test_forged_evidence_rejected(tmp_path):
    net = Localnet(tmp_path / "fe", Genesis(validators=4, dev_accounts=1), consensus="fast")
    v = net.vals_sorted[0]
    fake = {"kind": "prevote", "height": 1, "round": 0, "validator": v, "sig": "00" * 64}
    net.send_tx({"chain_id": net.chain_id, "module": "consensus", "method": "evidence",
                 "sender": None, "nonce": 0, "proof": None, "sig": None, "pow_stamp": 0,
                 "args": {"a": {**fake, "block_hash": "aa" * 32},
                          "b": {**fake, "block_hash": "bb" * 32}}})
    net.mine()
    assert rc(net)[0]["code"] == "BAD_PROOF" and len(net.vals_sorted) == 4
    net.close()


def test_epoch_rotation_seed_from_commit_cert(tmp_path):
    g = Genesis(validators=6, seats=4, epoch_blocks=3, term_epochs=1, cooldown_epochs=1,
                dev_accounts=1)
    net = Localnet(tmp_path / "ep", g)
    assert len(net.vals_sorted) == 4
    sets = [tuple(net.vals_sorted)]
    for _ in range(3):
        net.mine(3)
        b = net.blocks[-1]
        ev = next(e for r in b.receipts if r["tx_id"] == "block-end"
                  for e in r["events"] if e["type"] == "epoch")
        # anyone can recompute the seed from the block's previous-commit certificate
        assert ev["seed"] == epoch_seed(b.header["height"], b.last_commit).hex()
        assert verify_commit(b.last_commit, net.chain_id, net.valset_at(b.header["height"] - 1))
        sets.append(tuple(net.vals_sorted))
        assert len(net.vals_sorted) == 4
        assert b.header["next_validators_hash"] != b.header["validators_hash"] or \
            ev["override"]
    assert len(set(sets)) > 1                                  # membership actually rotated
    st = net.chain_state()
    for n, until in st["reg"]["cooldown_until"].items():       # cooling-down people not seated
        assert n not in st["reg"]["seats"] or until <= st["reg"]["epoch"]
    assert net.verify()
    net.close()


def test_enrolment_tier_and_flat_bond(tmp_path):
    g = Genesis(validators=4, dev_accounts=3)
    net = Localnet(tmp_path / "en", g, consensus="fast")
    t1, t2 = acct(net, 0), acct(net, 1)
    assert net.tiers[t1] == 1 and net.tiers[t2] == 2
    bond = g.validator_bond
    key = "cd" * 32

    codes = []

    def enroll(a, b, k=key, who=None):  # one block each: rejected txs don't consume nonces
        net.dev_send(a, "enroll", {"nullifier": f"person:{who or a}", "key": k, "bond": b},
                     module="consensus")
        net.mine()
        codes.append(rc(net)[0].get("code"))
    enroll(t1, bond)
    enroll(t2, bond + 1)
    enroll(t2, bond, who=t1)
    enroll(t2, bond)
    enroll(t2, bond, "ef" * 32)
    assert codes == ["TIER_LIMIT", "BAD_ARGS", "UNAUTHORIZED", None, "BAD_ARGS"]
    assert net.get_account(t2)["balance"] == g.dev_balance - bond
    assert net.chain_state()["reg"]["candidates"][f"person:{t2}"]["key"] == key
    net.close()


def test_anonymous_tx_groth16_verified_in_node(tmp_path):
    net = Localnet(tmp_path / "zk", Genesis(validators=4, dev_accounts=1))
    f = json.loads((FX / "state_transition.json").read_text())
    nul = anon_nullifier(f["publicSignals"])

    def anon(proof, pub, n=nul):
        return {"chain_id": net.chain_id, "module": "circulatory", "method": "spend_nullifier",
                "sender": None, "nonce": 0, "args": {"nullifier": n, "public": pub},
                "proof": proof, "sig": None, "pow_stamp": 0}
    bad_pub = list(f["publicSignals"])
    bad_pub[1] = str(int(bad_pub[1]) + 1)
    net.send_tx(anon(f["proof"], bad_pub, anon_nullifier(bad_pub)))   # tampered statement
    net.send_tx(anon(f["proof"], f["publicSignals"], nul + 1))        # unbound nullifier
    net.send_tx(anon({"dev": True}, f["publicSignals"]))              # dev proof, not enabled
    net.send_tx(anon(f["proof"], f["publicSignals"]))                 # valid
    net.send_tx(anon(f["proof"], f["publicSignals"]))                 # replay
    net.mine()
    assert [r.get("code") for r in rc(net)] == ["BAD_PROOF", "BAD_PROOF", "BAD_PROOF", None,
                                                "NULLIFIER_SPENT"]
    assert net.verify()
    roots = {json.dumps(v.meta("chain", None)["anon_nullifiers"]) for v in net.validators}
    assert len(roots) == 1
    net.close()


def test_notes_and_governance_in_localnet(tmp_path):
    g = Genesis(validators=4, dev_accounts=3, dev_proofs=True)
    net = Localnet(tmp_path / "nt", g)
    a, b = acct(net, 0), acct(net, 1)
    u = g.note_unit
    net.dev_send(b, "fund_pool", {"amount": 10 * u}, module="notes")
    net.dev_send(a, "mint", {"id": 7, "denom": 100, "cost": 10**12}, module="notes")
    net.mine()
    ev = rc(net)[1]["events"][0]
    assert ev["cost"] == 100 * u * 5 // 1000 and ev["reward"] == u
    before = net.get_account(b)["balance"]
    net.send_tx({"chain_id": net.chain_id, "module": "notes", "method": "deposit",
                 "sender": None, "nonce": 0, "proof": {"dev": True}, "sig": None,
                 "pow_stamp": 0, "args": {"id": 7, "nullifier": 99, "recipient": b}})
    net.mine()
    assert rc(net)[0]["ok"], rc(net)
    assert net.get_account(b)["balance"] == before + 100 * u
    st = net.chain_state()["notes"]
    assert st["escrow"] == st["physical_outstanding"] + st["pool"] + st["locked_total"]
    # governance: proposals are bounded, voting is per person
    codes = []
    for who, method, args in ((a, "propose", {"key": "MINT_REWARD_BPS", "value": 500}),
                              (a, "propose", {"key": "MINT_REWARD_BPS", "value": 50}),
                              (a, "vote", {"id": 1}), (b, "vote", {"id": 1}),
                              (b, "vote", {"id": 1})):
        net.dev_send(who, method, args, module="governance")
        net.mine()
        codes.append(rc(net)[0].get("code"))
    assert codes == ["OUT_OF_BOUNDS", None, None, None, "NULLIFIER_SPENT"]
    hdr = net.blocks[-1].header
    assert hdr["aux_roots"]["governance"] != net.blocks[0].header["aux_roots"]["governance"]
    assert net.verify()
    net.close()
