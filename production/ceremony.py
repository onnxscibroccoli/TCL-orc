#!/usr/bin/env python3
"""Verify externally produced Groth16 ceremonies. No participant secrets are handled."""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REQUIRED = {'source', 'circuit', 'r1cs', 'wasm', 'lock', 'toolchain', 'ptau', 'proving_key', 'verification_key', 'verifier'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def message(domain, payload):
    return b'TCL-CEREMONY-v1:' + domain.encode() + b'\n' + canonical(payload)


def sha256(path):
    path = Path(path)
    h = hashlib.sha256()
    if path.is_dir():
        for file in sorted(path.rglob('*')):
            if file.is_symlink() and (not file.is_file() or not file.resolve().is_relative_to(path.resolve())):
                raise ValueError('symlink escapes pinned directory or aliases directory')
            if file.is_file():
                h.update(canonical(str(file.relative_to(path))))
                h.update(bytes.fromhex(sha256(file)))
    else:
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
    return h.hexdigest()


def require_hash(path, digest):
    if not isinstance(digest, str) or len(digest) != 64 or sha256(path) != digest:
        raise ValueError(f'artifact hash mismatch: {path}')


def authenticate(public, signature, domain, payload):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(bytes.fromhex(signature), message(domain, payload))
    except Exception as exc:
        raise ValueError('invalid authenticated approval') from exc


def check_policy(policy):
    if policy['version'] != 1:
        raise ValueError('unsupported policy version')
    keys = list(policy['contributors'].values()) + list(policy['release_approvers'].values())
    if len(set(keys)) != len(keys) or len(set(bytes.fromhex(k) for k in keys)) != len(keys):
        raise ValueError('duplicate identity key, including across roles')
    for key in keys:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key))
    if type(policy['phase_quorum']) is not int or not 2 <= policy['phase_quorum'] <= len(policy['contributors']):
        raise ValueError('invalid phase quorum')
    if type(policy['release_quorum']) is not int or not 2 <= policy['release_quorum'] <= len(policy['release_approvers']):
        raise ValueError('invalid release quorum')
    for phase in ('ptau', 'zkey'):
        beacon = policy['beacons'][phase]
        if type(beacon['iterations']) is not int or len(bytes.fromhex(beacon['value'])) < 32 or not 10 <= beacon['iterations'] <= 63 or not beacon['source'] or not 1 <= len(beacon['name'].encode()) <= 64:
            raise ValueError('invalid precommitted public beacon')


def check_extension(before, after):
    if len(after) != len(before) + 1 or after[:-1] != before:
        raise ValueError('transcript is not exactly one contribution extending predecessor')


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_directory_durable(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        fsync_directory(directory.parent)
        fsync_directory(directory)


def durable_tree(path):
    for file in path.rglob('*'):
        if file.is_file():
            file.chmod(0o444)
            with file.open('rb') as stream:
                os.fsync(stream.fileno())
    for directory in sorted((p for p in path.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        fsync_directory(directory)
    fsync_directory(path)


def rename_directory_exclusive(source, target):
    # Linux RENAME_NOREPLACE supplies the no-overwrite guarantee even against
    # a process that does not cooperate with the archive lock.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('atomic directory archival requires Linux renameat2')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileExistsError(code, os.strerror(code), str(target))
        raise OSError(code, os.strerror(code), str(target))


def archive_file(source, digest, archive, replica):
    archive, replica = Path(archive).resolve(), Path(replica).resolve()
    if archive == replica or archive in replica.parents or replica in archive.parents:
        raise ValueError('archive and independent replica must be separate roots')
    source = Path(source)
    require_hash(source, digest)
    for root in (archive, replica):
        if source.is_dir() and (source.resolve() == root or source.resolve() in root.parents):
            raise ValueError('archive cannot reside inside archived source')
        make_directory_durable(root)
        fsync_directory(root.parent)
        # All writers use this per-root advisory lock. Stale temporary objects
        # left by a killed writer can then be removed safely before retrying.
        lock_fd = os.open(root / '.archive.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'r+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for stale in root.glob('.staging-*'):
                if stale.is_symlink() or not stale.is_dir():
                    stale.unlink()
                else:
                    shutil.rmtree(stale)
            target = root / digest
            if target.is_symlink():
                raise ValueError('archive symlink')
            if target.exists():
                if source.is_dir() != target.is_dir():
                    raise ValueError('archive object type mismatch')
                require_hash(target, digest)
                fsync_directory(root)
                continue
            with tempfile.TemporaryDirectory(prefix='.staging-', dir=root) as temporary:
                staged = Path(temporary) / 'object'
                if source.is_dir():
                    shutil.copytree(source, staged, symlinks=False)
                    require_hash(staged, digest)
                    durable_tree(staged)
                    try:
                        rename_directory_exclusive(staged, target)
                    except FileExistsError:
                        if target.is_symlink() or not target.is_dir():
                            raise ValueError('archive object replaced')
                        require_hash(target, digest)
                else:
                    with staged.open('xb') as out, source.open('rb') as incoming:
                        shutil.copyfileobj(incoming, out)
                        out.flush()
                        os.fchmod(out.fileno(), 0o444)
                        os.fsync(out.fileno())
                    require_hash(staged, digest)
                    try:
                        os.link(staged, target, follow_symlinks=False)
                    except FileExistsError:
                        if target.is_symlink() or not target.is_file():
                            raise ValueError('archive object replaced')
                        require_hash(target, digest)
                fsync_directory(root)
            fsync_directory(root)
            require_hash(target, digest)


class SnarkJS:
    def __init__(self, toolchain):
        self.root = Path(toolchain).resolve()
        self.cli = self.root / 'snarkjs/build/cli.cjs'
        if json.loads((self.root / 'snarkjs/package.json').read_text())['version'] != '0.7.6':
            raise ValueError('unsupported snarkjs version')

    def run(self, *args):
        result = subprocess.run(['node', str(self.cli), *map(str, args)], capture_output=True, text=True, check=False)
        if result.returncode or 'INVALID' in result.stdout.upper() or 'ERROR' in result.stdout.upper():
            raise ValueError('snarkjs failed: ' + result.stdout[-4000:] + result.stderr[-4000:])
        return result.stdout

    def verify(self, phase, path, r1cs, ptau):
        output = self.run('powersoftau', 'verify', path) if phase == 'ptau' else self.run('zkey', 'verify', r1cs, ptau, path)
        marker = 'Powers of Tau Ok!' if phase == 'ptau' else 'ZKey Ok!'
        if marker.lower() not in output.lower():
            raise ValueError('snarkjs did not affirm mathematical verification')

    def metadata(self, phase, path):
        # Read only transcript metadata rather than exporting huge powers-of-tau arrays.
        root = self.root
        js = '''
import * as b from BIN;
import * as p from PTAU;
import * as z from ZKEY;
import {getCurveFromQ} from CURVES;
import {createHash} from 'node:crypto';
const phase=process.argv[1], file=process.argv[2];
const {fd,sections}=await b.readBinFile(file,phase==='ptau'?'ptau':'zkey',phase==='ptau'?1:2);
const header=phase==='ptau'?await p.readPTauHeader(fd,sections):await z.readHeader(fd,sections,false);
const curve=phase==='ptau'?header.curve:await getCurveFromQ(header.q);
const cs=phase==='ptau'?await p.readContributions(fd,curve,sections):(await z.readMPCParams(fd,curve,sections)).contributions;
const norm=(x)=>typeof x==='bigint'?x.toString():ArrayBuffer.isView(x)?Array.from(x):Array.isArray(x)?x.map(norm):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,norm(x[k])])):x;
console.log(JSON.stringify({contributions:cs.map(c=>createHash('sha256').update(JSON.stringify(norm(c))).digest('hex')),types:cs.map(c=>c.type),power:header.power,nPublic:header.nPublic,protocol:header.protocol}));
await fd.close(); await curve.terminate();
'''
        imports = {'BIN': root / '@iden3/binfileutils/src/binfileutils.js', 'PTAU': root / 'snarkjs/src/powersoftau_utils.js', 'ZKEY': root / 'snarkjs/src/zkey_utils.js', 'CURVES': root / 'snarkjs/src/curves.js'}
        for name, file in imports.items():
            js = js.replace(name, json.dumps(file.as_uri()))
        result = subprocess.run(['node', '--input-type=module', '-e', js, phase, str(path)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('cannot read transcript metadata: ' + result.stderr[-1000:])
        return json.loads(result.stdout)


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON number')))


def verify(policy, manifest, base, archive=None, replica=None, backend=None, audit=False):
    if not audit and (archive is None or replica is None):
        raise ValueError('release verification requires archive and independent replica')
    check_policy(policy)
    if manifest['version'] != 1 or manifest['policy_sha256'] != hashlib.sha256(canonical(policy)).hexdigest():
        raise ValueError('wrong policy or manifest version')
    artifacts = manifest['artifacts']
    if set(artifacts) != REQUIRED:
        raise ValueError('missing or unknown required artifact')
    paths = {}
    for name, artifact in artifacts.items():
        path = (Path(base) / artifact['path']).resolve()
        require_hash(path, artifact['sha256'])
        paths[name] = path
    for name, expected in policy['pinned_artifacts'].items():
        if artifacts[name]['sha256'] != expected:
            raise ValueError('wrong source or toolchain pin')
    if not {'source', 'circuit', 'lock', 'toolchain'} <= set(policy['pinned_artifacts']):
        raise ValueError('source/toolchain pins required in trusted policy')
    backend = backend or SnarkJS(paths['toolchain'])
    paths['circuit'].relative_to(paths['source'])
    compiler_package = load(paths['toolchain'] / 'circom2/package.json')
    if compiler_package['version'] != '0.2.22' or compiler_package['bin']['circom2'] != './cli.js':
        raise ValueError('unsupported pinned compiler')
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(['node', str(paths['toolchain'] / 'circom2/cli.js'), str(paths['circuit']), '--r1cs', '--wasm', '-l', str(paths['toolchain']), '-o', tmp], cwd=paths['source'], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('circuit reproducibility compile failed: ' + result.stderr[-2000:])
        stem = paths['circuit'].stem
        require_hash(Path(tmp) / (stem + '.r1cs'), artifacts['r1cs']['sha256'])
        require_hash(Path(tmp) / (stem + '_js') / (stem + '.wasm'), artifacts['wasm']['sha256'])
    seen_receipts = set()
    transcript_objects = []
    for phase, final in [('ptau', 'ptau'), ('zkey', 'proving_key')]:
        chain = manifest['phases'][phase]
        previous = chain['initial']
        initial_path = (Path(base) / previous['path']).resolve()
        require_hash(initial_path, previous['sha256'])
        transcript_objects.append((initial_path, previous['sha256']))
        metadata = backend.metadata(phase, initial_path)
        with tempfile.TemporaryDirectory() as tmp:
            genesis = Path(tmp) / 'genesis'
            if phase == 'ptau':
                backend.run('powersoftau', 'new', 'bn128', str(metadata['power']), genesis)
            else:
                backend.run('groth16', 'setup', paths['r1cs'], paths['ptau'], genesis)
            require_hash(genesis, previous['sha256'])
        if metadata['contributions']:
            raise ValueError('initial transcript must have zero contributions')
        identities = set()
        private_contributors = set()
        beacon_seen = False
        for receipt in chain['receipts']:
            payload = receipt['payload']
            identity = payload['contributor']
            rid = hashlib.sha256(canonical(payload)).hexdigest()
            if rid in seen_receipts or identity in identities:
                raise ValueError('duplicate receipt or contributor identity')
            seen_receipts.add(rid)
            identities.add(identity)
            if payload['phase'] != phase or payload['predecessor_sha256'] != previous['sha256'] or payload['policy_sha256'] != manifest['policy_sha256'] or payload['r1cs_sha256'] != artifacts['r1cs']['sha256']:
                raise ValueError('wrong receipt chain or circuit')
            authenticate(policy['contributors'][identity], receipt['signature'], 'receipt', payload)
            output = (Path(base) / payload['path']).resolve()
            require_hash(output, payload['output_sha256'])
            transcript_objects.append((output, payload['output_sha256']))
            backend.verify(phase, output, paths['r1cs'], paths['ptau'])
            after = backend.metadata(phase, output)
            check_extension(metadata['contributions'], after['contributions'])
            if beacon_seen:
                raise ValueError('beacon must be last contribution')
            is_beacon = after['types'][-1] == 1
            if is_beacon != (payload['kind'] == 'beacon'):
                raise ValueError('receipt contribution type mismatch')
            if not is_beacon:
                if payload['kind'] != 'contribution':
                    raise ValueError('unknown contribution kind')
                private_contributors.add(identity)
            if is_beacon:
                beacon_seen = True
                beacon = policy['beacons'][phase]
                if payload['beacon'] != beacon:
                    raise ValueError('wrong beacon commitment')
                # Reproduce the deterministic beacon, binding the precommitted public value.
                with tempfile.TemporaryDirectory() as tmp:
                    expected = Path(tmp) / 'beacon'
                    backend.run('powersoftau' if phase == 'ptau' else 'zkey', 'beacon', initial_path, expected, beacon['value'], str(beacon['iterations']), '-n=' + beacon['name'])
                    require_hash(expected, payload['output_sha256'])
            metadata = after
            previous = {'path': payload['path'], 'sha256': payload['output_sha256']}
            initial_path = output
        if len(private_contributors) < policy['phase_quorum'] or not beacon_seen:
            raise ValueError('insufficient phase quorum or missing final public beacon')
        if phase == 'ptau':
            # prepare phase2 changes encoding without adding a contribution; reproduce it.
            with tempfile.TemporaryDirectory() as tmp:
                prepared = Path(tmp) / 'prepared.ptau'
                backend.run('powersoftau', 'prepare', 'phase2', initial_path, prepared)
                require_hash(prepared, artifacts[final]['sha256'])
        elif previous['sha256'] != artifacts[final]['sha256'] or metadata.get('nPublic') != 5 or metadata.get('protocol') != 'groth16':
            raise ValueError('wrong final Groth16 key or public input count')
    with tempfile.TemporaryDirectory() as tmp:
        vk, sol = Path(tmp) / 'vk.json', Path(tmp) / 'Verifier.sol'
        backend.run('zkey', 'export', 'verificationkey', paths['proving_key'], vk)
        backend.run('zkey', 'export', 'solidityverifier', paths['proving_key'], sol)
        require_hash(vk, artifacts['verification_key']['sha256'])
        require_hash(sol, artifacts['verifier']['sha256'])
    release = {k: v for k, v in manifest.items() if k != 'approvals'}
    approvers = set()
    for approval in manifest['approvals']:
        identity = approval['approver']
        if identity in approvers:
            raise ValueError('duplicate release approver')
        authenticate(policy['release_approvers'][identity], approval['signature'], 'release', release)
        approvers.add(identity)
    if len(approvers) < policy['release_quorum']:
        raise ValueError('insufficient release quorum')
    if (archive is None) != (replica is None):
        raise ValueError('both archive and independent replica are required')
    if archive:
        with tempfile.TemporaryDirectory() as tmp:
            for name, value in [('policy', policy), ('manifest', manifest)]:
                record = Path(tmp) / name
                record.write_bytes(canonical(value))
                archive_file(record, sha256(record), archive, replica)
        objects = [(paths[n], a['sha256']) for n, a in artifacts.items()]
        for chain in manifest['phases'].values():
            objects.append((Path(base) / chain['initial']['path'], chain['initial']['sha256']))
            objects.extend((Path(base) / r['payload']['path'], r['payload']['output_sha256']) for r in chain['receipts'])
        for path, digest in objects:
            archive_file(path, digest, archive, replica)
    for path, digest in transcript_objects:
        require_hash(path, digest)
    # Catch replacement while external verification ran.
    for name, artifact in artifacts.items():
        require_hash(paths[name], artifact['sha256'])
    return hashlib.sha256(canonical(release)).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('verify', 'audit'):
        check = sub.add_parser(name)
        check.add_argument('--policy', required=True)
        check.add_argument('--manifest', required=True)
        check.add_argument('--archive', required=name == 'verify')
        check.add_argument('--replica', required=name == 'verify')
    digest = sub.add_parser('digest')
    digest.add_argument('path')
    signed = sub.add_parser('signing-message')
    signed.add_argument('--domain', choices=['receipt', 'release'], required=True)
    signed.add_argument('payload')
    args = parser.parse_args()
    try:
        if args.command == 'digest':
            print(sha256(args.path))
        elif args.command == 'signing-message':
            sys.stdout.buffer.write(message(args.domain, load(args.payload)))
        else:
            print(verify(load(args.policy), load(args.manifest), Path(args.manifest).resolve().parent, args.archive, args.replica, audit=args.command == 'audit'))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        parser.exit(1, f'ceremony rejected: {exc}\n')


if __name__ == '__main__':
    main()
