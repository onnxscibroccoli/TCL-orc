#!/usr/bin/env python3
"""Fail-closed rootless OCI executor. No exactly-once side-effect guarantee."""
import argparse
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def only(obj, names):
    if not isinstance(obj, dict) or set(obj) - set(names):
        raise ValueError('unknown fields')


def decode_base64(value):
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode('ascii') != value:
        raise ValueError('noncanonical base64')
    return decoded


def execution_identity(envelope, policy):
    # Key aliases and signature serialization are not execution authority changes.
    normalized = dict(policy)
    normalized['keys'] = sorted({decode_base64(value).hex() for value in policy['keys'].values()})
    return digest(canonical({
        'domain': 'TCL-execution-identity-v1',
        'plan': envelope['plan'],
        'authority': decode_base64(policy['keys'][envelope['key_id']]).hex(),
        'policy': normalized,
    }))


def verify(envelope, policy):
    try:
        only(envelope, ['key_id', 'plan', 'signature'])
        plan = envelope['plan']
        Ed25519PublicKey.from_public_bytes(decode_base64(policy['keys'][envelope['key_id']])).verify(decode_base64(envelope['signature']), b'TCL-EXECUTION-v1\n' + canonical(plan))
        only(plan, ['version', 'source_sha256', 'image', 'tasks', 'resources'])
        if plan['version'] != 1 or not re.fullmatch('[a-f0-9]{64}', plan['source_sha256']):
            raise ValueError('invalid plan identity')
        if not re.fullmatch(r'[^\s,@]+@sha256:[a-f0-9]{64}', plan['image']) or plan['image'] not in policy['images']:
            raise ValueError('image must be policy-approved digest')
        limits = policy['limits']
        resources = plan['resources']
        only(resources, ['memory_mb', 'cpus', 'pids', 'tmpfs_mb'])
        for name in ['memory_mb', 'cpus', 'pids', 'tmpfs_mb']:
            value = resources[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= limits[name] or (name != 'cpus' and type(value) is not int):
                raise ValueError('invalid resource budget')
        tasks = plan['tasks']
        if not isinstance(tasks, list) or not 0 < len(tasks) <= limits['tasks']:
            raise ValueError('task budget')
        seen, total = set(), 0
        for task in tasks:
            only(task, ['id', 'command', 'timeout', 'secrets', 'depends'])
            if not re.fullmatch('[A-Za-z0-9_-]{1,64}', task['id']) or task['id'] in seen:
                raise ValueError('invalid task ID')
            seen.add(task['id'])
            if task['command'] not in policy['commands'] or not isinstance(task['command'], list) or not task['command'] or not all(isinstance(x, str) and x and '\0' not in x for x in task['command']):
                raise ValueError('command not allowlisted')
            timeout = task['timeout']
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= limits['timeout']:
                raise ValueError('timeout budget')
            total += timeout
            names = task.get('secrets', [])
            if not isinstance(names, list) or len(set(names)) != len(names):
                raise ValueError('invalid secrets')
            for name in names:
                if not re.fullmatch('[A-Za-z0-9_-]{1,64}', name) or name not in policy['secrets']:
                    raise ValueError('secret reference not allowlisted')
        if total > limits['total_timeout']:
            raise ValueError('total timeout budget')
        task_order(plan)
        return plan
    except Exception as exc:
        raise ValueError('signature or policy validation failed') from exc


def task_order(plan):
    tasks = {task['id']: task for task in plan['tasks']}
    visiting, visited, ordered = set(), set(), []
    def visit(ident):
        if ident not in tasks or ident in visiting:
            raise ValueError('unknown or cyclic dependency')
        if ident in visited:
            return
        visiting.add(ident)
        dependencies = tasks[ident].get('depends', [])
        if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError('invalid dependencies')
        for dep in dependencies:
            visit(dep)
        visiting.remove(ident)
        visited.add(ident)
        ordered.append(tasks[ident])
    for ident in tasks:
        visit(ident)
    return ordered


@contextmanager
def termination_guard():
    # Record signals rather than raising inside Popen's launch/assignment window.
    stopped = [False]
    def terminate(signum, frame):
        stopped[0] = True
    previous = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield stopped
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def snapshot(source, destination, expected):
    source = Path(source).resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL)
    if Path(git('rev-parse', '--show-toplevel').decode().strip()) != source:
        raise ValueError('source must be repository root')
    if git('status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none') or git('ls-files', '--others'):
        raise ValueError('source must be clean, including untracked files')
    # Archive the object database, never mutable working-tree bytes.
    revision = git('rev-parse', 'HEAD').decode().strip()
    tree = git('ls-tree', '-r', revision)
    if b'160000 commit ' in tree:
        raise ValueError('submodules unsupported')
    archive = git('archive', '--format=tar', revision)
    if digest(archive) != expected:
        raise ValueError('source archive digest mismatch')
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # Symlinks could address container paths outside the immutable source.
        if any(not (item.isfile() or item.isdir()) for item in tar.getmembers()):
            raise ValueError('only regular source files and directories supported')
        tar.extractall(destination, filter='data')
    for root, dirs, files in os.walk(destination):
        for name in files:
            path = Path(root) / name
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        Path(root).chmod(0o555)


def build_run_command(plan, task, source, name, secrets):
    resources = plan['resources']
    args = ['docker', 'run', '--name', name, '--rm', '--pull=never', '--user=65532:65532', '--read-only', '--network=none', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--log-driver=none', '--ipc=none', '--pids-limit='+str(resources['pids']), '--memory='+str(resources['memory_mb'])+'m', '--memory-swap='+str(resources['memory_mb'])+'m', '--cpus='+str(resources['cpus']), '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size='+str(resources['tmpfs_mb'])+'m', '--workdir=/workspace', '--mount', 'type=bind,src='+str(source)+',dst=/workspace,readonly']
    for key in task.get('secrets', []):
        if not Path(secrets[key]).is_absolute():
            raise ValueError('secret paths must be absolute')
        path = Path(secrets[key]).resolve(strict=True)
        if not path.is_file() or ',' in str(path):
            raise ValueError('invalid secret file')
        args += ['--mount', 'type=bind,src='+str(path)+',dst=/run/secrets/'+key+',readonly']
    # Override image ENTRYPOINT, so only the approved argv executes.
    return args + ['--entrypoint', task['command'][0], plan['image'], *task['command'][1:]]


class Ledger:
    def __init__(self, path):
        self.path = str(path)
    def __enter__(self):
        self.lock = open(self.path+'.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError('executor already active')
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (identity TEXT, task TEXT, status TEXT, metadata TEXT, PRIMARY KEY(identity,task))')
        return self
    def begin(self, identity, task):
        try:
            self.db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?)', (identity, task, 'running', '{}'))
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError('task already recorded; running is uncertain and requires operator reconciliation') from exc
    def finish(self, identity, task, status, metadata):
        result = self.db.execute('UPDATE jobs SET status=?, metadata=? WHERE identity=? AND task=? AND status=\'running\'', (status, json.dumps(metadata), identity, task))
        if result.rowcount != 1:
            raise ValueError('ledger compare-and-swap failed')
        self.db.commit()
    def __exit__(self, *_):
        self.db.close()
        self.lock.close()


def execute(envelope, policy, source, ledger_path, signing_key=None):
    plan = verify(envelope, policy)
    policy_sha256 = digest(canonical(policy))
    envelope_sha256 = digest(canonical(envelope))
    identity = execution_identity(envelope, policy)
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .SecurityOptions}}']))
    if not any('rootless' in option for option in info):
        raise ValueError('rootless Docker daemon required')
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', plan['image']]))[0]
    if image.get('Config', {}).get('Volumes'):
        raise ValueError('images declaring writable volumes are unsupported')
    records = []
    with Ledger(ledger_path) as ledger, tempfile.TemporaryDirectory(prefix='tcl-source-') as tmp, termination_guard() as interrupted:
        snapshot(source, tmp, plan['source_sha256'])
        stopped = False
        for task in task_order(plan):
            name = 'tcl-'+identity[:32]+'-'+task['id'].lower()
            command = build_run_command(plan, task, tmp, name, policy.get('secrets', {}))
            ledger.begin(identity, task['id'])
            if stopped or interrupted[0]:
                code, status = None, 'blocked'
            else:
                proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    deadline = time.monotonic() + task['timeout']
                    while True:
                        if interrupted[0]:
                            raise KeyboardInterrupt('termination requested')
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, task['timeout'])
                        try:
                            code = proc.wait(timeout=min(remaining, 0.1))
                            break
                        except subprocess.TimeoutExpired:
                            pass
                    status = 'succeeded' if code == 0 else 'failed'
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    try:
                        subprocess.run(['docker', 'rm', '--force', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
                    finally:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait()
                    code, status = None, 'uncertain'
            record = {'schema': 'tcl-execution/v1', 'plan_sha256': digest(canonical(plan)), 'envelope_sha256': envelope_sha256, 'policy_sha256': policy_sha256, 'job_identity': identity, 'source_sha256': plan['source_sha256'], 'image': plan['image'], 'task_id': task['id'], 'command_sha256': digest(canonical(task['command'])), 'status': status, 'exit_code': code, 'output': 'discarded'}
            ledger.finish(identity, task['id'], status, record)
            records.append(record)
            if status != 'succeeded':
                stopped = True
    result = {'schema': 'tcl-provenance/v1', 'records': records}
    output = {'provenance': result, 'sha256': digest(canonical(result))}
    if signing_key:
        output['signature'] = base64.b64encode(Ed25519PrivateKey.from_private_bytes(Path(signing_key).read_bytes()).sign(canonical(result))).decode()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--provenance-key')
    args = parser.parse_args()
    result = execute(json.loads(Path(args.plan).read_text()), json.loads(Path(args.policy).read_text()), args.source, args.ledger, args.provenance_key)
    print(json.dumps(result, sort_keys=True))
    return 0 if all(record['status'] == 'succeeded' for record in result['provenance']['records']) else 1

if __name__ == '__main__':
    raise SystemExit(main())
