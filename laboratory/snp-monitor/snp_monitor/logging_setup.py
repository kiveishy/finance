"""Logging: rotating log file plus a console handler."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import LoggingConfig

_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"


def configure_logging(config: LoggingConfig, *, console_level: str | None = None) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, (console_level or config.level).upper()))
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)
    effective = console.level
    if config.file is not None:
        try:
            config.file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                config.file, maxBytes=config.max_bytes, backupCount=config.backup_count, encoding="utf-8"
            )
            file_handler.setLevel(getattr(logging, config.level))
            file_handler.setFormatter(logging.Formatter(_FORMAT))
            root.addHandler(file_handler)
            effective = min(effective, file_handler.level)
        except OSError as exc:
            logging.getLogger(__name__).warning("Cannot open log file %s: %s", config.file, exc)
    root.setLevel(effective)
    for noisy in ("urllib3", "yfinance", "peewee", "curl_cffi"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, effective))
