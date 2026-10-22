"""Offline release approval verification. Never signs or broadcasts transactions."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REQUIRED = frozenset({'circuit_statement', 'release_manifest', 'contract_audit',
    'executor_provenance', 'executor_attestation', 'governance_custody',
    'guardian_custody', 'issuer_custody', 'independent_security_audit',
    'source_model_refinement_audit', 'mpc_report', 'mpc_archive', 'mpc_replica',
    'physical_enrollment_qualification', 'privacy_scope_review', 'jurisdiction_review',
    'incident_recovery_drill', 'rootless_runtime_acceptance'})
DOMAIN = 'TCL-production-release-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_artifact(root, spec):
    require(isinstance(spec, dict), 'artifact specification required')
    base = Path(root).resolve()
    target = (base / spec['path']).resolve()
    require(target.is_relative_to(base), 'artifact escapes root')
    raw = target.read_bytes()
    require(digest(raw) == spec['sha256'], 'artifact digest mismatch')
    require(raw, 'empty artifact')
    require(b'local-test-ceremony' not in raw.lower(), 'test ceremony forbidden')
    return raw


def decoded_keys(keys):
    require(isinstance(keys, dict) and bool(keys), 'public key policy required')
    decoded = {}
    for key_id, value in keys.items():
        try:
            raw = bytes.fromhex(value)
        except (TypeError, ValueError) as error:
            raise ValueError('invalid public key encoding') from error
        require(len(raw) == 32, 'Ed25519 public key must be 32 bytes')
        decoded[key_id] = raw
    return decoded


def verify_signatures(message, signatures, keys, quorum):
    require(type(quorum) is int and quorum >= 1, 'positive integer quorum required')
    public_keys = decoded_keys(keys)
    accepted = set()
    for item in signatures:
        key_id = item['key_id']
        require(key_id in keys, 'unrecognized approval key')
        try:
            public = Ed25519PublicKey.from_public_bytes(public_keys[key_id])
            public.verify(bytes.fromhex(item['signature']), message)
        except Exception as error:
            raise ValueError('invalid Ed25519 signature') from error
        accepted.add(public_keys[key_id])
    require(len(accepted) >= quorum, 'insufficient distinct signing keys')


def validate(config, policy, envelope, root, now=None):
    """Policy is an external trust root; caller must pin its digest out of band."""
    now = int(time.time()) if now is None else now
    require(policy.get('version') == 1, 'policy version')
    require(policy.get('quorum', 0) >= 2, 'release quorum must be at least two')
    release_keys = decoded_keys(policy.get('release_keys', {}))
    attestation_keys = decoded_keys(policy.get('attestation_keys', {}))
    require(type(policy['quorum']) is int, 'integer release quorum required')
    require(len(set(release_keys.values())) >= policy['quorum'], 'policy key quorum')
    require(set(release_keys.values()).isdisjoint(attestation_keys.values()),
            'executor attestation keys must be separate from release authorities')
    require(REQUIRED <= set(policy.get('required_evidence', [])), 'policy omits mandatory evidence')
    require(config.get('policy_sha256') == digest(canonical(policy)), 'wrong policy digest')
    chain = config.get('chain_id')
    require(type(chain) is int and chain > 0 and chain == policy.get('chain_id'), 'wrong chain')
    deployment = config.get('deployment')
    require(isinstance(deployment, dict), 'deployment parameters required')
    for name in ('governance', 'guardian', 'initial_holder', 'verifier', 'enrollment_authority', 'factory'):
        address = deployment.get(name)
        require(isinstance(address, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', address)
                and int(address, 16) != 0, f'invalid deployment address: {name}')
    for name in ('verifier_code_hash', 'release_manifest_hash', 'governance_code_hash'):
        value = deployment.get(name)
        require(isinstance(value, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', value)
                and int(value, 16) != 0, f'invalid deployment hash: {name}')
    require(type(deployment.get('supply')) is int and 0 < deployment['supply'] < 2**128, 'supply bounds')
    require(type(deployment.get('finality_confirmations')) is int
            and deployment['finality_confirmations'] > 0, 'positive finality threshold required')
    require(type(deployment.get('max_reorg_depth')) is int
            and 0 <= deployment['max_reorg_depth'] < deployment['finality_confirmations'], 'reorg policy bounds')
    require(deployment == policy.get('deployment_pins'), 'deployment differs from trusted policy pins')
    require(config.get('purpose') == 'production-release', 'production purpose required')
    require(type(config.get('issued_at')) is int and type(config.get('expires_at')) is int, 'timestamps required')
    require(config['issued_at'] <= now <= config['expires_at'], 'stale or future release')
    require(config['expires_at'] - config['issued_at'] <= policy.get('max_validity_seconds', 0), 'validity exceeds policy')
    require(config.get('circuit_version') == policy.get('circuit_version') and bool(config.get('circuit_version')), 'circuit version')
    artifacts = config.get('evidence', {})
    require(set(policy['required_evidence']) <= set(artifacts), 'missing required evidence')
    raw = {kind: read_artifact(root, spec) for kind, spec in artifacts.items()}
    # These pins select a concrete approved release, not merely any signed release.
    for kind in ('circuit_statement', 'release_manifest', 'contract_audit', 'executor_provenance'):
        require(artifacts[kind]['sha256'] == policy.get('artifact_pins', {}).get(kind), f'wrong {kind} pin')
    audit = json.loads(raw['contract_audit'])
    require(bool(audit.get('compiler')) and bool(audit.get('compilerInputSha256')),
            'compiler provenance absent')
    require(bool(audit.get('sources')) and bool(audit.get('contracts')), 'source or bytecode provenance absent')
    require(audit['sources'] == policy.get('source_pins'), 'wrong source hashes')
    # Verify each source byte against the compiler report, rather than trusting hashes alone.
    for source, expected in audit['sources'].items():
        read_artifact(root, {'path': source, 'sha256': expected})
    for source, expected in audit.get('importedSources', {}).items():
        read_artifact(root, {'path': 'node_modules/' + source, 'sha256': expected})
    for artifact in audit['contracts'].values():
        for field, checksum in (('creationBytecode', 'creationBytecodeSha256'),
                                ('runtimeBytecodeTemplate', 'runtimeBytecodeTemplateSha256')):
            require(digest(bytes.fromhex(artifact[field])) == artifact[checksum], 'wrong bytecode hash')
    statement = json.loads(raw['circuit_statement'])
    require(statement.get('version') == config['circuit_version'] and bool(statement.get('statement')),
            'full circuit statement absent')
    mpc = json.loads(raw['mpc_report'])
    require(mpc.get('purpose') == 'production-ceremony' and mpc.get('status') == 'accepted', 'MPC report not accepted')
    for label in ('archive', 'replica'):
        entry = mpc.get(label, {})
        require(entry.get('status') == 'verified' and entry.get('sha256') == artifacts[f'mpc_{label}']['sha256'],
                f'MPC {label} verification missing')
    # Attestation authorities vouch for opaque evidence semantics. Bind the executor
    # attestation separately to the exact provenance and chain, then verify it.
    attestation = json.loads(raw['executor_attestation'])
    payload = attestation['payload']
    require(payload == {'domain': 'TCL-executor-attestation-v1', 'chain_id': chain,
                        'executor_provenance_sha256': artifacts['executor_provenance']['sha256']},
            'executor attestation binding')
    require(policy.get('attestation_quorum', 1) >= 1, 'attestation quorum required')
    verify_signatures(canonical(payload), attestation['signatures'], policy.get('attestation_keys', {}),
                      policy.get('attestation_quorum', 1))
    approved = {'domain': DOMAIN, 'deployment': config}
    require(envelope.get('payload') == approved, 'approval does not cover exact deployment')
    verify_signatures(canonical(approved), envelope.get('signatures', []),
                      policy['release_keys'], policy['quorum'])
    return digest(canonical(approved))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'policy', 'approvals', 'root'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        values = [json.loads(Path(getattr(args, name)).read_text()) for name in ('config', 'policy', 'approvals')]
        print(validate(*values, args.root))
    except Exception as error:
        parser.exit(1, f'release blocked: {error}\n')


if __name__ == '__main__':
    main()
