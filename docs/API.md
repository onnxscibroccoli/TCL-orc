# TCL API Reference

The console scripts `tcl` and `tcl-cli` are the same Typer app.
All command output is JSON. Errors exit with code 1.

## CLI

### `tcl-cli task`

Task commands talk to a running daemon over its Unix socket (`--socket`, env `TCL_SOCKET`, default `.tcl/daemon.sock`).
If the daemon can't be reached they fail with exit 1 and do not fall back to anything else.

| Command | Description |
|---|---|
| `task create ID [--depends ID]... [--timeout S] [--branch B] [--base REF] [--memory-mb N] [--cpu-seconds N] -- CMD ARGS...` | Submit one task. Prints `{"ok": true, "submitted": [ID]}`. |
| `task list` | Map of `task_id → state`. |
| `task status ID` | Full record: state, history, error, spec, start and finish times. |

### `tcl-cli daemon`

| Command | Description |
|---|---|
| `daemon start [--socket P] [--tasks-db P] [--repo DIR] [--workers N] [--telemetry FILE]` | Runs in the foreground. With `--repo`, each task runs in a sibling worktree `../worktrees/worktree-task_<id>`. Stops cleanly on SIGTERM. |
| `daemon health` | Health response (see below). |

### `tcl-cli worktree`

| Command | Description |
|---|---|
| `worktree create BRANCH [--repo DIR] [--base REF] [--claim FILE]... [--layout nested\|sibling]` | Allocate a worktree, port and task record. |
| `worktree list [--repo DIR]` | Registry records. |
| `worktree remove BRANCH [--force]` | Remove a worktree. Refused for active tasks unless `--force` is given. |
| `worktree health [--repo DIR]` | Per-worktree `exists`, `registered` and `healthy`. |

`bin/worktree-harness {create|remove|list|prune|health}` forwards to these commands.

### `tcl-cli ledger`

`init`, `deposit ACCOUNT AMOUNT`, `balance ACCOUNT`, `apply-decay` (`--db`, `--mode linear|fixed`, `--now`).

## Task spec (JSON)

```json
{
  "task_id": "build-1",
  "command": ["sh", "-c", "make test"],
  "depends": ["fetch-1"],
  "timeout": 300.0,
  "branch": null,
  "base": "HEAD",
  "limits": {"cpu_seconds": 60, "memory_bytes": 1073741824, "max_files": 256,
             "max_file_bytes": 268435456, "max_procs": null},
  "env": {}
}
```

Validation rules:

- `task_id` may use only `[A-Za-z0-9._-]`.
- `command` must be a non-empty list of strings. It is never run through a shell implicitly.
- `timeout` must be greater than 0 and at most 86400 seconds.
- Unknown fields, unknown dependencies, duplicate ids, self-dependencies and cycles are rejected.
- The default branch is `task/<task_id>`.

Child processes get only these environment variables: `PATH`, `HOME` (set to the worktree), `LANG`, `TCL_TASK_ID`, plus anything in `env`.

## Daemon IPC (Unix socket, JSON lines)

The daemon reads one JSON object per line and answers with one JSON line containing `"ok"`.
The socket is created with mode `0600`. Lines larger than 1 MiB are rejected.

| Request | Response |
|---|---|
| `{"op":"health"}` | `{"ok":true,"status":"healthy\|degraded","pid":…,"uptime":…,"tasks":{id:state},"worktrees":[…],"alerts":[…]}` |
| `{"op":"submit","tasks":[spec,…]}` or `{"op":"submit","task":spec}` | `{"ok":true,"submitted":[ids]}` |
| `{"op":"status"}` | `{"ok":true,"tasks":{id:state}}` |
| `{"op":"status","task_id":"x"}` | `{"ok":true,"task":{…}}` |
| invalid request | `{"ok":false,"error":"…"}` |

## Conflict alert

```json
{"alert_id": "uuid", "ts": 1761170000.0, "worktree": "pkg-1",
 "others": ["pkg-2"], "files": ["package.json"]}
```

The flagged task goes to `FAILED` with `error = "conflict: package.json"` and never reaches `MERGED`.

## Telemetry (JSON lines, `--telemetry FILE`)

Each line has an `event` field:

- `daemon.start`
- `daemon.stop`
- `daemon.health` (worktree health, written periodically)
- `task.submitted`
- `task.state` (`task_id`, `state`, `error`)
- `task.exited` (`returncode`, plus the last 2000 characters of stdout and stderr)
- `conflict.alert`

## Smart contract ABI

Planned for Phase 3; not yet stable.
