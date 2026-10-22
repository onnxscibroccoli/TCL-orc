#!/usr/bin/env python3
"""Execute a trusted task DAG with durable attempts and dependency gates.

Task commands have the same authority as the caller. This is not a sandbox.
Interrupted running tasks require a new state database after operator reconciliation;
we deliberately do not promise exactly-once external side effects after a crash.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}, sort_keys=True), flush=True)


def validate(plan, workspace):
    if not isinstance(plan, dict) or not isinstance(plan.get('tasks'), list):
        raise ValueError('plan requires tasks array')
    tasks = {}
    for raw in plan['tasks']:
        task = dict(raw)
        ident = task.get('id')
        if not isinstance(ident, str) or not ident or ident in tasks:
            raise ValueError('unique nonempty task id required')
        command = task.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x and '\0' not in x for x in command):
            raise ValueError('command must be a nonempty argv array')
        depends = task.get('depends', [])
        if not isinstance(depends, list) or not all(isinstance(x, str) for x in depends):
            raise ValueError('depends must be an array of task IDs')
        cwd = (workspace / task.get('worktree', '.')).resolve()
        if not cwd.is_relative_to(workspace) or not cwd.is_dir():
            raise ValueError('worktree must exist inside workspace')
        timeout, attempts = task.get('timeout', 300), task.get('attempts', 1)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError('timeout must be in (0, 3600] seconds')
        if type(attempts) is not int or not 1 <= attempts <= 5:
            raise ValueError('attempts must be in [1, 5]')
        task.update(depends=depends, worktree=str(cwd), timeout=timeout, attempts=attempts)
        tasks[ident] = task
    order, visiting, visited = [], set(), set()

    def visit(ident):
        if ident not in tasks:
            raise ValueError('unknown dependency: ' + ident)
        if ident in visiting:
            raise ValueError('cyclic dependency')
        if ident in visited:
            return
        visiting.add(ident)
        for dep in tasks[ident]['depends']:
            visit(dep)
        visiting.remove(ident)
        visited.add(ident)
        order.append(ident)
    for ident in tasks:
        visit(ident)
    return tasks, order


def invoke(task):
    # Spool command output to disk, rather than retaining unbounded output in RAM.
    import tempfile
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(task['command'], cwd=task['worktree'], stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=task['timeout'])
            status = 'succeeded' if code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code, status = -1, 'timeout'
        except BaseException:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 8192))
        return status, code, output.read().decode('utf-8', errors='replace')


def source_identity(worktree):
    """Bind cached results to HEAD plus tracked and non-ignored untracked source."""
    result = subprocess.run(['git', '-C', worktree, 'rev-parse', '--show-toplevel'], capture_output=True)
    if result.returncode:
        return None
    root = result.stdout.decode().strip()
    digest = hashlib.sha256()
    for args in (['rev-parse', 'HEAD'], ['diff', '--binary', 'HEAD', '--']):
        digest.update(subprocess.check_output(['git', '-C', root, *args]))
    paths = subprocess.check_output(['git', '-C', root, 'ls-files', '--others', '--exclude-standard', '-z'])
    for raw in sorted(paths.split(b'\0')):
        if not raw:
            continue
        path = Path(root) / os.fsdecode(raw)
        digest.update(raw)
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open('rb') as handle:
                while block := handle.read(65536):
                    digest.update(block)
    return digest.hexdigest()


def execute(plan, workspace, state):
    tasks, order = validate(plan, workspace)
    state.parent.mkdir(parents=True, exist_ok=True)
    with open(str(state) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sqlite3.connect(state) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, digest TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL)')
            # Include transitive dependency definitions in the fingerprint.
            digests = {}
            sources = {t['worktree']: source_identity(t['worktree']) for t in tasks.values()}
            for ident in order:
                task = tasks[ident]
                digest = hashlib.sha256(json.dumps({'task': task, 'source': sources[task['worktree']], 'dependencies': [digests[d] for d in task['depends']]}, sort_keys=True).encode()).hexdigest()
                digests[ident] = digest
                row = db.execute('SELECT digest,status,attempts FROM tasks WHERE id=?', (ident,)).fetchone()
                if row and sources[task['worktree']] is None:
                    raise ValueError('existing task state requires a Git source identity: ' + ident)
                if row and row[0] != digest:
                    raise ValueError('task ID reused with changed definition: ' + ident)
            statuses = {}
            for ident in order:
                task = tasks[ident]
                row = db.execute('SELECT digest,status,attempts FROM tasks WHERE id=?', (ident,)).fetchone()
                if row and row[1] == 'running':
                    raise ValueError('interrupted task requires reconciliation: ' + ident)
                if row and row[1] == 'succeeded':
                    statuses[ident] = 'succeeded'
                    emit('task_skipped', task=ident, reason='already succeeded')
                    continue
                if any(statuses[d] != 'succeeded' for d in task['depends']):
                    statuses[ident] = 'blocked'
                    emit('task_blocked', task=ident)
                    continue
                attempts = row[2] if row else 0
                status = row[1] if row else 'pending'
                while attempts < task['attempts']:
                    attempts += 1
                    db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?)', (ident, digests[ident], 'running', attempts))
                    db.commit()
                    emit('task_started', task=ident, attempt=attempts)
                    try:
                        status, code, output = invoke(task)
                    except OSError as exc:
                        status, code, output = 'failed', -1, str(exc)
                    db.execute('UPDATE tasks SET status=? WHERE id=?', (status, ident))
                    db.commit()
                    emit('task_result', task=ident, status=status, exit_code=code, output=output)
                    if status == 'succeeded':
                        break
                    if attempts < task['attempts']:
                        time.sleep(min(attempts, 3))
                statuses[ident] = status
            return 0 if all(x == 'succeeded' for x in statuses.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=Path('agents/plan.json'))
    parser.add_argument('--workspace', type=Path, default=Path('.'))
    parser.add_argument('--state', type=Path, default=Path('.runtime/tasks.sqlite'))
    args = parser.parse_args()
    try:
        return execute(json.loads(args.plan.read_text()), args.workspace.resolve(), args.state.resolve())
    except (ValueError, OSError, sqlite3.Error, TypeError) as exc:
        emit('plan_rejected', error=str(exc))
        return 2


if __name__ == '__main__':
    sys.exit(main())
