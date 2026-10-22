"""Strict mainnet ceremony profile around the frozen Circom/snarkjs protocol.

This verifies authenticated operational claims, not honest erasure, participant
independence, wall-clock truth, physical qualification, or circuit completeness.
"""
import argparse
import hashlib
from pathlib import Path
from . import ceremony, drand
from .release_gate import require, verify_signatures

TIMELINE_DOMAIN='TCL-ceremony-timeline-v1'
TIMELINE_PREFIX=b'TCL-CEREMONY-TIMELINE-v1\n'
FROZEN_CORE={
    'circuits/puf_verify.circom':'75ad0740b09fa4682d6b71db81df2d921fbe5d578ad8bf57c5216a353efeca3f',
    'contracts/CirculatoryEngine.sol':'ec8d85c1ad01035ed44652af3fd765570431eb2428d91b0ed25b09b8bc2ea883',
}


def blake2b512(path):
    """Plain BLAKE2b-512 for files; canonical path + child digest framing for trees."""
    path=Path(path)
    h=hashlib.blake2b(digest_size=64)
    if path.is_dir():
        for file in sorted(path.rglob('*')):
            if file.is_symlink() and (not file.is_file() or not file.resolve().is_relative_to(path.resolve())):
                raise ValueError('symlink escapes pinned directory or aliases directory')
            if file.is_file():
                h.update(ceremony.canonical(str(file.relative_to(path))))
                h.update(bytes.fromhex(blake2b512(file)))
    else:
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify(profile, policy, manifest, timeline, drand_policy, base, archive, replica, expected_profile_sha256):
    require(drand.digest(profile)==expected_profile_sha256,'external mainnet profile pin mismatch')
    require(profile.get('version')==1 and profile.get('profile')=='tcl-mainnet-ceremony-v1','mainnet ceremony profile required')
    minimum=profile.get('minimum_contributors')
    require(type(minimum) is int and minimum>=10,'minimum ten private contributors required')
    ceremony.check_policy(policy)
    require(policy['phase_quorum']>=minimum,'ceremony policy weakens contributor minimum')
    require(profile.get('ceremony_policy_sha256')==drand.digest(policy),'ceremony policy pin mismatch')
    require(profile.get('drand_policy_sha256')==drand.digest(drand_policy),'drand policy pin mismatch')
    require(manifest.get('policy_sha256')==drand.digest(policy),'manifest policy mismatch')
    require(archive is not None and replica is not None,'durable archive and independent replica required')
    base=Path(base).resolve()
    artifacts=manifest['artifacts']
    require(set(artifacts)==ceremony.REQUIRED,'required ceremony artifacts absent')
    for artifact in artifacts.values():
        path=(base/artifact['path']).resolve()
        ceremony.require_hash(path,artifact['sha256'])
        require(blake2b512(path)==artifact['blake2b512'],'BLAKE2b-512 artifact mismatch')
    source=(base/artifacts['source']['path']).resolve()
    for path,expected in FROZEN_CORE.items():ceremony.require_hash(source/path,expected)
    require(artifacts['circuit']['sha256']==FROZEN_CORE['circuits/puf_verify.circom'],'different redemption statement')
    keys=profile['timeline_keys']
    quorum=profile.get('timeline_quorum')
    require(type(quorum) is int and quorum>=2,'two timeline witnesses required')
    timeline_public={bytes.fromhex(k) for k in keys.values()}
    ceremony_public={bytes.fromhex(k) for role in ('contributors','release_approvers') for k in policy[role].values()}
    require(len(timeline_public)==len(keys) and timeline_public.isdisjoint(ceremony_public),'timeline witness key reuse')
    payload=timeline['payload']
    require(payload.get('domain')==TIMELINE_DOMAIN and payload.get('manifest_sha256')==drand.digest(manifest),'timeline does not cover exact manifest')
    verify_signatures(TIMELINE_PREFIX+ceremony.canonical(payload),timeline.get('approvals',[]),keys,quorum)
    require(set(payload['phases'])=={'ptau','zkey'},'both phase timelines required')
    counts={};beacons={}
    for phase in ('ptau','zkey'):
        chain=manifest['phases'][phase]
        initial=chain['initial'];initial_path=(base/initial['path']).resolve()
        ceremony.require_hash(initial_path,initial['sha256'])
        require(blake2b512(initial_path)==initial['blake2b512'],'initial transcript dual hash mismatch')
        receipts=chain['receipts'];private=[];seen=set()
        require(receipts and receipts[-1]['payload']['kind']=='beacon','final beacon required')
        for receipt in receipts:
            data=receipt['payload'];who=data['contributor']
            key=bytes.fromhex(policy['contributors'][who])
            require(key not in seen,'duplicate contributor key');seen.add(key)
            ceremony.authenticate(policy['contributors'][who],receipt['signature'],'receipt',data)
            output=(base/data['path']).resolve()
            ceremony.require_hash(output,data['output_sha256'])
            require(blake2b512(output)==data['output_blake2b512'],'contribution dual hash mismatch')
            if data['kind']=='contribution':private.append(data)
        require(len(private)>=minimum,'fewer than ten private contributions')
        event=payload['phases'][phase]
        require(event['final_private_output_sha256']==private[-1]['output_sha256'],'timeline last private contribution mismatch')
        require(event['commitment']==profile['beacon_commitments'][phase],'round was not pinned in trusted profile')
        beacon=drand.verify_beacon(drand_policy,event['commitment'],event['response'],event['completed_at'])
        require(policy['beacons'][phase]['value']==beacon['randomness'],'ceremony beacon differs from authenticated drand randomness')
        require(receipts[-1]['payload'].get('beacon')==policy['beacons'][phase],'beacon receipt differs from policy')
        counts[phase]=len(private);beacons[phase]=beacon
    require(payload['phases']['zkey']['completed_at'] > beacons['ptau']['round_time'],
            'phase two must complete after phase one beacon publication')
    require(beacons['zkey']['round_time'] > beacons['ptau']['round_time'],
            'phase two requires a later beacon round')
    # This native verifier reproduces R1CS/WASM, verifies every transcript prefix,
    # reproduces final beacons/phase2/keys and checks independent archive readback.
    release=ceremony.verify(policy,manifest,base,archive,replica)
    # Archive the authenticated profile/timeline/beacon policy as public evidence.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for name,value in (('profile',profile),('timeline',timeline),('drand-policy',drand_policy)):
            path=Path(tmp)/name;path.write_bytes(ceremony.canonical(value))
            ceremony.archive_file(path,ceremony.sha256(path),archive,replica)
    return {'profile':'tcl-mainnet-ceremony-v1','status':'verified','release_digest':release,
            'profile_sha256':expected_profile_sha256,'manifest_sha256':drand.digest(manifest),
            'private_contributors':counts,'beacons':beacons,
            'assurance':'Native transcript mathematics, dual hashes, authenticated identities and timing attestations; independence, clock truth and honest erasure require external evidence.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('profile','policy','manifest','timeline','drand-policy','archive','replica','profile-sha256'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    try:
        manifest_path=Path(args.manifest).resolve()
        result=verify(*(ceremony.load(getattr(args,name.replace('-','_'))) for name in ('profile','policy','manifest','timeline','drand-policy')),manifest_path.parent,args.archive,args.replica,args.profile_sha256)
        print(ceremony.canonical(result).decode())
    except (ValueError,KeyError,OSError,TypeError) as error:parser.exit(1,f'mainnet ceremony blocked: {error}\n')

if __name__=='__main__':main()
