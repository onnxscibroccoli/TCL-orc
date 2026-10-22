"""Executable integer reference, differentially tested against local EVM settlement.

This is not the authoritative ledger or an independent correctness proof.
Chain-derived timestamps, authorization and atomicity are tested on Solidity.
"""
T = 5_000_000 * 10**18
SCALE = 10**27
SECOND_FACTOR = 999999994212962979707861764


def unsigned(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('unsigned integer out of bounds')


def settle(balance, elapsed_seconds, remainder=0, *, exempt=False, initialized=True):
    """Return balance, vault fee and carried numerator remainder, exactly as Solidity."""
    unsigned(balance,2**128-1);unsigned(elapsed_seconds,2**256-1);unsigned(remainder,SCALE-1)
    if balance <= T or exempt or not initialized:
        return balance,0,0
    factor=SCALE;base=SECOND_FACTOR;elapsed=elapsed_seconds
    while elapsed:
        if elapsed & 1:factor=factor*base//SCALE
        elapsed >>= 1
        if elapsed:base=base*base//SCALE
    surplus=balance-T
    fee,remainder=divmod(surplus*(SCALE-factor)+remainder,SCALE)
    if fee>=surplus:fee=surplus;remainder=0
    return balance-fee,fee,remainder


def apply_demurrage(balance, delta_t_days):
    """Compatibility helper for one settlement of a whole-day interval."""
    unsigned(delta_t_days,(2**256-1)//86400)
    return settle(balance,delta_t_days*86400)[:2]


def redistribute(vault, amount):
    unsigned(vault,2**128-1);unsigned(amount,2**128-1)
    if amount>vault:raise ValueError('invalid redistribution amount')
    return vault-amount,amount
