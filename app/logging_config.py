"""Console and rotating-file logging configuration."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> logging.Logger:
    log_dir = Path(os.getenv("X2_LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("X2_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger("x2")
    root.setLevel(level)
    root.propagate = False
    if not root.handlers:
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        rotating = RotatingFileHandler(log_dir / "x2.log", maxBytes=5_000_000, backupCount=5)
        rotating.setLevel(level)
        rotating.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(console)
        root.addHandler(rotating)
    return root
