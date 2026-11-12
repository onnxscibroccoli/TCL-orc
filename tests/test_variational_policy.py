"""Independent invariants for opt-in variational admission (not a personhood proof)."""
from dataclasses import replace

import pytest

from tcl.consensus import ed25519
from tcl.consensus.engine import TendermintEngine
from tcl.consensus.sim import CHAIN_ID, Network, SimApp, dev_secret
from tcl.consensus.types import block_hash
from tcl.consensus.variational_policy import (
    LagrangianPolicy, PolicyConfig, TransitionMetrics,
)


def metrics(**overrides):
    fields = dict(
        participating_persons=30, eligible_persons=100,
        previous_largest_share_ppm=180_000, largest_share_ppm=190_000,
        previous_hhi_ppm=100_000, hhi_ppm=110_000,
        work_units=1_000, governance_transition=True,
        value_transition=True,
    )
    fields.update(overrides)
    return TransitionMetrics(**fields)


def test_voice_is_person_based_and_capital_does_not_weight_voting():
    gate = LagrangianPolicy()
    small = gate.evaluate(metrics(participating_persons=10))
    large = gate.evaluate(metrics(participating_persons=40))
    assert small.admitted and large.admitted
    assert large.density > small.density
    assert large.voice_ppm == 400_000
    assert gate.evaluate(metrics(participating_persons=9)).violations == ("VOICE_QUORUM",)


def test_excess_concentration_rejects_worsening_not_repairs():
    gate = LagrangianPolicy()
    bad = gate.evaluate(metrics(
        previous_largest_share_ppm=205_000, largest_share_ppm=210_000,
        previous_hhi_ppm=170_000, hhi_ppm=180_000,
    ))
    assert not bad.admitted
    assert "CAPITAL_CONCENTRATION" in bad.violations
    assert "MONOPOLY_CONCENTRATION" in bad.violations
    better = gate.evaluate(metrics(
        previous_largest_share_ppm=230_000, largest_share_ppm=220_000,
        previous_hhi_ppm=190_000, hhi_ppm=180_000,
    ))
    assert better.admitted
    assert better.density < gate.evaluate(metrics()).density


def test_non_governance_empty_block_does_not_require_votes():
    decision = LagrangianPolicy().evaluate(metrics(
        participating_persons=0, eligible_persons=0,
        governance_transition=False, value_transition=False))
    assert decision.admitted


def test_malformed_evidence_and_unbounded_work_fail_closed():
    gate = LagrangianPolicy()
    with pytest.raises(ValueError):
        gate.evaluate(metrics(participating_persons=True))
    with pytest.raises(ValueError):
        gate.evaluate(metrics(participating_persons=101))
    with pytest.raises(ValueError):
        gate.evaluate(metrics(largest_share_ppm=1_000_001))
    with pytest.raises(ValueError):
        gate.evaluate(metrics(governance_transition=1))
    with pytest.raises(ValueError):
        PolicyConfig(max_hhi_ppm=-1)
    assert gate.evaluate(metrics(work_units=50_001)).violations == ("WORK_BUDGET",)


def test_repeatable_pure_density():
    gate = LagrangianPolicy(PolicyConfig())
    m = metrics()
    assert gate.evaluate(m) == gate.evaluate(replace(m))
    assert gate.evaluate(m).violations == ()


def test_consensus_gate_defaults_off_and_enabled_gate_needs_verified_host_evidence():
    secrets = [dev_secret(i) for i in range(4)]
    keys = [ed25519.public_key(s).hex() for s in secrets]
    app = SimApp(CHAIN_ID, keys)
    block = app.build_block(1, 0, sorted(keys)[0])
    bh = block_hash(block["header"])

    disabled = TendermintEngine(app, secrets[0])
    disabled.start(1)
    assert disabled._valid_block(bh, block)

    enabled = TendermintEngine(app, secrets[0], transition_policy=LagrangianPolicy())
    enabled.start(1)
    assert not enabled._valid_block(bh, block)  # missing application evidence

    app.transition_metrics = lambda b, height: metrics()
    # Proposer-supplied or uncommitted policy parameters are not authoritative.
    app.policy_commitment = lambda height: "0" * 64
    wrong_hash = TendermintEngine(app, secrets[0], transition_policy=LagrangianPolicy())
    wrong_hash.start(1)
    assert not wrong_hash._valid_block(bh, block)

    app.policy_commitment = lambda height: LagrangianPolicy().commitment(CHAIN_ID)
    permitted = TendermintEngine(app, secrets[0], transition_policy=LagrangianPolicy())
    permitted.start(1)
    assert permitted._valid_block(bh, block)

    app.transition_metrics = lambda b, height: metrics(work_units=70_000)
    rejected = TendermintEngine(app, secrets[0], transition_policy=LagrangianPolicy())
    rejected.start(1)
    assert not rejected._valid_block(bh, block)


def test_opt_in_bft_local_network_preserves_finality_and_liveness():
    # All honest validators independently derive the same deterministic
    # metrics. BFT quorum weights and signature verification stay unchanged.
    net = Network(4, seed=17, byzantine=frozenset({0}))
    for app in net.apps:
        app.transition_metrics = lambda block, height: metrics()
        # Test-only simulation stand-in for a genesis-pinned commitment.
        app.policy_commitment = lambda height: LagrangianPolicy().commitment(CHAIN_ID)
    for node in net.nodes:
        node.transition_policy = LagrangianPolicy()
    net.start()
    net.run_until(350)
    assert net.min_honest_height() >= 2
    for a in net.honest():
        for b in net.honest():
            for height, block_id in net.decisions[a].items():
                if height in net.decisions[b]:
                    assert net.decisions[b][height] == block_id


def test_unverified_concentration_rejects_consensus_candidates():
    net = Network(4, seed=5)
    for app in net.apps:
        app.transition_metrics = lambda block, height: metrics(
            previous_largest_share_ppm=260_000,
            largest_share_ppm=261_000,
        )
        app.policy_commitment = lambda height: LagrangianPolicy().commitment(CHAIN_ID)
    for node in net.nodes:
        node.transition_policy = LagrangianPolicy()
    net.start()
    net.run_until(250)
    assert net.min_honest_height() == 0


def test_policy_commitment_is_deterministic_chain_bound_and_configuration_bound():
    gate = LagrangianPolicy()
    assert gate.commitment(CHAIN_ID) == LagrangianPolicy(PolicyConfig()).commitment(CHAIN_ID)
    assert len(gate.commitment(CHAIN_ID)) == 64
    assert gate.commitment(CHAIN_ID) != gate.commitment(CHAIN_ID + "-fork")
    updated = LagrangianPolicy(PolicyConfig(min_voice_ppm=200_000))
    assert gate.commitment(CHAIN_ID) != updated.commitment(CHAIN_ID)
    with pytest.raises(ValueError):
        gate.commitment("")
    with pytest.raises(ValueError):
        PolicyConfig(voice_weight=10**9)


def test_policy_commitment_callback_errors_fail_closed():
    secret = dev_secret(0)
    keys = [ed25519.public_key(dev_secret(i)).hex() for i in range(4)]
    app = SimApp(CHAIN_ID, keys)
    app.transition_metrics = lambda b, height: metrics()
    def broken_commitment(height):
        raise RuntimeError("synthetic genesis read failure")
    app.policy_commitment = broken_commitment
    blk = app.build_block(1, 0, sorted(keys)[0])
    node = TendermintEngine(app, secret, transition_policy=LagrangianPolicy())
    node.start(1)
    assert not node._valid_block(block_hash(blk["header"]), blk)
