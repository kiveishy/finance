"""snp-monitor configuration (JSON). Relative paths are resolved against the config file's directory."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

CONFIG_ENV_VAR = "SNP_MONITOR_CONFIG"
DEFAULT_CHROME_DIR = "../../common/chrome"
CHROME_CHOICES = ("local", "installed")
CONSTITUENT_SOURCES = ("wikipedia", "github-datasets")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigurationError(Exception):
    """Invalid or missing configuration."""


def app_home() -> Path:
    """Directory of the executable (frozen) or of the project sources."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV_VAR)
    return Path(env).expanduser() if env else app_home() / "config.json"


def default_chrome_profile_dir() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "snp-monitor" / "chrome-profile"
    return Path.home() / ".local" / "share" / "snp-monitor" / "chrome-profile"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8720
    open_browser: bool = True

    @property
    def is_loopback(self) -> bool:
        if self.host == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False


@dataclass(frozen=True)
class BrowserConfig:
    """Which Chrome opens the UI when server.open_browser is true."""

    chrome: str = "local"  # "local" (chrome_dir, private profile) or "installed" (Chrome installed on the PC)
    chrome_dir: Path | None = None
    app_window: bool = True  # --app window without tabs/address bar
    exit_on_close: bool = True  # stop the app when the local Chrome window it opened is closed
    profile_dir: Path = field(default_factory=default_chrome_profile_dir)


@dataclass(frozen=True)
class DataConfig:
    constituent_sources: tuple[str, ...] = CONSTITUENT_SOURCES
    constituents_refresh_hours: float = 24.0
    quote_refresh_seconds: float = 60.0
    cache_dir: Path = Path("data")
    request_timeout_seconds: float = 20.0


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: Path | None = None
    max_bytes: int = 2_000_000
    backup_count: int = 3


@dataclass(frozen=True)
class AppConfig:
    path: Path | None
    server: ServerConfig = ServerConfig()
    browser: BrowserConfig = BrowserConfig()
    data: DataConfig = DataConfig()
    web_root: Path | None = None
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {config_path}: {exc}") from exc
        return cls.from_dict(raw, base_dir=config_path.parent, path=config_path)

    @classmethod
    def from_dict(cls, raw: Any, *, base_dir: Path, path: Path | None = None) -> "AppConfig":
        if not isinstance(raw, Mapping):
            raise ConfigurationError("Configuration root must be a JSON object")
        base = base_dir.resolve()

        def resolve(value: Any) -> Path:
            candidate = Path(str(value)).expanduser()
            return candidate if candidate.is_absolute() else (base / candidate).resolve()

        web = _section(raw, "web")
        return cls(
            path=path,
            server=_parse_server(_section(raw, "server")),
            browser=_parse_browser(_section(raw, "browser"), resolve),
            data=_parse_data(_section(raw, "data"), resolve),
            web_root=resolve(web["root"]) if web.get("root") else None,
            logging=_parse_logging(_section(raw, "logging"), resolve),
        )


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"'{key}' must be a JSON object")
    return value


def _number(section: Mapping[str, Any], key: str, default: float, name: str, minimum: float = 0) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ConfigurationError(f"{name}.{key} must be a number >= {minimum}, got {value!r}")
    return float(value)


def _parse_server(section: Mapping[str, Any]) -> ServerConfig:
    port = section.get("port", 8720)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigurationError(f"server.port must be between 0 and 65535, got {port!r}")
    return ServerConfig(
        host=str(section.get("host", "127.0.0.1")),
        port=port,
        open_browser=bool(section.get("open_browser", True)),
    )


def _parse_browser(section: Mapping[str, Any], resolve: Callable[[Any], Path]) -> BrowserConfig:
    chrome = str(section.get("chrome", "local"))
    if chrome not in CHROME_CHOICES:
        raise ConfigurationError(f"browser.chrome must be 'local' or 'installed', got {chrome!r}")
    profile_dir = section.get("profile_dir")
    return BrowserConfig(
        chrome=chrome,
        chrome_dir=resolve(section.get("chrome_dir") or DEFAULT_CHROME_DIR),
        app_window=bool(section.get("app_window", True)),
        exit_on_close=bool(section.get("exit_on_close", True)),
        profile_dir=resolve(profile_dir) if profile_dir else default_chrome_profile_dir(),
    )


def _parse_data(section: Mapping[str, Any], resolve: Callable[[Any], Path]) -> DataConfig:
    sources = section.get("constituent_sources", list(CONSTITUENT_SOURCES))
    if not isinstance(sources, list) or not sources or any(s not in CONSTITUENT_SOURCES for s in sources):
        raise ConfigurationError(f"data.constituent_sources must be a non-empty list of {list(CONSTITUENT_SOURCES)}")
    return DataConfig(
        constituent_sources=tuple(sources),
        constituents_refresh_hours=_number(section, "constituents_refresh_hours", 24, "data"),
        quote_refresh_seconds=_number(section, "quote_refresh_seconds", 60, "data", minimum=10),
        cache_dir=resolve(section.get("cache_dir", "data")),
        request_timeout_seconds=_number(section, "request_timeout_seconds", 20, "data", minimum=1),
    )


def _parse_logging(section: Mapping[str, Any], resolve: Callable[[Any], Path]) -> LoggingConfig:
    level = str(section.get("level", "INFO")).upper()
    if level not in LOG_LEVELS:
        raise ConfigurationError(f"logging.level must be one of {list(LOG_LEVELS)}, got {level!r}")
    file = section.get("file", "logs/snp-monitor.log")
    return LoggingConfig(
        level=level,
        file=resolve(file) if file else None,
        max_bytes=int(_number(section, "max_bytes", 2_000_000, "logging", minimum=10_000)),
        backup_count=int(_number(section, "backup_count", 3, "logging")),
    )
