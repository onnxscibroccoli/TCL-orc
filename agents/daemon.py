#!/usr/bin/env python3
"""Poll a trusted plan; durable orchestration state prevents completed-task replay."""
import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from orchestrator import execute, emit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=Path('agents/plan.json'))
    parser.add_argument('--workspace', type=Path, default=Path('.'))
    parser.add_argument('--state', type=Path, default=Path('.runtime/tasks.sqlite'))
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not 0 < args.interval <= 3600:
        parser.error('interval must be in (0, 3600]')
    stop = threading.Event()
    def shutdown(signum, frame):
        stop.set()
        # Interrupt any active child wait, so invoke() kills its process group.
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while not stop.is_set():
            try:
                result = execute(json.loads(args.plan.read_text()), args.workspace.resolve(), args.state.resolve())
            except (ValueError, OSError, TypeError) as exc:
                emit('daemon_error', error=str(exc))
                result = 2
            if args.once:
                return result
            stop.wait(args.interval)
    except KeyboardInterrupt:
        emit('daemon_stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
