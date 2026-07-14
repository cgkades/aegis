"""Structured logging setup for Aegis."""

from __future__ import annotations

import logging
import sys
from typing import Literal

LogLevel = Literal["debug", "info", "warning", "error"]

_CONFIGURED = False


def setup_logging(
    level: LogLevel | str = "info",
    *,
    stream: bool = True,
) -> logging.Logger:
    """Configure root logger for the aegis namespace once.

    Returns the ``aegis`` logger.
    """
    global _CONFIGURED
    log = logging.getLogger("aegis")
    numeric = _to_level(level)
    log.setLevel(numeric)

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        handler.setLevel(numeric)
        log.addHandler(handler)
        log.propagate = False
        _CONFIGURED = True
    else:
        for handler in log.handlers:
            handler.setLevel(numeric)

    return log


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under ``aegis``."""
    if name is None or name == "aegis":
        return logging.getLogger("aegis")
    if name.startswith("aegis."):
        return logging.getLogger(name)
    return logging.getLogger(f"aegis.{name}")


def _to_level(level: str) -> int:
    mapping = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    key = level.lower()
    if key not in mapping:
        raise ValueError(f"unknown log level: {level}")
    return mapping[key]


def reset_logging_for_tests() -> None:
    """Clear handlers — test helper only."""
    global _CONFIGURED
    log = logging.getLogger("aegis")
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    _CONFIGURED = False
