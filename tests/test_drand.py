"""Offline quicknet interoperability and fail-closed policy tests."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from production.drand import digest, make_commitment, verify_beacon

ROOT = Path(__file__).resolve().parents[1]
# Official quicknet public/1, retrieved 2026-10-22. No network in tests.
VECTOR = {'round': 1,
          'randomness': '1466a6cd24e327188770752f6134001c64d6efcc590ccc26b721611ad96f165a',
          'signature': 'b55e7cb2d5c613ee0b2e28d6750aabbb78c39dcc96bd9d38c2c2e12198df95571de8e8e402a0cc48871c7089a2b3af4b'}


class DrandTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / 'production/drand-chain.example.json').read_text())
        self.commitment = make_commitment(self.policy, 1, 1692803000)
        self.response = copy.deepcopy(VECTOR)
        self.completed = 1692803200

    def verify(self):
        return verify_beacon(self.policy, self.commitment, self.response, self.completed)

    def test_official_quicknet_round_one(self):
        result = self.verify()
        self.assertEqual(result['randomness'], VECTOR['randomness'])
        self.assertEqual(result['round_time'], 1692803367)
        self.assertEqual(result['commitment_sha256'], digest(self.commitment))

    def test_wrong_round(self):
        self.response['round'] = 2
        with self.assertRaises(ValueError): self.verify()

    def test_wrong_committed_chain(self):
        self.commitment['chain_hash'] = '00' * 32
        with self.assertRaises(ValueError): self.verify()

    def test_wrong_response_chain(self):
        self.response['chain_hash'] = '00' * 32
        with self.assertRaises(ValueError): self.verify()

    def test_wrong_signature_even_with_matching_randomness(self):
        self.response['signature'] = 'a5' + self.response['signature'][2:]
        self.response['randomness'] = hashlib.sha256(bytes.fromhex(self.response['signature'])).hexdigest()
        with self.assertRaises(ValueError): self.verify()

    def test_wrong_randomness(self):
        self.response['randomness'] = '00' * 32
        with self.assertRaises(ValueError): self.verify()

    def test_infinity_signature(self):
        self.response['signature'] = 'c0' + '00' * 47
        self.response['randomness'] = hashlib.sha256(bytes.fromhex(self.response['signature'])).hexdigest()
        with self.assertRaises(ValueError): self.verify()

    def test_non_subgroup_signature(self):
        from py_ecc.bls.hash_to_curve import map_to_curve_G1
        from py_ecc.bls.point_compression import compress_G1
        from py_ecc.optimized_bls12_381 import FQ, curve_order, is_inf, multiply
        point = map_to_curve_G1(FQ(1))
        self.assertFalse(is_inf(multiply(point, curve_order)))
        self.response['signature'] = int(compress_G1(point)).to_bytes(48, 'big').hex()
        self.response['randomness'] = hashlib.sha256(bytes.fromhex(self.response['signature'])).hexdigest()
        with self.assertRaises(ValueError): self.verify()

    def test_infinity_key(self):
        self.policy['chain_info']['public_key'] = 'c0' + '00' * 95
        self.policy['chain_info_sha256'] = digest(self.policy['chain_info'])
        self.commitment = make_commitment(self.policy, 1, 1692803000)
        with self.assertRaises(ValueError): self.verify()

    def test_exact_120_seconds_is_too_early(self):
        self.completed = 1692803367 - 120
        with self.assertRaises(ValueError): self.verify()

    def test_121_seconds_is_valid(self):
        self.completed = 1692803367 - 121
        self.assertEqual(self.verify()['round'], 1)

    def test_post_completion_commitment(self):
        self.commitment['committed_at'] = self.completed
        with self.assertRaises(ValueError): self.verify()

    def test_policy_chain_info_tampering(self):
        self.policy['chain_info']['genesis_time'] += 1
        with self.assertRaises(ValueError): self.verify()

    def test_unsupported_scheme(self):
        self.policy['chain_info']['schemeID'] = 'bls-unchained-on-g1'
        self.policy['chain_info_sha256'] = digest(self.policy['chain_info'])
        with self.assertRaises(ValueError): self.verify()

    def test_round_types_and_bounds(self):
        for value in (True, 0, -1, 2**64, 1.0, '1'):
            with self.subTest(value=value):
                self.response['round'] = value
                with self.assertRaises(ValueError): self.verify()

    def test_noncanonical_signature(self):
        self.response['signature'] = self.response['signature'].upper()
        with self.assertRaises(ValueError): self.verify()


if __name__ == '__main__':
    unittest.main()
