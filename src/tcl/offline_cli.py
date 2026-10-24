# SPDX-License-Identifier: Apache-2.0
"""`tcl offline ...` commands (see docs/OFFLINE.md)."""
from __future__ import annotations

import json

import typer


offline_app = typer.Typer(help="Offline bundle (vendor/): verify and install with no network",
                          no_args_is_help=True)


@offline_app.command("verify")
def offline_verify(require_assets: bool = typer.Option(
        False, "--require-assets", help="also require fetched release assets")):
    """Fail closed on any sha256/size mismatch, missing blob or license."""
    from pathlib import Path as _P

    from . import offline as off
    problems, stats = off.verify(off.repo_root(_P.cwd() / "x"), require_assets=require_assets)
    for pr in problems:
        typer.echo(f"FAIL {pr}", err=True)
    typer.echo(json.dumps({"ok": not problems, **stats}))
    raise typer.Exit(1 if problems else 0)


@offline_app.command("install")
def offline_install(python: str = typer.Option(None, help="target interpreter (default: this one)"),
                    runtime_only: bool = typer.Option(False, help="skip test-only wheels"),
                    editable: bool = typer.Option(True, help="pip install -e the checkout")):
    """pip install --no-index from the verified bundle (never touches the network)."""
    from pathlib import Path as _P

    from . import offline as off
    raise typer.Exit(off.install(off.repo_root(_P.cwd() / "x"), python=python,
                                 editable=editable, test=not runtime_only))
