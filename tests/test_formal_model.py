"""Solver obligations: production model UNSAT; deliberately broken models SAT."""
import unittest
from verification.ledger_model import audit


class FormalLedgerTests(unittest.TestCase):
    def test_invariants_are_proved(self):
        for name, result in audit().items():
            with self.subTest(obligation=name):
                self.assertEqual(result, "unsat")

    def test_mutations_have_counterexamples(self):
        for mutation, obligation in {
            "burn_fee": "settle_conservation",
            "overcharge": "settle_threshold_cap",
            "relock": "permanent_commitment",
            "non_atomic": "redeem_atomic_failure",
            "forget_spent": "replay_monotonic",
        }.items():
            with self.subTest(mutation=mutation):
                self.assertEqual(audit(mutation)[obligation], "sat")


if __name__ == "__main__":
    unittest.main()
