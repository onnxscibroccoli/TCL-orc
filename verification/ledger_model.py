"""Symbolic one-step abstraction, NOT verification of deployed EVM bytecode.

Two explicit holders, an aggregate of all other holders, vault and controller.
A selected commitment/nullifier has arbitrary peers represented by other debt.
All quantities are mathematical integers bounded by the Solidity supply limit.
"""
import json
from z3 import And, Bool, If, Int, Not, Or, Solver, sat, unknown

SCALE = 10**27
MAX_SUPPLY = 2**128 - 1
THRESHOLD = 5_000_000 * 10**18


def audit(mutation=None):
    results = {}

    def prove(name, assumptions, claim):
        solver = Solver()
        solver.set(timeout=30000)
        solver.add(*assumptions, Not(claim))
        outcome = solver.check()
        if outcome == unknown:
            raise RuntimeError(f"{name}: solver unknown: {solver.reason_unknown()}")
        results[name] = str(outcome)

    a, b, other, vault, escrow, debt, value, supply = [Int(x) for x in
        ('a', 'b', 'other', 'vault', 'escrow', 'debt', 'value', 'supply')]
    common = [a >= 0, b >= 0, other >= 0, vault >= 0, escrow >= debt,
              debt >= 0, supply > 0, supply <= MAX_SUPPLY,
              a + b + other + vault + escrow == supply]
    factor, remainder = Int('factor'), Int('remainder')
    surplus = If(a > THRESHOLD, a - THRESHOLD, 0)
    numerator = surplus * (SCALE - factor) + remainder
    raw = numerator / SCALE
    fee = If(raw >= surplus, surplus, raw)
    if mutation == 'overcharge':
        fee = raw + 1
    next_remainder = If(raw >= surplus, 0, numerator % SCALE)
    assumptions = common + [factor >= 0, factor <= SCALE,
                            remainder >= 0, remainder < SCALE]
    new_a = a - fee
    new_vault = vault if mutation == 'burn_fee' else vault + fee
    prove('settle_conservation', assumptions,
          new_a + b + other + new_vault + escrow == supply)
    prove('settle_threshold_cap', assumptions, And(fee >= 0, fee <= surplus,
          new_a >= 0, If(a >= THRESHOLD, new_a >= THRESHOLD, new_a == a)))
    prove('fractional_remainder', assumptions,
          And(next_remainder >= 0, next_remainder < SCALE))
    prove('numerator_uint256_bound', assumptions,
          And(numerator >= 0, numerator < 2**256))
    # The exponentiation loop is proved through its inductive loop invariant:
    # result and factor start in [0,SCALE]; each multiplication preserves it.
    x, y = Int('fixed_x'), Int('fixed_y')
    prove('fixedpoint_loop_induction', [x >= 0, x <= SCALE, y >= 0, y <= SCALE],
          And(x*y < 2**256, x*y / SCALE >= 0, x*y / SCALE <= SCALE))

    amount = Int('amount')
    transfer_ok = And(amount >= 0, amount <= a)
    ta = If(transfer_ok, a-amount, a)
    tb = If(transfer_ok, b+amount, b)
    prove('transfer', common, And(ta >= 0, tb >= 0,
          ta+tb+other+vault+escrow == supply))
    redistribute_ok = And(amount >= 0, amount <= vault)
    ra = If(redistribute_ok, a+amount, a)
    rv = If(redistribute_ok, vault-amount, vault)
    prove('redistribute', common, And(ra >= 0, rv >= 0,
          ra+b+other+rv+escrow == supply))

    exists, locked, spent, proof, external_ok, configured = [Bool(x) for x in
        ('exists', 'locked', 'spent', 'proof', 'external_ok', 'configured')]
    note_state = common + [value > 0, value <= MAX_SUPPLY,
                           Or(Not(locked), And(exists, debt >= value))]
    lock_ok = And(configured, Not(exists), a >= value, external_ok)
    if mutation == 'relock':
        lock_ok = And(configured, Not(locked), a >= value, external_ok)
    la = If(lock_ok, a-value, a)
    le = If(lock_ok, escrow+value, escrow)
    ld = If(lock_ok, debt+value, debt)
    lexists = Or(exists, lock_ok)
    llocked = Or(locked, lock_ok)
    prove('lock_conservation_solvency', note_state,
          And(la >= 0, le >= ld, la+b+other+vault+le == supply))
    prove('permanent_commitment', note_state, Not(And(exists, lock_ok)))
    prove('lock_atomic_failure', note_state + [Not(lock_ok)],
          And(la == a, le == escrow, ld == debt, lexists == exists,
              llocked == locked))
    redeem_ok = And(locked, Not(spent), proof, external_ok)
    re = If(redeem_ok, escrow-value, escrow)
    rd = If(redeem_ok, debt-value, debt)
    rb = If(redeem_ok, b+value, b)
    rspent = Or(spent, redeem_ok)
    if mutation == 'forget_spent':
        rspent = False
    rlocked = And(locked, Not(redeem_ok))
    if mutation == 'non_atomic':
        rlocked = And(locked, Not(proof))
    prove('redeem_conservation_solvency', note_state,
          And(re >= rd, rd >= 0, rb >= 0,
              a+rb+other+vault+re == supply))
    prove('redeem_atomic_failure', note_state + [Not(redeem_ok)],
          And(re == escrow, rd == debt, rb == b,
              rlocked == locked, rspent == spent))
    prove('replay_monotonic', note_state,
          And(Or(Not(spent), rspent), Or(Not(redeem_ok),
              And(rspent, Not(rlocked), exists))))
    return results


if __name__ == '__main__':
    report = audit()
    print(json.dumps(report, indent=2))
    raise SystemExit(any(result != 'unsat' for result in report.values()))
