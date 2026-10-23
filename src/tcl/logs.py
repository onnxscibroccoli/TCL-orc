"""Compatibility shim: logging moved to :mod:`tcl.utils.logger`."""
from .utils.logger import JsonFormatter, configure, get_logger  # noqa: F401
