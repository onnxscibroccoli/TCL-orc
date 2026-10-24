"""`python -m tcl.localnet --data-dir D [--port P] [--validators N] [--dev]` (foreground node)."""
from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path

from .genesis import Genesis
from .node import Localnet
from .rpc import Server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tcl-localnet")
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--port", type=int, default=8645)
    ap.add_argument("--validators", type=int, default=4)
    ap.add_argument("--seed", default="tcl-dev")
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--consensus", choices=("bft", "fast"), default="bft")
    a = ap.parse_args(argv)
    gen = None if (a.data_dir / "genesis.json").exists() else Genesis(validators=a.validators,
                                                                      seed=a.seed)
    net = Localnet(a.data_dir, gen, dev=a.dev, consensus=a.consensus)
    srv = Server(net, port=a.port, sock=a.data_dir / "localnet.sock").start()
    (a.data_dir / "node.json").write_text(json.dumps(
        {"pid": os.getpid(), "url": f"http://127.0.0.1:{srv.port}",
         "sock": str(a.data_dir / "localnet.sock")}))
    signal.signal(signal.SIGTERM, lambda *_: srv.shutdown())
    try:
        srv.wait()
    except KeyboardInterrupt:
        srv.shutdown()
    finally:
        (a.data_dir / "node.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
