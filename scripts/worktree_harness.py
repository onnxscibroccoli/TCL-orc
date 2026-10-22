#!/usr/bin/env python3
"""Local worktree lifecycle without shell interpolation or forced data deletion."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'remove', 'list', 'prune'])
    parser.add_argument('branch', nargs='?')
    args = parser.parse_args()
    if args.action == 'list':
        print(git('worktree', 'list'))
        return
    if args.action == 'prune':
        print(git('worktree', 'prune'))
        return
    branch = args.branch
    # Restrict branch names before they can become paths, refs, or command options.
    if not branch or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,119}', branch):
        parser.error('branch requires a simple alphanumeric Git ref')
    subprocess.run(['git', 'check-ref-format', '--branch', branch], check=True, stdout=subprocess.DEVNULL)
    root = Path(git('rev-parse', '--show-toplevel'))
    os.chdir(root)
    base = root / '.worktrees'
    if base.is_symlink():
        raise ValueError('worktree base cannot be a symlink')
    base.mkdir(exist_ok=True)
    registry = base / 'ports.json'
    lockpath = base / 'ports.lock'
    if registry.is_symlink() or lockpath.is_symlink():
        raise ValueError('registry and lock cannot be symlinks')
    path = base / hashlib.sha256(branch.encode()).hexdigest()[:24]
    with lockpath.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ports = json.loads(registry.read_text()) if registry.exists() else {}
        if args.action == 'create':
            if path.exists() or branch in ports:
                raise ValueError('worktree already exists')
            port = next(p for p in range(8000, 65536) if p not in ports.values())
            subprocess.run(['git', 'worktree', 'add', '-b', branch, str(path), 'HEAD'], check=True)
            ports[branch] = port
            # No credentials are copied and no dependency installers run implicitly.
            print(json.dumps({'branch': branch, 'path': str(path), 'port': port}))
        else:
            if branch not in ports or not path.is_dir() or path.is_symlink():
                raise ValueError('unknown worktree')
            # Git refuses tracked modifications and untracked files without --force.
            subprocess.run(['git', 'worktree', 'remove', str(path)], check=True)
            del ports[branch]
        temporary = base / 'ports.tmp'
        if temporary.is_symlink():
            raise ValueError('temporary registry cannot be a symlink')
        temporary.write_text(json.dumps(ports, indent=2) + '\n')
        os.replace(temporary, registry)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
