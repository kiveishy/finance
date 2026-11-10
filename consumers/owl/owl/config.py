"""Owl configuration (JSON). Relative paths are resolved against the config file's directory."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from finance_aggregator.config import LoggingConfig
from finance_aggregator.core.errors import ConfigurationError

CONFIG_ENV_VAR = "OWL_CONFIG"
API_KEY_ENV_VAR = "OWL_API_KEY"
DEFAULT_CORS_ORIGINS = ("http://localhost", "https://localhost", "capacitor://localhost")


def app_home() -> Path:
    """Directory of the executable (frozen) or of the owl project sources."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV_VAR)
    return Path(env).expanduser() if env else app_home() / "config.json"


@dataclass(frozen=True)
class AggregatorLink:
    """How owl reaches its data provider (the finance aggregator)."""

    mode: str = "embedded"  # "embedded" (in-process provider chain) or "remote" (aggregator HTTP API)
    config_file: Path | None = None
    url: str = "http://127.0.0.1:5100"
    api_key: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8710
    api_key: str | None = None
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    open_browser: bool = True
    allow_insecure_remote: bool = False

    @property
    def is_loopback(self) -> bool:
        if self.host == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False


@dataclass(frozen=True)
class OwlConfig:
    path: Path | None
    base_dir: Path
    aggregator: AggregatorLink
    server: ServerConfig = ServerConfig()
    database: Path = Path("data/owl.db")
    web_root: Path | None = None
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def load(cls, path: str | Path) -> "OwlConfig":
        config_path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {config_path}: {exc}") from exc
        return cls.from_dict(raw, base_dir=config_path.parent, path=config_path)

    @classmethod
    def from_dict(cls, raw: Any, *, base_dir: Path, path: Path | None = None) -> "OwlConfig":
        if not isinstance(raw, Mapping):
            raise ConfigurationError("Configuration root must be a JSON object")
        base = base_dir.resolve()

        def resolve(value: Any) -> Path:
            candidate = Path(str(value)).expanduser()
            return candidate if candidate.is_absolute() else (base / candidate).resolve()

        storage = _section(raw, "storage")
        web = _section(raw, "web")
        return cls(
            path=path,
            base_dir=base,
            aggregator=_parse_aggregator(_section(raw, "aggregator"), resolve),
            server=_parse_server(_section(raw, "server")),
            database=resolve(storage.get("database", "data/owl.db")),
            web_root=resolve(web["root"]) if web.get("root") else None,
            logging=LoggingConfig.from_mapping(_section(raw, "logging"), base),
        )


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"'{key}' must be a JSON object")
    return value


def _parse_aggregator(section: Mapping[str, Any], resolve: Any) -> AggregatorLink:
    mode = str(section.get("mode", "embedded"))
    if mode not in ("embedded", "remote"):
        raise ConfigurationError(f"aggregator.mode must be 'embedded' or 'remote', got {mode!r}")
    config_file = section.get("config_file")
    if mode == "embedded" and not config_file:
        raise ConfigurationError("aggregator.config_file is required in embedded mode")
    url = str(section.get("url", "http://127.0.0.1:5100")).rstrip("/")
    if mode == "remote" and not url.startswith(("http://", "https://")):
        raise ConfigurationError(f"aggregator.url must be an http(s) URL, got {url!r}")
    try:
        timeout = float(section.get("timeout_seconds", 30.0))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("aggregator.timeout_seconds must be a number") from exc
    return AggregatorLink(
        mode=mode,
        config_file=resolve(config_file) if config_file else None,
        url=url,
        api_key=section.get("api_key") or None,
        timeout_seconds=timeout,
    )


def _parse_server(section: Mapping[str, Any]) -> ServerConfig:
    port = section.get("port", 8710)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigurationError(f"server.port must be between 0 and 65535, got {port!r}")
    origins = section.get("cors_origins", list(DEFAULT_CORS_ORIGINS))
    if not isinstance(origins, list) or not all(isinstance(o, str) for o in origins):
        raise ConfigurationError("server.cors_origins must be a list of strings")
    if "*" in origins:
        raise ConfigurationError("server.cors_origins must not contain '*'; list the allowed origins")
    return ServerConfig(
        host=str(section.get("host", "127.0.0.1")),
        port=port,
        api_key=section.get("api_key") or os.environ.get(API_KEY_ENV_VAR) or None,
        cors_origins=tuple(o.rstrip("/") for o in origins),
        open_browser=bool(section.get("open_browser", True)),
        allow_insecure_remote=bool(section.get("allow_insecure_remote", False)),
    )
