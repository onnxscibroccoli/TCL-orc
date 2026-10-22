#!/usr/bin/env python3
"""Offline, policy-pinned quicknet verifier. No network or local-clock trust."""
import argparse
import hashlib
import json
from pathlib import Path
from py_ecc.bls.hash_to_curve import hash_to_G1
from py_ecc.bls.point_compression import decompress_G1, decompress_G2
from py_ecc.optimized_bls12_381 import G2, curve_order, is_inf, multiply, pairing

SCHEME = 'bls-unchained-g1-rfc9380'
DST = b'BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_NUL_'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value < 2**64:
        raise ValueError('invalid ' + name)
    return value


def hexbytes(value, length):
    if not isinstance(value, str) or len(value) != length * 2:
        raise ValueError('invalid hex length')
    try:
        result = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError('invalid hex encoding') from exc
    if result.hex() != value:
        raise ValueError('hex must be canonical lowercase')
    return result


def validate_policy(policy):
    """Policy itself must be supplied from the authenticated ceremony policy."""
    if policy['version'] != 1:
        raise ValueError('unsupported drand policy')
    info = policy['chain_info']
    if digest(info) != policy['chain_info_sha256']:
        raise ValueError('pinned chain info digest mismatch')
    if info['schemeID'] != SCHEME or info['metadata']['beaconID'] != 'quicknet':
        raise ValueError('unsupported drand scheme/network')
    hexbytes(info['hash'], 32)
    hexbytes(info['groupHash'], 32)
    hexbytes(info['public_key'], 96)
    integer(info['genesis_time'], 'genesis time', 1)
    integer(info['period'], 'period', 1)
    return info


def make_commitment(policy, round_number, committed_at):
    info = validate_policy(policy)
    integer(round_number, 'round', 1)
    integer(committed_at, 'commitment time', 1)
    return {'version': 1, 'chain_hash': info['hash'],
            'chain_info_sha256': policy['chain_info_sha256'],
            'round': round_number, 'committed_at': committed_at}


def verify_beacon(policy, commitment, response, completed_at):
    """Verify local response; caller authenticates commitment/completion timestamps."""
    info = validate_policy(policy)
    completed_at = integer(completed_at, 'completion time', 1)
    expected = make_commitment(policy, commitment['round'], commitment['committed_at'])
    if commitment != expected:
        raise ValueError('beacon commitment differs from pinned chain/format')
    if commitment['committed_at'] >= completed_at:
        raise ValueError('beacon must be committed before contribution completion')
    round_number = integer(response['round'], 'response round', 1)
    if round_number != commitment['round']:
        raise ValueError('wrong beacon round')
    if 'chain_hash' in response and response['chain_hash'] != info['hash']:
        raise ValueError('wrong response chain')
    round_time = info['genesis_time'] + (round_number - 1) * info['period']
    if round_time <= completed_at + 120:
        raise ValueError('beacon round must be more than 120 seconds after completion')
    signature = hexbytes(response['signature'], 48)
    randomness = hexbytes(response['randomness'], 32)
    if hashlib.sha256(signature).digest() != randomness:
        raise ValueError('beacon randomness does not hash signature')
    key = hexbytes(info['public_key'], 96)
    try:
        sig = decompress_G1(int.from_bytes(signature, 'big'))
        pub = decompress_G2((int.from_bytes(key[:48], 'big'), int.from_bytes(key[48:], 'big')))
        if is_inf(sig) or is_inf(pub) or not is_inf(multiply(sig, curve_order)) or not is_inf(multiply(pub, curve_order)):
            raise ValueError('invalid BLS point/subgroup')
        message = hashlib.sha256(round_number.to_bytes(8, 'big')).digest()
        hashed = hash_to_G1(message, DST, hashlib.sha256)
        if pairing(pub, hashed) != pairing(G2, sig):
            raise ValueError('invalid drand BLS signature')
    except (ValueError, AssertionError, TypeError) as exc:
        raise ValueError('invalid drand BLS signature or point') from exc
    return {'version': 1, 'source': 'drand-quicknet', 'chain_hash': info['hash'],
            'chain_info_sha256': policy['chain_info_sha256'], 'scheme': SCHEME,
            'round': round_number, 'round_time': round_time,
            'commitment_sha256': digest(commitment), 'completed_at': completed_at,
            'signature': signature.hex(), 'randomness': randomness.hex()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('policy', 'commitment', 'response'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--completed-at', required=True, type=int)
    args = parser.parse_args()
    evidence = verify_beacon(*(json.loads(getattr(args, name).read_text()) for name in ('policy', 'commitment', 'response')), args.completed_at)
    print(canonical(evidence).decode())


if __name__ == '__main__':
    main()
