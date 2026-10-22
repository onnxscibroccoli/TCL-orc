import base64
import json
import tempfile
import unittest
import subprocess
import os
import sqlite3
import hashlib
from unittest.mock import patch
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from production.executor import canonical, verify, build_run_command, Ledger, snapshot, execute, task_order, execution_identity

class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.image = 'example/runner@sha256:' + 'a'*64
        self.policy = {'keys': {'ci': base64.b64encode(self.key.public_key().public_bytes_raw()).decode()}, 'images': [self.image], 'commands': [['python3', '-V']], 'secrets': {}, 'limits': {'tasks': 2, 'timeout': 30, 'memory_mb': 128, 'cpus': 1, 'pids': 32, 'tmpfs_mb': 16, 'total_timeout': 60}}
        self.plan = {'version': 1, 'source_sha256': 'b'*64, 'image': self.image, 'tasks': [{'id': 'check', 'command': ['python3', '-V'], 'timeout': 10}], 'resources': {'memory_mb': 64, 'cpus': 1, 'pids': 16, 'tmpfs_mb': 8}}
    def signed(self):
        return {'key_id': 'ci', 'plan': self.plan, 'signature': base64.b64encode(self.key.sign(b'TCL-EXECUTION-v1\n' + canonical(self.plan))).decode()}
    def test_dag_order_and_rejection(self):
        self.plan['tasks'] = [dict(self.plan['tasks'][0], id='child', depends=['parent']), dict(self.plan['tasks'][0], id='parent')]
        verify(self.signed(), self.policy)
        self.assertEqual([t['id'] for t in task_order(self.plan)], ['parent', 'child'])
        self.plan['tasks'][1]['depends'] = ['child']
        with self.assertRaises(ValueError): verify(self.signed(), self.policy)
        self.plan['tasks'][1]['depends'] = ['unknown']
        with self.assertRaises(ValueError): verify(self.signed(), self.policy)
    def test_fake_docker_state_provenance_timeout_and_signal(self):
        # Real subprocess/ledger lifecycle against a fake CLI, not sandbox evidence.
        self.policy['keys']['alias'] = self.policy['keys']['ci']
        for mode, expected in [('success', 'succeeded'), ('fail', 'failed'), ('timeout', 'uncertain'), ('signal', 'uncertain'), ('cleanup_failure', 'running')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp)/'docker'
                fake.write_text("""#!/usr/bin/env python3
import json, os, signal, sys, time
args = sys.argv[1:]
if args[0] == 'info': print(json.dumps(['name=rootless']))
elif args[0] == 'image': print(json.dumps([{'Config': {}}]))
elif args[0] == 'rm':
    if os.environ['FAKE_MODE'] == 'cleanup_failure': sys.exit(1)
    open(os.environ['FAKE_LOG'], 'a').write('removed\\n')
elif args[0] == 'run':
    mode = os.environ['FAKE_MODE']
    if mode == 'signal': os.kill(os.getppid(), signal.SIGTERM)
    if mode in ('signal', 'timeout', 'cleanup_failure'): time.sleep(60)
    sys.exit(1 if mode == 'fail' else 0)
""")
                fake.chmod(0o755)
                self.plan['tasks'][0]['timeout'] = 0.1 if mode in ('timeout', 'cleanup_failure') else 2
                self.plan['tasks'] = [self.plan['tasks'][0], dict(self.plan['tasks'][0], id='after', depends=['check'])]
                path = Path(tmp)/'state.sqlite'
                with patch.dict(os.environ, {'PATH': tmp + os.pathsep + os.environ['PATH'], 'FAKE_MODE': mode, 'FAKE_LOG': str(Path(tmp)/'removed')}), patch('production.executor.snapshot'):
                    if mode == 'cleanup_failure':
                        with self.assertRaises(subprocess.CalledProcessError):
                            execute(self.signed(), self.policy, '/unused', path)
                        with sqlite3.connect(path) as db:
                            self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()[0], 'running')
                        with self.assertRaises(ValueError): execute(self.signed(), self.policy, '/unused', path)
                        self.plan['tasks'] = [self.plan['tasks'][0]]
                        continue
                    output = execute(self.signed(), self.policy, '/unused', path)
                    records = output['provenance']['records']
                    self.assertEqual(records[0]['status'], expected)
                    self.assertEqual(records[1]['status'], 'succeeded' if mode == 'success' else 'blocked')
                    self.assertEqual(records[0]['policy_sha256'], hashlib.sha256(canonical(self.policy)).hexdigest())
                    self.assertEqual(output['sha256'], hashlib.sha256(canonical(output['provenance'])).hexdigest())
                    with self.assertRaises(ValueError): execute(self.signed(), self.policy, '/unused', path)
                    with self.assertRaises(ValueError): execute(dict(self.signed(), key_id='alias'), self.policy, '/unused', path)
                if mode in ('signal', 'timeout'):
                    self.assertTrue((Path(tmp)/'removed').exists())
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('SELECT status FROM jobs WHERE task=?', ('check',)).fetchone()[0], expected)
                self.plan['tasks'] = [self.plan['tasks'][0]]
    def test_signature_presentation_and_key_alias_cannot_replay(self):
        self.policy['keys']['alias'] = self.policy['keys']['ci']
        original = self.signed()
        alternate = dict(original, key_id='alias')
        verify(original, self.policy); verify(alternate, self.policy)
        with tempfile.TemporaryDirectory() as tmp, Ledger(Path(tmp)/'state.sqlite') as ledger:
            first = execution_identity(original, self.policy)
            ledger.begin(first, 'check')
            ledger.finish(first, 'check', 'succeeded', {})
            with self.assertRaises(ValueError):
                ledger.begin(execution_identity(alternate, self.policy), 'check')
        alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
        signature = original['signature']
        offset = alphabet.index(signature[-3])
        alternate = dict(original, signature=signature[:-3] + alphabet[offset + 1] + '==')
        self.assertEqual(base64.b64decode(signature, validate=True), base64.b64decode(alternate['signature'], validate=True))
        with self.assertRaises(ValueError): verify(alternate, self.policy)
        aliased_policy = dict(self.policy, keys={'another': self.policy['keys']['ci']})
        alternate = dict(original, key_id='another')
        self.assertEqual(execution_identity(original, self.policy), execution_identity(alternate, aliased_policy))
    def test_signature_and_policy(self):
        self.assertEqual(verify(self.signed(), self.policy), self.plan)
        signed = self.signed(); signed['plan']['tasks'][0]['command'] = ['sh']
        with self.assertRaises(ValueError): verify(signed, self.policy)
    def test_reject_unpinned_and_excess_budget(self):
        self.plan['image'] = 'runner:latest'
        with self.assertRaises(ValueError): verify(self.signed(), self.policy)
        self.plan['image'] = self.image; self.plan['resources']['cpus'] = 2
        with self.assertRaises(ValueError): verify(self.signed(), self.policy)
    def test_reject_unknown_fields_and_secret_values(self):
        self.plan['tasks'][0]['env'] = {'TOKEN': 'secret'}
        with self.assertRaises(ValueError): verify(self.signed(), self.policy)
    def test_command_hardening(self):
        cmd = build_run_command(self.plan, self.plan['tasks'][0], '/tmp/snapshot', 'job', {})
        for flag in ['--read-only', '--network=none', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=65532:65532']:
            self.assertIn(flag, cmd)
        self.assertIn('type=bind,src=/tmp/snapshot,dst=/workspace,readonly', cmd)
        self.assertNotIn('/var/run/docker.sock', ' '.join(cmd))
    def test_secret_reference_rejected(self):
        self.plan['tasks'][0]['secrets'] = ['unknown']
        with self.assertRaises(ValueError): verify(self.signed(), self.policy)
    def test_rootful_daemon_rejected(self):
        with patch('production.executor.subprocess.check_output', return_value=b'["name=seccomp"]'):
            with self.assertRaisesRegex(ValueError, 'rootless'):
                execute(self.signed(), self.policy, '/unused', '/unused')
    def test_lock_excludes_concurrent_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.sqlite'
            with Ledger(path):
                with self.assertRaisesRegex(ValueError, 'already active'):
                    with Ledger(path): pass
    def test_snapshot_clean_digest_and_dirty_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'repo'; source.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL)
            git('init'); git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.invalid')
            (source/'a').write_text('original')
            git('add', 'a'); git('commit', '-m', 'fixture')
            expected = hashlib.sha256(git('archive', '--format=tar', 'HEAD')).hexdigest()
            dest = Path(tmp)/'snapshot'; dest.mkdir()
            snapshot(source, dest, expected)
            self.assertEqual((dest/'a').read_text(), 'original')
            with self.assertRaisesRegex(ValueError, 'digest'):
                snapshot(source, dest, '0'*64)
            (source/'a').write_text('dirty')
            with self.assertRaisesRegex(ValueError, 'clean'):
                snapshot(source, dest, expected)
    def test_uncertain_no_replay_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Ledger(Path(tmp)/'state.sqlite') as ledger:
                ledger.begin('a', 'task')
                with self.assertRaises(ValueError): ledger.begin('a', 'task')
                ledger.begin('b', 'task')

if __name__ == '__main__': unittest.main()
