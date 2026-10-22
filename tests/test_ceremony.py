import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
spec = importlib.util.spec_from_file_location('ceremony', Path(__file__).parents[1] / 'production/ceremony.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class CeremonyTests(unittest.TestCase):
    def test_signature_domains_and_mutations(self):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes_raw().hex()
        payload = {'phase': 'ptau', 'output': 'a' * 64}
        signature = key.sign(c.message('receipt', payload)).hex()
        c.authenticate(public, signature, 'receipt', payload)
        for domain, changed in [('release', payload), ('receipt', dict(payload, output='b' * 64))]:
            with self.assertRaises(ValueError):
                c.authenticate(public, signature, domain, changed)

    def test_archive_replacement_and_replica_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.write_bytes(b'ceremony')
            digest = c.sha256(source)
            c.archive_file(source, digest, root / 'archive', root / 'replica')
            (root / 'replica' / digest).chmod(0o600)
            (root / 'replica' / digest).write_bytes(b'altered')
            with self.assertRaises(ValueError):
                c.archive_file(source, digest, root / 'archive', root / 'replica')
            with self.assertRaises(ValueError):
                c.archive_file(source, digest, root / 'archive', root / 'archive')

    def test_atomic_archive_retry_and_parent_fsync(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'; source.write_bytes(b'complete object')
            digest = c.sha256(source)
            archive, replica = root / 'archive', root / 'replica'
            def interrupted(incoming, out):
                out.write(b'partial')
                raise OSError('injected interrupted copy')
            with patch.object(c.shutil, 'copyfileobj', side_effect=interrupted):
                with self.assertRaises(OSError):
                    c.archive_file(source, digest, archive, replica)
            self.assertFalse((archive / digest).exists())
            self.assertFalse(list(archive.glob('.staging-*')))
            abandoned = archive / '.staging-abandoned'
            abandoned.mkdir(); (abandoned / 'partial').write_bytes(b'crashed writer')
            with patch.object(c, 'fsync_directory', wraps=c.fsync_directory) as synced:
                c.archive_file(source, digest, archive, replica)
            self.assertIn(archive, [call.args[0] for call in synced.call_args_list])
            self.assertIn(replica, [call.args[0] for call in synced.call_args_list])
            self.assertFalse(abandoned.exists())
            self.assertEqual((archive / digest).read_bytes(), source.read_bytes())
            tree = root / 'tree'; tree.mkdir(); (tree / 'a').write_bytes(b'a')
            tree_hash = c.sha256(tree)
            def interrupted_tree(src, dst, **kwargs):
                Path(dst).mkdir(); (Path(dst) / 'a').write_bytes(b'partial')
                raise OSError('injected directory interruption')
            with patch.object(c.shutil, 'copytree', side_effect=interrupted_tree):
                with self.assertRaises(OSError):
                    c.archive_file(tree, tree_hash, archive, replica)
            self.assertFalse((archive / tree_hash).exists())
            c.archive_file(tree, tree_hash, archive, replica)
            c.require_hash(archive / tree_hash, tree_hash)
            rival = root / 'rival'; rival.mkdir(); (rival / 'a').write_bytes(b'wrong')
            with self.assertRaises(FileExistsError):
                c.rename_directory_exclusive(rival, archive / tree_hash)
            c.require_hash(archive / tree_hash, tree_hash)

    def test_duplicate_keys_across_roles_rejected(self):
        key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
        with self.assertRaises(ValueError):
            c.check_policy({'version': 1, 'contributors': {'alice': key}, 'release_approvers': {'bob': key}, 'phase_quorum': 1, 'release_quorum': 1})

    def test_prefix_rejects_fork_and_skipped_contribution(self):
        c.check_extension(['a'], ['a', 'b'])
        for after in [['z', 'b'], ['a'], ['a', 'b', 'c']]:
            with self.assertRaises(ValueError):
                c.check_extension(['a'], after)

    def test_nonfinite_json_and_single_party_policy_rejected(self):
        with self.assertRaises(ValueError):
            c.canonical({'untrusted': float('nan')})
        keys = [Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex() for _ in range(4)]
        policy = {'version': 1, 'contributors': {'a': keys[0], 'b': keys[1]}, 'release_approvers': {'c': keys[2], 'd': keys[3]}, 'phase_quorum': 1, 'release_quorum': 2}
        with self.assertRaises(ValueError):
            c.check_policy(policy)
        policy.update(phase_quorum=2, release_quorum=1)
        with self.assertRaises(ValueError):
            c.check_policy(policy)

    def test_hash_failure_before_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'artifact'; f.write_bytes(b'wrong')
            with self.assertRaises(ValueError):
                c.require_hash(f, '0' * 64)


class SnarkJSIntegrationTests(unittest.TestCase):
    def test_real_transcript_verification_and_fork_rejection(self):
        repo = Path(__file__).parents[1]
        if not (repo / 'node_modules/snarkjs/build/cli.cjs').exists():
            self.skipTest('npm ci required')
        backend = c.SnarkJS(repo / 'node_modules')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial, first, second, fork = [root / x for x in ['initial.ptau', 'first.ptau', 'second.ptau', 'fork.ptau']]
            backend.run('powersoftau', 'new', 'bn128', '4', initial)
            backend.run('powersoftau', 'beacon', initial, first, 'ab' * 32, '10', '-n=test-first')
            backend.run('powersoftau', 'beacon', first, second, 'cd' * 32, '10', '-n=test-second')
            backend.run('powersoftau', 'beacon', initial, fork, 'ef' * 32, '10', '-n=test-fork')
            backend.verify('ptau', first, None, None)
            before = backend.metadata('ptau', first)
            after = backend.metadata('ptau', second)
            c.check_extension(before['contributions'], after['contributions'])
            with self.assertRaises(ValueError):
                c.check_extension(before['contributions'], backend.metadata('ptau', fork)['contributions'])
            self.assertEqual(after['types'], [1, 1])
            first.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                backend.verify('ptau', first, None, None)

    def test_complete_synthetic_release_and_cli(self):
        import hashlib
        import json
        import os
        import subprocess
        repo = Path(__file__).parents[1]
        if not (repo / 'node_modules/snarkjs/build/cli.cjs').exists():
            self.skipTest('npm ci required')
        backend = c.SnarkJS(repo / 'node_modules')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'; source.mkdir()
            circuit = source / 'tiny.circom'
            circuit.write_text('pragma circom 2.1.6; template T(){signal input a;signal input b;signal input c;signal input d;signal input e;signal input x;a===x*x;b===a*x;c===b*x;d===c*x;e===d*x;} component main {public [a,b,c,d,e]}=T();')
            subprocess.run(['node', str(repo / 'node_modules/circom2/cli.js'), str(circuit), '--r1cs', '--wasm', '-o', str(root)], check=True, capture_output=True)
            r1cs = root / 'tiny.r1cs'; ptau = root / 'final.ptau'; zkey = root / 'final.zkey'
            private = Ed25519PrivateKey.generate(); private2 = Ed25519PrivateKey.generate(); release2 = Ed25519PrivateKey.generate(); beacon_key = Ed25519PrivateKey.generate(); release_key = Ed25519PrivateKey.generate()
            public = lambda k: k.public_key().public_bytes_raw().hex()
            policy = {'version': 1, 'contributors': {'private': public(private), 'private2': public(private2), 'beacon': public(beacon_key)}, 'release_approvers': {'release': public(release_key), 'release2': public(release2)}, 'phase_quorum': 2, 'release_quorum': 2, 'pinned_artifacts': {}, 'beacons': {phase: {'source': 'synthetic test only', 'value': 'ab' * 32, 'iterations': 10, 'name': 'test ' + phase} for phase in ['ptau', 'zkey']}}
            files = {'source': source, 'circuit': circuit, 'r1cs': r1cs, 'wasm': root / 'tiny_js/tiny.wasm', 'lock': repo / 'package-lock.json', 'toolchain': repo / 'node_modules', 'ptau': ptau, 'proving_key': zkey, 'verification_key': root / 'vk.json', 'verifier': root / 'Verifier.sol'}
            for name in ['source', 'circuit', 'lock', 'toolchain']:
                policy['pinned_artifacts'][name] = c.sha256(files[name])
            policy_hash = hashlib.sha256(c.canonical(policy)).hexdigest()
            manifest = {'version': 1, 'policy_sha256': policy_hash, 'artifacts': {}, 'phases': {}, 'approvals': []}
            for phase in ['ptau', 'zkey']:
                initial, contributed, final = [root / (phase + suffix) for suffix in ['.initial', '.contributed', '.beacon']]
                if phase == 'ptau':
                    backend.run('powersoftau', 'new', 'bn128', '4', initial)
                else:
                    backend.run('groth16', 'setup', r1cs, ptau, initial)
                api = 'powersOfTau' if phase == 'ptau' else 'zKey'
                js = "const s=require(process.argv[1]); const fs=require('fs'); s[process.argv[2]].contribute(process.argv[3],process.argv[4],'test-private',fs.readFileSync(0,'utf8')).then(()=>process.exit(0)).catch(e=>{console.error(e);process.exit(1)});"
                subprocess.run(['node', '-e', js, str(repo / 'node_modules/snarkjs'), api, str(initial), str(contributed)], input=os.urandom(64).hex(), text=True, check=True, capture_output=True)
                contributed2 = root / (phase + '.contributed2')
                subprocess.run(['node', '-e', js, str(repo / 'node_modules/snarkjs'), api, str(contributed), str(contributed2)], input=os.urandom(64).hex(), text=True, check=True, capture_output=True)
                beacon = policy['beacons'][phase]
                backend.run('powersoftau' if phase == 'ptau' else 'zkey', 'beacon', contributed2, final, beacon['value'], str(beacon['iterations']), '-n=' + beacon['name'])
                chain = {'initial': {'path': str(initial), 'sha256': c.sha256(initial)}, 'receipts': []}
                previous = initial
                for identity, key, output, kind in [('private', private, contributed, 'contribution'), ('private2', private2, contributed2, 'contribution'), ('beacon', beacon_key, final, 'beacon')]:
                    payload = {'phase': phase, 'contributor': identity, 'kind': kind, 'policy_sha256': policy_hash, 'r1cs_sha256': c.sha256(r1cs), 'predecessor_sha256': c.sha256(previous), 'path': str(output), 'output_sha256': c.sha256(output)}
                    if kind == 'beacon':
                        payload['beacon'] = beacon
                    chain['receipts'].append({'payload': payload, 'signature': key.sign(c.message('receipt', payload)).hex()})
                    previous = output
                manifest['phases'][phase] = chain
                if phase == 'ptau':
                    backend.run('powersoftau', 'prepare', 'phase2', final, ptau)
                else:
                    zkey.write_bytes(final.read_bytes())
            backend.run('zkey', 'export', 'verificationkey', zkey, files['verification_key'])
            backend.run('zkey', 'export', 'solidityverifier', zkey, files['verifier'])
            manifest['artifacts'] = {name: {'path': str(path), 'sha256': c.sha256(path)} for name, path in files.items()}
            release = {k: v for k, v in manifest.items() if k != 'approvals'}
            manifest['approvals'] = [{'approver': identity, 'signature': key.sign(c.message('release', release)).hex()} for identity, key in [('release', release_key), ('release2', release2)]]
            # Verify exact command used by operators; no signing keys or entropy are persisted.
            policy_file, manifest_file = root / 'policy.json', root / 'manifest.json'
            policy_file.write_text(json.dumps(policy)); manifest_file.write_text(json.dumps(manifest))
            result = subprocess.run(['python3', str(repo / 'production/ceremony.py'), 'verify', '--policy', str(policy_file), '--manifest', str(manifest_file), '--archive', str(root / 'archive'), '--replica', str(root / 'replica')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), hashlib.sha256(c.canonical(release)).hexdigest())
            manifest['artifacts']['circuit']['sha256'] = '0' * 64
            with self.assertRaises(ValueError):
                c.verify(policy, manifest, root, backend=backend, audit=True)


if __name__ == '__main__':
    unittest.main()
