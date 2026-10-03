"""Logging setup producing '[INFO] message' lines."""
import logging
import sys


def setup_logging(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root = logging.getLogger("jobmonitor")
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    root.propagate = False
    return root


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger("jobmonitor" + (f".{name}" if name else ""))
