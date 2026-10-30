# -*- coding: utf-8 -*-
"""Centralized logging configuration.

Call setup_logging() once from app.create_app() to initialize.
Each module should use: logger = logging.getLogger(__name__)
"""
from __future__ import annotations

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
