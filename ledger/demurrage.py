"""Backwards-compatible shim: moved to tcl.ledger.demurrage."""
import sys as _sys
from pathlib import Path as _P

_src = str(_P(__file__).resolve().parent.parent / "src")
if _src not in _sys.path:
    _sys.path.insert(0, _src)
from tcl.ledger.demurrage import *  # noqa: F401,F403,E402
from tcl.ledger import demurrage as _m  # noqa: E402

globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
