#!/usr/bin/env python3
"""Checksum inventory verification; CI archive/image provenance supplies run identity.

This mutable file is not a signature, immutable store or execution attestation.
"""
import argparse
import hashlib
import json
from pathlib import Path

ARTIFACTS = [
    'ledger/demurrage.py','ledger/test_demurrage.py',
    'tests/protocol.test.cjs','tests/production-contracts.test.cjs','tests/zk.test.cjs',
    'contracts/CirculatoryEngine.sol','contracts/PhysicalMintController.sol',
    'contracts/production/ProductionMintController.sol',
    'contracts/production/ProtocolDeploymentFactory.sol',
    'contracts/production/ProtocolTimelock.sol','circuits/puf_verify.circom',
    'Containerfile','production/validate-in-container.sh','scripts/container_validation.py',
    'production/executor.py','production/ceremony.py','production/ceremony_profile.py',
    'production/drand.py','production/hardware.py','production/release_gate.py',
    'scripts/record_provenance.py','package.json','package-lock.json',
    'requirements-production.txt','requirements-test.txt','verification/requirements.txt',
    '.github/workflows/quality-gates.yml',
]


def inventory(root):
    return {'artifacts':{rel:hashlib.sha256((Path(root)/rel).read_bytes()).hexdigest() for rel in ARTIFACTS}}


def check(root,manifest):
    if manifest!=inventory(root):raise ValueError('stale or incomplete artifact inventory')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];path=root/'provenance.json'
    if args.check:
        check(root,json.loads(path.read_text()));print('Source artifact checksum inventory verified.')
    else:
        path.write_text(json.dumps(inventory(root),indent=2)+'\n');print('Updated source checksum inventory; not an execution attestation.')

if __name__=='__main__':main()
