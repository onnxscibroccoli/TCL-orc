"""Independent invariants for opt-in variational admission (not a personhood proof)."""
from dataclasses import replace

import pytest

from tcl.consensus import ed25519
from tcl.consensus.engine import TendermintEngine
from tcl.consensus.sim import CHAIN_ID, SimApp, dev_secret
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
    permitted = TendermintEngine(app, secrets[0], transition_policy=LagrangianPolicy())
    permitted.start(1)
    assert permitted._valid_block(bh, block)

    app.transition_metrics = lambda b, height: metrics(work_units=70_000)
    rejected = TendermintEngine(app, secrets[0], transition_policy=LagrangianPolicy())
    rejected.start(1)
    assert not rejected._valid_block(bh, block)
