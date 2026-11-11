"""Typed application configuration loaded from JSON.

Relative paths inside the file are resolved against the directory of the config file.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .core.errors import ConfigurationError

CONFIG_ENV_VAR = "FINANCE_AGGREGATOR_CONFIG"
CONFIG_FILE_NAME = "config.json"


def app_home() -> Path:
    """Directory holding the executable (frozen build) or the project sources."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path | None:
    """PyInstaller extraction dir for bundled resources, None when running from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV_VAR)
    return Path(env).expanduser() if env else app_home() / CONFIG_FILE_NAME


@dataclass(frozen=True)
class ProviderSpec:
    type: str
    name: str
    enabled: bool = True
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: Path | None = None
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any], base_dir: Path) -> "LoggingConfig":
        return _parse_logging(section, base_dir)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 5100
    api_key: str | None = None
    auto_start: bool = True  # also serve the HTTP API while the interactive CLI runs


@dataclass(frozen=True)
class AggregatorConfig:
    history_max_staleness_days: int | None = 0


@dataclass(frozen=True)
class AppConfig:
    path: Path | None
    base_dir: Path
    providers: tuple[ProviderSpec, ...]
    aggregator: AggregatorConfig = AggregatorConfig()
    server: ServerConfig = ServerConfig()
    logging: LoggingConfig = LoggingConfig()
    plugins: tuple[str, ...] = ()

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {config_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigurationError(f"Cannot read configuration {config_path}: {exc}") from exc
        return cls.from_dict(raw, base_dir=config_path.parent, path=config_path)

    @classmethod
    def from_dict(cls, raw: Any, *, base_dir: Path, path: Path | None = None) -> "AppConfig":
        if not isinstance(raw, Mapping):
            raise ConfigurationError("Configuration root must be a JSON object")
        base = base_dir.resolve()
        return cls(
            path=path,
            base_dir=base,
            providers=_parse_providers(raw.get("providers", [])),
            aggregator=_parse_aggregator(_section(raw, "aggregator")),
            server=_parse_server(_section(raw, "server")),
            logging=_parse_logging(_section(raw, "logging"), base),
            plugins=tuple(_list_of_str(raw.get("plugins", []), "plugins")),
        )


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"'{key}' must be a JSON object")
    return value


def _list_of_str(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigurationError(f"'{key}' must be a list of strings")
    return value


def _int(section: Mapping[str, Any], key: str, default: int | None, *, minimum: int = 0) -> int | None:
    value = section.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(f"'{key}' must be an integer >= {minimum}, got {value!r}")
    return value


def _parse_providers(raw: Any) -> tuple[ProviderSpec, ...]:
    if not isinstance(raw, list):
        raise ConfigurationError("'providers' must be a list")
    specs: list[ProviderSpec] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or not isinstance(item.get("type"), str) or not item["type"]:
            raise ConfigurationError(f"providers[{index}] must be an object with a non-empty 'type'")
        name = str(item.get("name") or item["type"])
        if name in names:
            raise ConfigurationError(f"Duplicate provider name {name!r}")
        names.add(name)
        options = item.get("options") or {}
        if not isinstance(options, Mapping):
            raise ConfigurationError(f"providers[{index}].options must be an object")
        specs.append(ProviderSpec(item["type"], name, bool(item.get("enabled", True)), dict(options)))
    return tuple(specs)


def _parse_aggregator(section: Mapping[str, Any]) -> AggregatorConfig:
    return AggregatorConfig(
        history_max_staleness_days=_int(section, "history_max_staleness_days", 0),
    )


def _parse_server(section: Mapping[str, Any]) -> ServerConfig:
    port = _int(section, "port", 5100, minimum=0)
    if port is None or port > 65535:
        raise ConfigurationError(f"'server.port' must be between 0 and 65535, got {port!r}")
    api_key = section.get("api_key") or os.environ.get("FINANCE_AGGREGATOR_API_KEY") or None
    auto_start = section.get("auto_start", True)
    if not isinstance(auto_start, bool):
        raise ConfigurationError(f"'server.auto_start' must be true or false, got {auto_start!r}")
    return ServerConfig(host=str(section.get("host", "127.0.0.1")), port=port, api_key=api_key, auto_start=auto_start)


def _parse_logging(section: Mapping[str, Any], base_dir: Path) -> LoggingConfig:
    level = str(section.get("level", "INFO")).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError(f"Invalid logging level {level!r}")
    file_value = section.get("file")
    log_file = None
    if file_value:
        candidate = Path(str(file_value)).expanduser()
        log_file = candidate if candidate.is_absolute() else (base_dir / candidate).resolve()
    return LoggingConfig(
        level=level,
        file=log_file,
        max_bytes=_int(section, "max_bytes", 5 * 1024 * 1024, minimum=1024) or 5 * 1024 * 1024,
        backup_count=_int(section, "backup_count", 5) or 0,
    )
