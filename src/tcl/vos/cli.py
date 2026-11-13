"""Typer commands for launching the local-only vOS and playable Civitas-99 game."""
from pathlib import Path
import json
import typer

from .runtime import GameError, GameOS
from .server import create_server

vos_app = typer.Typer(help="Civitas-99 virtual gaming OS (local-only playable prototype)",
                      no_args_is_help=True)


@vos_app.command("serve")
def serve(port: int = typer.Option(8765, help="Loopback HTTP port"),
          save: Path = typer.Option(Path.home() / ".local/share/tcl-vos/civitas99.json",
                                   help="Private local save file")) -> None:
    try:
        server = create_server(port=port, path=save)
    except GameError as exc:
        typer.echo(f"vOS failed: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Civitas-99: http://127.0.0.1:{server.server_address[1]}/")
    typer.echo("Development demo only: no authentication, fiat, blockchain trading or network play.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


@vos_app.command("demo")
def demo() -> None:
    game = GameOS()
    for player in ("alice", "bob"):
        game.dispatch({"game": "civitas99", "actor": player, "action": "join", "params": {}})
    game.dispatch({"game": "civitas99", "actor": "alice", "action": "order",
                   "params": {"side": "sell", "resource": "energy", "price": 12, "quantity": 3}})
    game.dispatch({"game": "civitas99", "actor": "bob", "action": "order",
                   "params": {"side": "buy", "resource": "energy", "price": 15, "quantity": 2}})
    typer.echo(json.dumps(game.snapshot("bob"), indent=2))

