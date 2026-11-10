"""Composition root and command line entry point of owl."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from finance_aggregator.core.errors import ConfigurationError
from finance_aggregator.logging_setup import configure_logging

from . import __version__
from .charts import ChartService
from .config import OwlConfig, app_home, bundle_dir, default_config_path
from .market_data import MarketData, create_market_data
from .server import OwlApi, OwlServer, StaticFiles
from .watchlist import SqliteWatchlistRepository, WatchlistRepository

logger = logging.getLogger(__name__)


def default_web_root() -> Path:
    bundled = bundle_dir()
    return bundled / "web" if bundled else app_home() / "web"


class OwlApplication:
    """Wires market data, storage, chart service and HTTP server from one config."""

    def __init__(
        self,
        config: OwlConfig,
        *,
        market: MarketData | None = None,
        watchlist: WatchlistRepository | None = None,
    ) -> None:
        self.config = config
        self.market = market or create_market_data(config.aggregator)
        self.watchlist = watchlist or SqliteWatchlistRepository(config.database)
        self.api = OwlApi(self.market, self.watchlist, ChartService(self.market))
        self.static = StaticFiles(config.web_root or default_web_root())

    def create_server(self) -> OwlServer:
        server = self.config.server
        if not server.is_loopback and not server.api_key and not server.allow_insecure_remote:
            raise ConfigurationError(
                f"Refusing to listen on {server.host} without server.api_key "
                "(set an API key, or server.allow_insecure_remote=true on a trusted network)"
            )
        return OwlServer(server, self.api, self.static)

    def backfill_names(self) -> None:
        """Look up display names for watchlist symbols added without one (e.g. the seeded defaults)."""
        for symbol in self.watchlist.symbols_without_name():
            try:
                self.watchlist.set_symbol_name(symbol, self.market.get_ticker_info(symbol).display_name)
            except Exception as exc:  # noqa: BLE001 - best effort, retried on next start
                logger.info("Could not look up name of %s: %s", symbol, exc)

    def close(self) -> None:
        self.watchlist.close()
        self.market.close()

    def __enter__(self) -> "OwlApplication":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="owl", description="Owl stock viewer (web UI on top of the finance aggregator)")
    parser.add_argument("--config", help="path to config.json (env OWL_CONFIG)")
    parser.add_argument("--host", help="bind address (default from config)")
    parser.add_argument("--port", type=int, help="port (default from config)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the UI in a browser")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], type=str.upper)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        config = OwlConfig.load(args.config or default_config_path())
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    server_config = config.server
    if args.host:
        server_config = replace(server_config, host=args.host)
    if args.port is not None:
        server_config = replace(server_config, port=args.port)
    config = replace(config, server=server_config)
    configure_logging(config.logging, console_level=args.log_level or "INFO")
    logger.info("owl %s starting with config %s", __version__, config.path)

    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        with OwlApplication(config) as app, app.create_server() as server:
            print(f"Owl is running at {server.url}  (Ctrl+C to stop)", flush=True)
            threading.Thread(target=app.backfill_names, name="name-backfill", daemon=True).start()
            if config.server.open_browser and not args.no_browser:
                webbrowser.open(server.url)
            while not stop.wait(0.5):
                pass
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Cannot start owl: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    print("Owl stopped")
    return 0
