"""Profile orchestration tests; native MPC math remains covered by test_ceremony."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from production import ceremony, drand
from production import ceremony_profile as profile

ROOT=Path(__file__).resolve().parents[1]

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'source/contracts').mkdir(parents=True);(self.root/'source/circuits').mkdir()
        for path in profile.FROZEN_CORE:
            (self.root/'source'/path).write_bytes((ROOT/path).read_bytes())
        self.keys={str(i):Ed25519PrivateKey.generate() for i in range(11)}
        self.timeline_keys={str(i):Ed25519PrivateKey.generate() for i in range(2)}
        self.release_keys={str(i):Ed25519PrivateKey.generate() for i in range(2)}
        public=lambda k:k.public_key().public_bytes_raw().hex()
        self.policy={'version':1,'phase_quorum':10,'release_quorum':2,'contributors':{i:public(k) for i,k in self.keys.items()},'release_approvers':{i:public(k) for i,k in self.release_keys.items()},'beacons':{},'pinned_artifacts':{}}
        self.manifest={'version':1,'phases':{},'artifacts':{},'approvals':[]}
        self.drand_policy=json.loads((ROOT/'production/drand-chain.example.json').read_text())
        # Synthetic BLS authority tests orchestration with two sequential rounds.
        # Official quicknet interoperability is independently covered by test_drand.
        from py_ecc.optimized_bls12_381 import G2, multiply
        from py_ecc.bls.point_compression import compress_G1, compress_G2
        from py_ecc.bls.hash_to_curve import hash_to_G1
        key=42;z1,z2=compress_G2(multiply(G2,key))
        self.drand_policy['chain_info']['public_key']=(z1.to_bytes(48,'big')+z2.to_bytes(48,'big')).hex()
        self.drand_policy['chain_info_sha256']=drand.digest(self.drand_policy['chain_info'])
        self.responses={};self.completions={};commitments={}
        info=self.drand_policy['chain_info']
        for phase,round_number in (('ptau',1),('zkey',100)):
            msg=hashlib.sha256(round_number.to_bytes(8,'big')).digest()
            signature=compress_G1(multiply(hash_to_G1(msg,drand.DST,hashlib.sha256),key)).to_bytes(48,'big')
            self.responses[phase]={'round':round_number,'signature':signature.hex(),'randomness':hashlib.sha256(signature).hexdigest()}
            self.completions[phase]=info['genesis_time']+(round_number-1)*info['period']-121
            commitments[phase]=drand.make_commitment(self.drand_policy,round_number,self.completions[phase]-10)
        self.profile={'version':1,'profile':'tcl-mainnet-ceremony-v1','minimum_contributors':10,'timeline_quorum':2,'timeline_keys':{i:public(k) for i,k in self.timeline_keys.items()},'drand_policy_sha256':drand.digest(self.drand_policy),'beacon_commitments':commitments}
        for name in ceremony.REQUIRED:
            if name=='source':path=self.root/'source'
            elif name=='circuit':path=self.root/'source/circuits/puf_verify.circom'
            else:path=self.root/name;path.write_bytes(('fixture '+name).encode())
            self.manifest['artifacts'][name]={'path':str(path.relative_to(self.root)),'sha256':ceremony.sha256(path),'blake2b512':profile.blake2b512(path)}
        for phase in ('ptau','zkey'):
            self.policy['beacons'][phase]={'source':'drand-quicknet','value':self.responses[phase]['randomness'],'name':phase,'iterations':10}
            chain={'receipts':[],'initial':{}}
            for i in range(11):
                file=self.root/(phase+str(i));file.write_bytes((phase+str(i)).encode())
                payload={'phase':phase,'contributor':str(i),'kind':'beacon' if i==10 else 'contribution','path':file.name,'output_sha256':ceremony.sha256(file),'output_blake2b512':profile.blake2b512(file)}
                if i==10:payload['beacon']=self.policy['beacons'][phase]
                chain['receipts'].append({'payload':payload,'signature':self.keys[str(i)].sign(ceremony.message('receipt',payload)).hex()})
            initial=self.root/(phase+'initial');initial.write_bytes(b'initial')
            chain['initial']={'path':initial.name,'sha256':ceremony.sha256(initial),'blake2b512':profile.blake2b512(initial)}
            self.manifest['phases'][phase]=chain
        self.profile['ceremony_policy_sha256']=drand.digest(self.policy)
        self.manifest['policy_sha256']=drand.digest(self.policy)
        payload={'domain':profile.TIMELINE_DOMAIN,'manifest_sha256':drand.digest(self.manifest),'phases':{p:{'commitment':commitments[p],'completed_at':self.completions[p],'final_private_output_sha256':self.manifest['phases'][p]['receipts'][-2]['payload']['output_sha256'],'response':self.responses[p]} for p in ('ptau','zkey')}}
        self.timeline={'payload':payload};self.sign()

    def sign(self):
        self.timeline['approvals']=[{'key_id':i,'signature':k.sign(profile.TIMELINE_PREFIX+ceremony.canonical(self.timeline['payload'])).hex()} for i,k in self.timeline_keys.items()]

    def rebind(self):
        self.profile['ceremony_policy_sha256']=drand.digest(self.policy)
        self.manifest['policy_sha256']=drand.digest(self.policy)
        self.timeline['payload']['manifest_sha256']=drand.digest(self.manifest)
        self.sign()

    def verify(self):
        return profile.verify(self.profile,self.policy,self.manifest,self.timeline,self.drand_policy,self.root,self.root/'archive',self.root/'replica',drand.digest(self.profile))

    def test_native_verifier_required_after_authenticated_profile(self):
        with patch('production.ceremony_profile.ceremony.verify',return_value='native-release-id') as native:
            result=self.verify()
        self.assertEqual(result['release_digest'],'native-release-id');self.assertEqual(result['private_contributors'],{'ptau':10,'zkey':10});native.assert_called_once()
        self.assertFalse(native.call_args.kwargs.get('audit',False))

    def test_rejects_nine_and_duplicate_identity(self):
        for kind in ('nine','duplicate'):
            self.setUp()
            if kind=='nine':self.manifest['phases']['zkey']['receipts'].pop(0)
            else:self.policy['contributors']['1']=self.policy['contributors']['0']
            self.rebind()
            with self.assertRaises(ValueError):self.verify()

    def test_dual_hashes_and_frozen_core(self):
        for kind in ('blake','core','missing'):
            self.setUp()
            if kind=='blake':self.manifest['artifacts']['r1cs']['blake2b512']='0'*128
            elif kind=='core':(self.root/'source/contracts/CirculatoryEngine.sol').write_text('changed')
            else:del self.manifest['artifacts']['r1cs']['blake2b512']
            with self.assertRaises((ValueError,KeyError)):self.verify()

    def test_timeline_must_bind_exact_manifest_and_threshold_keys(self):
        for kind in ('manifest','single','alias','beacon','margin','output'):
            self.setUp()
            if kind=='manifest':self.timeline['payload']['manifest_sha256']='0'*64
            elif kind=='single':self.timeline['approvals'].pop()
            elif kind=='alias':self.profile['timeline_keys']['1']=self.profile['timeline_keys']['0'].upper()
            elif kind=='beacon':self.policy['beacons']['zkey']['value']='0'*64
            elif kind=='margin':self.timeline['payload']['phases']['zkey']['completed_at']+=1;self.sign()
            else:self.timeline['payload']['phases']['zkey']['final_private_output_sha256']='0'*64;self.sign()
            with self.assertRaises((ValueError,KeyError)):self.verify()

    def test_directory_hash_detects_changes_and_escaping_symlinks(self):
        tree=self.root/'tree';tree.mkdir();(tree/'a').write_bytes(b'first');before=profile.blake2b512(tree)
        (tree/'a').write_bytes(b'second');self.assertNotEqual(before,profile.blake2b512(tree))
        outside=self.root/'outside';outside.write_text('secret');(tree/'escape').symlink_to(outside)
        with self.assertRaises(ValueError):profile.blake2b512(tree)

    def test_reject_phase_two_completed_before_phase_one_beacon(self):
        self.timeline['payload']['phases']['zkey']['completed_at']=self.completions['ptau']
        self.timeline['payload']['phases']['zkey']['commitment']['committed_at']=self.completions['ptau']-10
        self.sign()
        with patch('production.ceremony_profile.ceremony.verify',return_value='native-release-id'):
            with self.assertRaisesRegex(ValueError,'phase two'):
                self.verify()
