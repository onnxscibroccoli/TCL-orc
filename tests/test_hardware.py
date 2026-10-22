import math
import unittest
from production.hardware import binomial_upper, verify

class HardwareTests(unittest.TestCase):
    def test_zero_fail_bound(self):
        self.assertAlmostEqual(binomial_upper(0, 100, .95), 1 - .05 ** .01)
        self.assertGreater(binomial_upper(0, 2995730, .95), 1e-6)
        self.assertLess(binomial_upper(0, 2995731, .95), 1e-6)

    def test_positive_fail_bound(self):
        self.assertAlmostEqual(binomial_upper(1, 10, .95), .3941633024, places=8)

    def test_bad_counts(self):
        for k,n in [(-1,10),(1,0),(11,10),(False,10),(0,1.5)]:
            with self.assertRaises(ValueError): binomial_upper(k,n,.95)

    def test_missing_policy_blocked(self):
        self.assertEqual(verify({}, {}, '.')['status'], 'blocked')

class SyntheticEvidenceTests(unittest.TestCase):
    """Synthetic fixtures exercise validation only, never physical qualification."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from production.release_gate import digest, canonical
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        Path(self.root, 'synthetic').write_bytes(b'synthetic fixture, not physical evidence')
        self.artifact = {'path':'synthetic','sha256':digest(Path(self.root,'synthetic').read_bytes())}
        self.keys = [Ed25519PrivateKey.generate() for _ in range(2)]
        self.policy = {'version':1,'threshold':1e-6,'confidence':.95,'quorum':2,
            'review_keys':{str(i):k.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex() for i,k in enumerate(self.keys)},
            'extractor_suite':{'version':'synthetic-v1','code':self.artifact},
            'voltage_bounds_v':[.9,1.1],
            'strata':[{'temperature_c':t,'voltage_v':v} for t in (-15,25,80) for v in (.9,1.1)]}
        self.evidence = {'policy_sha256':digest(canonical(self.policy)),
            'purpose':'physical-enrollment-qualification','extractor_suite':self.policy['extractor_suite'],
            'experiment_metadata':self.artifact,'independence_protocol':self.artifact,
            'helper_authentication_tests':self.artifact,
            'independence_declaration':{k:'synthetic test only' for k in ('device_population','sample_selection','independent_trial_definition','correlation_handling')},
            'strata':[{**x,'device_count':100,'enrolled_measurements':self.artifact,'failures':0,'independent_trials':2995731} for x in self.policy['strata']]}

    def signed(self):
        from production.release_gate import canonical
        from production.hardware import DOMAIN
        self.evidence.pop('approvals',None)
        message = canonical({'domain':DOMAIN,'evidence':self.evidence})
        self.evidence['approvals'] = [{'key_id':str(i),'signature':key.sign(message).hex()} for i,key in enumerate(self.keys)]
        return verify(self.policy,self.evidence,self.root)

    def test_synthetic_conditional_report(self):
        self.assertEqual(self.signed()['status'],'conditional-acceptance')

    def test_reject_missing_corner(self):
        self.evidence['strata'].pop()
        self.assertEqual(self.signed()['status'],'blocked')

    def test_reject_insufficient_trials(self):
        self.evidence['strata'][0]['independent_trials'] = 10
        self.assertEqual(self.signed()['status'],'blocked')

    def test_reject_missing_helper_auth(self):
        del self.evidence['helper_authentication_tests']
        self.assertEqual(self.signed()['status'],'blocked')

    def test_reject_hash_mismatch(self):
        self.evidence['strata'][0]['enrolled_measurements'] = {'path':'synthetic','sha256':'0'*64}
        self.assertEqual(self.signed()['status'],'blocked')

    def test_reject_duplicate_key_aliases(self):
        from production.release_gate import canonical,digest
        self.policy['review_keys']['1'] = self.policy['review_keys']['0']
        self.keys[1] = self.keys[0]
        self.evidence['policy_sha256'] = digest(canonical(self.policy))
        self.assertEqual(self.signed()['status'],'blocked')

    def test_reject_degenerate_voltage_bounds(self):
        from production.release_gate import canonical,digest
        self.policy['voltage_bounds_v']=[.9,.9]
        self.policy['strata']=[{'temperature_c':t,'voltage_v':.9} for t in (-15,25,80)]
        self.evidence['strata']=[x for x in self.evidence['strata'] if x['voltage_v']==.9]
        self.evidence['policy_sha256']=digest(canonical(self.policy))
        self.assertEqual(self.signed()['status'],'blocked')
