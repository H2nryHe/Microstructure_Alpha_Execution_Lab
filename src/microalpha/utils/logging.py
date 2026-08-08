"""Logging setup for CLI and batch runs."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure standard logging with UTC timestamps."""

    logging.Formatter.converter = time_gmtime
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)sZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )


def time_gmtime(*args: object) -> object:
    import time

    return time.gmtime(*args)
