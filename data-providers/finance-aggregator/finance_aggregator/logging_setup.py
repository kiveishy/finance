"""Logging setup: rotating log file plus a console handler with a configurable level."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import LoggingConfig

_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"
_MARKER = "_finance_aggregator_handler"


def configure_logging(config: LoggingConfig, *, console_level: str | None = None) -> None:
    """Configure the root logger. `console_level` overrides the console verbosity (CLI uses WARNING)."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _MARKER, False)]:
        root.removeHandler(handler)
        handler.close()

    file_level = getattr(logging, config.level)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, (console_level or config.level).upper()))
    console.setFormatter(logging.Formatter(_FORMAT))
    setattr(console, _MARKER, True)
    root.addHandler(console)

    effective = min(file_level, console.level)
    if config.file is not None:
        try:
            config.file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                config.file, maxBytes=config.max_bytes, backupCount=config.backup_count, encoding="utf-8"
            )
            file_handler.setLevel(file_level)
            file_handler.setFormatter(logging.Formatter(_FORMAT))
            setattr(file_handler, _MARKER, True)
            root.addHandler(file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning("Cannot open log file %s: %s", config.file, exc)
    else:
        effective = console.level
    root.setLevel(effective)

    # Chatty third-party loggers; our providers log the classified outcome themselves.
    for noisy in ("urllib3", "yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, effective))
