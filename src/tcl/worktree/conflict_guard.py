"""Re-export (spec path); implementation lives in tcl.orchestrator.conflict_guard."""
from ..orchestrator.conflict_guard import (ConflictAlert, ConflictGuard, ConflictGuardError,
                                           modified_files)

__all__ = ["ConflictAlert", "ConflictGuard", "ConflictGuardError", "modified_files"]
