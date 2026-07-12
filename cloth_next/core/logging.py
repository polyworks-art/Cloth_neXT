# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Explicitly configured logging facade with conservative context handling."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping

LOGGER_NAME = "cloth_next"
_REDACTED_KEYS = frozenset({"token", "secret", "password", "authorization", "payload"})


def get_logger(component: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not component else f"{LOGGER_NAME}.{component}")


def initialize_logging(
    *,
    level: int = logging.INFO,
    log_file: Path | None = None,
    max_bytes: int = 2_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    logger = get_logger()
    logger.setLevel(level)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    handler: logging.Handler
    if log_file is None:
        handler = logging.NullHandler()
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    return logger


def safe_context(context: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in context.items():
        normalized = str(key).lower()
        result[str(key)] = "<redacted>" if normalized in _REDACTED_KEYS else repr(value)[:512]
    return result


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    context: Mapping[str, Any] | None = None,
) -> None:
    suffix = "" if not context else f" context={safe_context(context)}"
    logger.log(level, "%s%s", message, suffix)

