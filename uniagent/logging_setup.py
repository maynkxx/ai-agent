"""Tiny logging helper.

A single ``get_logger`` keeps log formatting consistent across modules and lets
the CLI raise/lower verbosity in one place.  We log liberally because the
agent's behaviour (which page it chose, why a value was flagged) is exactly the
"show your reasoning" the brief asks for.
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure(level: int = logging.INFO) -> None:
    """Set up root logging once; subsequent calls only adjust the level."""
    global _CONFIGURED
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-7s  %(name)-18s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root = logging.getLogger()
        root.addHandler(handler)
        _CONFIGURED = True
    logging.getLogger().setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring defaults on first use."""
    if not _CONFIGURED:
        configure()
    return logging.getLogger(name)
