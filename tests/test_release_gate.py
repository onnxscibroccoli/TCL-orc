"""Synthetic keys/evidence exercise the gate; they are never production approval."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from production.release_gate import canonical, digest, validate, verify_signatures, REQUIRED, DOMAIN


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.keys = [Ed25519PrivateKey.generate() for _ in range(3)]
        pubs = {str(i): k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
                for i, k in enumerate(self.keys)}
        def artifact(kind, value):
            raw = canonical(value)
            (self.root / kind).write_bytes(raw)
            return {'path': kind, 'sha256': digest(raw)}
        self.evidence = {kind: artifact(kind, {'evidence': kind}) for kind in REQUIRED}
        (self.root / 'Source.sol').write_text('contract SyntheticFixture {}')
        source_hash = digest((self.root / 'Source.sol').read_bytes())
        self.evidence['contract_audit'] = artifact('contract_audit', {
            'compiler': 'synthetic-fixture', 'compilerInputSha256': 'fixture',
            'sources': {'Source.sol': source_hash}, 'contracts': {'Fixture': {
                'creationBytecode': '00', 'creationBytecodeSha256': digest(b'\x00'),
                'runtimeBytecodeTemplate': '00', 'runtimeBytecodeTemplateSha256': digest(b'\x00')}}})
        self.evidence['circuit_statement'] = artifact('circuit_statement', {'version': 'v1', 'statement': {'full': 'synthetic relation'}})
        self.evidence['mpc_report'] = artifact('mpc_report', {'purpose': 'production-ceremony', 'status': 'accepted',
            **{label: {'status': 'verified', 'sha256': self.evidence['mpc_'+label]['sha256']}
               for label in ('archive', 'replica')}})
        attestation = {'domain': 'TCL-executor-attestation-v1', 'chain_id': 1,
                       'executor_provenance_sha256': self.evidence['executor_provenance']['sha256']}
        self.evidence['executor_attestation'] = artifact('executor_attestation', {
            'payload': attestation, 'signatures': [{'key_id': '2', 'signature': self.keys[2].sign(canonical(attestation)).hex()}]})
        self.policy = {'version': 1, 'quorum': 2, 'release_keys': {k: pubs[k] for k in ('0', '1')},
            'attestation_keys': {'2': pubs['2']}, 'required_evidence': sorted(REQUIRED),
            'chain_id': 1, 'circuit_version': 'v1', 'max_validity_seconds': 100,
            'source_pins': {'Source.sol': source_hash},
            'artifact_pins': {k: self.evidence[k]['sha256'] for k in
                ('circuit_statement', 'release_manifest', 'contract_audit', 'executor_provenance')}}
        self.deployment={**{name:'0x'+'1'*40 for name in ('governance','guardian','initial_holder','verifier','enrollment_authority','factory')}, **{name:'0x'+'2'*64 for name in ('verifier_code_hash','release_manifest_hash','governance_code_hash')},'supply':1000000,'finality_confirmations':64,'max_reorg_depth':16}
        self.policy['deployment_pins']=copy.deepcopy(self.deployment)
        self.config = {'deployment':self.deployment,'policy_sha256': digest(canonical(self.policy)), 'chain_id': 1,
            'purpose': 'production-release', 'issued_at': 100, 'expires_at': 200,
            'circuit_version': 'v1', 'evidence': self.evidence}
        self.sign()

    def sign(self):
        payload = {'domain': DOMAIN, 'deployment': copy.deepcopy(self.config)}
        self.envelope = {'payload': payload, 'signatures': [
            {'key_id': str(i), 'signature': self.keys[i].sign(canonical(payload)).hex()} for i in (0, 1)]}

    def test_approved_digest(self):
        self.assertEqual(validate(self.config, self.policy, self.envelope, self.root, 150),
                         digest(canonical(self.envelope['payload'])))

    def test_required_failures(self):
        for mutation in ('missing', 'chain', 'stale', 'policy', 'source', 'manifest', 'duplicate', 'unsigned', 'test_ceremony'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'missing': del self.config['evidence']['guardian_custody']
                elif mutation == 'chain': self.config['chain_id'] = 2
                elif mutation == 'stale': self.config['expires_at'] = 120
                elif mutation == 'policy': self.config['policy_sha256'] = 'wrong'
                elif mutation == 'source': (self.root / 'Source.sol').write_text('changed source')
                elif mutation == 'manifest': (self.root / 'release_manifest').write_text('changed manifest')
                elif mutation == 'duplicate': self.envelope['signatures'][1] = self.envelope['signatures'][0]
                elif mutation == 'unsigned': self.config['additional_deployment_parameter'] = 'changed'
                elif mutation == 'test_ceremony':
                    raw = b'local-test-ceremony'
                    (self.root / 'mpc_archive').write_bytes(raw)
                    self.config['evidence']['mpc_archive']['sha256'] = digest(raw)
                with self.assertRaises(ValueError):
                    validate(self.config, self.policy, self.envelope, self.root, 150)

    def test_policy_alias_and_cross_role_rejected(self):
        for role in ('alias', 'cross_role'):
            with self.subTest(role=role):
                self.setUp()
                if role == 'alias':
                    self.policy['release_keys']['1'] = self.policy['release_keys']['0'].upper()
                else:
                    self.policy['attestation_keys']['2'] = self.policy['release_keys']['0']
                if role == 'cross_role':
                    artifact = self.config['evidence']['executor_attestation']
                    attestation = json.loads((self.root / artifact['path']).read_bytes())
                    attestation['signatures'][0]['signature'] = self.keys[0].sign(canonical(attestation['payload'])).hex()
                    raw = canonical(attestation)
                    (self.root / artifact['path']).write_bytes(raw)
                    artifact['sha256'] = digest(raw)
                self.config['policy_sha256'] = digest(canonical(self.policy))
                self.sign()
                if role == 'alias':
                    self.envelope['signatures'][1]['signature'] = self.keys[0].sign(canonical(self.envelope['payload'])).hex()
                with self.assertRaises(ValueError):
                    validate(self.config, self.policy, self.envelope, self.root, 150)

    def test_deployment_parameters_are_mandatory_and_bounded(self):
        for mutation in ('missing', 'zero_address', 'supply', 'confirmation', 'pin'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'missing': self.config.pop('deployment')
                elif mutation == 'zero_address': self.config['deployment']['guardian']='0x'+'0'*40
                elif mutation == 'supply': self.config['deployment']['supply']=2**128
                elif mutation == 'confirmation': self.config['deployment']['finality_confirmations']=0
                elif mutation == 'pin': self.config['deployment']['verifier_code_hash']='0x'+'f'*64
                self.sign()
                with self.assertRaises(ValueError): validate(self.config,self.policy,self.envelope,self.root,150)

    def test_canonical_is_stable(self):
        self.assertEqual(canonical({'b': 2, 'a': 1}), canonical({'a': 1, 'b': 2}))


class SignatureAliasTests(unittest.TestCase):
    def test_encoded_aliases_do_not_form_quorum(self):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        signature = key.sign(b'message').hex()
        for alias in (public.upper(), ' '.join(public[i:i+2] for i in range(0, len(public), 2))):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                verify_signatures(b'message', [{'key_id': 'a', 'signature': signature},
                    {'key_id': 'b', 'signature': signature}], {'a': public, 'b': alias}, 2)
