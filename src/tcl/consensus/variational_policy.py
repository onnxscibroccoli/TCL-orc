"""Deterministic, integer-only variational *admission* policy for verified transitions.

This is NOT stake-weighted consensus, proof of personhood, or a mechanism to
compel third parties to use TCL.  A host must derive TransitionMetrics from
independently verified, committed state; never from proposer assertions.
The policy is opt-in pending genesis pinning and audited personhood proofs.
"""
from __future__ import annotations

from dataclasses import dataclass

PPM = 1_000_000


@dataclass(frozen=True)
class PolicyConfig:
    """Immutable protocol parameters; must be identical for every validator."""

    min_voice_ppm: int = 100_000
    max_largest_share_ppm: int = 200_000
    max_hhi_ppm: int = 150_000
    max_work_units: int = 50_000
    voice_weight: int = 1
    capital_weight: int = 4
    monopoly_weight: int = 4
    work_weight: int = 1

    def __post_init__(self) -> None:
        bounded = (self.min_voice_ppm, self.max_largest_share_ppm, self.max_hhi_ppm)
        if any(type(v) is not int or not 0 <= v <= PPM for v in bounded):
            raise ValueError("policy PPM thresholds must be integer in [0, 1000000]")
        weights = (self.voice_weight, self.capital_weight,
                   self.monopoly_weight, self.work_weight)
        if any(type(w) is not int or w < 0 for w in weights):
            raise ValueError("policy weights must be nonnegative integers")
        if type(self.max_work_units) is not int or self.max_work_units <= 0:
            raise ValueError("max_work_units must be positive integer")


@dataclass(frozen=True)
class TransitionMetrics:
    """Evidence-derived, consensus-reproducible metrics supplied by the host.

    Shares and Herfindahl-Hirschman index are in PPM. 'persons' must be
    distinct, verified personhood nullifiers, not keys or addresses.
    A governance_transition is a change requiring a democratic quality gate;
    a value_transition changes the economic distribution being measured.
    """

    participating_persons: int
    eligible_persons: int
    previous_largest_share_ppm: int
    largest_share_ppm: int
    previous_hhi_ppm: int
    hhi_ppm: int
    work_units: int
    governance_transition: bool = False
    value_transition: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    admitted: bool
    density: int
    voice_ppm: int
    violations: tuple[str, ...]


class LagrangianPolicy:
    """Fixed-point Lagrangian density and hard feasibility constraints.

    For accepted state transitions, maximize:
        voice_weight * voice
      - capital_weight * positive(largest_share - cap)^2 / PPM
      - monopoly_weight * positive(HHI - cap)^2 / PPM
      - work_weight * work_units * PPM / max_work_units.

    Hard constraints protect participation and non-increasing *excess*
    concentration, while retaining a repair path for preexisting breaches.
    This evaluation NEVER changes BFT quorum or voting weight.
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def evaluate(self, metrics: TransitionMetrics) -> PolicyDecision:
        if not isinstance(metrics, TransitionMetrics):
            raise ValueError("metrics must be evidence-derived TransitionMetrics")
        ints = (metrics.participating_persons, metrics.eligible_persons,
                metrics.previous_largest_share_ppm, metrics.largest_share_ppm,
                metrics.previous_hhi_ppm, metrics.hhi_ppm, metrics.work_units)
        if any(type(v) is not int for v in ints):
            raise ValueError("metrics must be integers (bool rejected)")
        if (metrics.eligible_persons < 0 or metrics.participating_persons < 0
                or metrics.participating_persons > metrics.eligible_persons
                or metrics.work_units < 0
                or any(not 0 <= v <= PPM for v in ints[2:6])):
            raise ValueError("metrics out of range")
        if (type(metrics.governance_transition) is not bool
                or type(metrics.value_transition) is not bool):
            raise ValueError("transition flags must be booleans")

        c = self.config
        voice = (metrics.participating_persons * PPM // metrics.eligible_persons
                 if metrics.eligible_persons else 0)
        excess_capital = max(0, metrics.largest_share_ppm - c.max_largest_share_ppm)
        excess_hhi = max(0, metrics.hhi_ppm - c.max_hhi_ppm)
        density = (
            (c.voice_weight * voice if metrics.governance_transition else 0)
            - c.capital_weight * excess_capital * excess_capital // PPM
            - c.monopoly_weight * excess_hhi * excess_hhi // PPM
            - c.work_weight * metrics.work_units * PPM // c.max_work_units
        )
        violations: list[str] = []
        if metrics.work_units > c.max_work_units:
            violations.append("WORK_BUDGET")
        if metrics.governance_transition and voice < c.min_voice_ppm:
            violations.append("VOICE_QUORUM")
        if metrics.value_transition:
            if (metrics.largest_share_ppm > c.max_largest_share_ppm
                    and metrics.largest_share_ppm > metrics.previous_largest_share_ppm):
                violations.append("CAPITAL_CONCENTRATION")
            if (metrics.hhi_ppm > c.max_hhi_ppm
                    and metrics.hhi_ppm > metrics.previous_hhi_ppm):
                violations.append("MONOPOLY_CONCENTRATION")
        return PolicyDecision(not violations, density, voice, tuple(violations))
