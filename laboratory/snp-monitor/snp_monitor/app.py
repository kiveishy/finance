"""Composition root and command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from . import __version__
from .browser import BrowserLauncher
from .config import AppConfig, ConfigurationError, app_home, bundle_dir, default_config_path
from .constituents import ConstituentsProvider, create_sources
from .logging_setup import configure_logging
from .monitor import SnpMonitor
from .quotes import YahooQuoteSource
from .server import Api, Server, StaticFiles

logger = logging.getLogger(__name__)

APP_NAME = "snp-monitor"
NO_CONSOLE = sys.stderr is None  # windowed executable (no terminal): errors are shown in a message box


def default_web_root() -> Path:
    bundled = bundle_dir()
    return bundled / "web" if bundled else app_home() / "web"


def create_monitor(config: AppConfig) -> SnpMonitor:
    data = config.data
    provider = ConstituentsProvider(
        create_sources(data.constituent_sources, timeout=data.request_timeout_seconds),
        data.cache_dir / "constituents.json",
        max_age_seconds=data.constituents_refresh_hours * 3600,
    )
    return SnpMonitor(provider, YahooQuoteSource(timeout=data.request_timeout_seconds),
                      refresh_seconds=data.quote_refresh_seconds)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="S&P 500 monitor: prices and share pie charts")
    parser.add_argument("--config", help="path to config.json (env SNP_MONITOR_CONFIG)")
    parser.add_argument("--host", help="bind address (default from config)")
    parser.add_argument("--port", type=int, help="port (default from config)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the UI in a browser")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], type=str.upper)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if sys.stdout is None or sys.stderr is None:  # windowed executable: no console
        devnull = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives as long as the process
        sys.stdout = sys.stdout or devnull
        sys.stderr = sys.stderr or devnull
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        config = AppConfig.load(args.config or default_config_path())
    except ConfigurationError as exc:
        return _fail(f"Configuration error: {exc}", 2)
    server_config = config.server
    if args.host:
        server_config = replace(server_config, host=args.host)
    if args.port is not None:
        server_config = replace(server_config, port=args.port)
    config = replace(config, server=server_config)
    configure_logging(config.logging, console_level=args.log_level or "INFO")
    logger.info("%s %s starting with config %s", APP_NAME, __version__, config.path)
    if not config.server.is_loopback:
        logger.warning("Listening on %s: the UI is reachable from other machines (read-only, no login)", config.server.host)
    open_browser = config.server.open_browser and not args.no_browser

    running_url = _running_url(config.server.host, config.server.port)
    if running_url:
        logger.info("%s is already running at %s - showing it instead of starting again", APP_NAME, running_url)
        print(f"snp-monitor is already running at {running_url}", flush=True)
        if open_browser:
            # No job binding: a Chrome started here must outlive this short-lived process.
            BrowserLauncher(config.browser, bind_children=lambda: None).open(running_url)
        return 0

    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    browser = BrowserLauncher(config.browser, on_closed=stop.set if config.browser.exit_on_close else None)
    monitor = create_monitor(config)
    static = StaticFiles(config.web_root or default_web_root())
    try:
        with Server(config.server, Api(monitor, web_build=static.build_id), static) as server:
            monitor.start()
            print(f"snp-monitor is running at {server.url}  (Ctrl+C to stop)", flush=True)
            if open_browser:
                browser.open(server.url)
            while not stop.wait(0.5):
                pass
    except OSError as exc:
        return _fail(f"Cannot start snp-monitor: {exc}", 1)
    except KeyboardInterrupt:
        pass
    finally:
        browser.close()
        monitor.close()
    logger.info("%s stopped", APP_NAME)
    print("snp-monitor stopped")
    return 0


def _running_url(host: str, port: int) -> str | None:
    """URL of an snp-monitor already serving on host:port, if any."""
    if port == 0:
        return None
    shown = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{shown}:{port}"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a corporate proxy
        with opener.open(f"{url}/api/health", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    data = body.get("data") if isinstance(body, dict) else None
    return url if isinstance(data, dict) and data.get("app") == APP_NAME else None


def _fail(message: str, code: int) -> int:
    logger.error(message)
    print(message, file=sys.stderr)
    if NO_CONSOLE and sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "snp-monitor", 0x10)  # MB_ICONERROR
    return code
