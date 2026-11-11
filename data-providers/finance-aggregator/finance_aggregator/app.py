"""Composition root: wires configuration, providers, services and the CLI together."""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from typing import Iterable, Sequence, TextIO

from . import __version__
from .cli import CommandContext, CommandRegistry, ExitCode, InteractiveShell, builtin_commands, run_once
from .cli.base import Command
from .config import AppConfig, bundle_dir, default_config_path
from .core.errors import ConfigurationError, FinanceError
from .core.interfaces import DataProvider
from .logging_setup import configure_logging
from .providers import MisconfiguredProvider, ProviderContext, ProviderRegistry, default_registry
from .services import AggregatorService, FallbackPolicy

logger = logging.getLogger(__name__)


def load_plugins(modules: Iterable[str]) -> None:
    """Import plugin modules; they register their providers via `@register_provider`."""
    for module in modules:
        try:
            importlib.import_module(module)
            logger.info("Loaded plugin %s", module)
        except Exception:  # noqa: BLE001 - a broken plugin must not stop the service
            logger.exception("Failed to load plugin module %s", module)


def build_providers(
    config: AppConfig, registry: ProviderRegistry, context: ProviderContext
) -> list[DataProvider]:
    """Instantiate enabled providers in priority order; broken ones become MisconfiguredProvider."""
    providers: list[DataProvider] = []
    for spec in config.providers:
        if not spec.enabled:
            logger.info("Provider %s (%s) disabled in configuration", spec.name, spec.type)
            continue
        try:
            providers.append(registry.create(spec.type, spec.name, spec.options, context))
        except FinanceError as exc:
            logger.error("Provider %s (%s) unusable: %s", spec.name, spec.type, exc)
            providers.append(MisconfiguredProvider(spec.name, spec.type, str(exc)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Provider %s (%s) failed to initialise", spec.name, spec.type)
            providers.append(MisconfiguredProvider(spec.name, spec.type, f"{type(exc).__name__}: {exc}"))
    return providers


def create_aggregator(
    config: AppConfig, *, registry: ProviderRegistry = default_registry
) -> AggregatorService:
    """Build the configured provider chain; the entry point for embedding the aggregator in consumers."""
    load_plugins(config.plugins)
    provider_context = ProviderContext(base_dir=config.base_dir, bundle_dir=bundle_dir())
    return AggregatorService(
        build_providers(config, registry, provider_context),
        policy=FallbackPolicy(config.aggregator.history_max_staleness_days),
    )


class Application:
    def __init__(
        self,
        config: AppConfig,
        *,
        registry: ProviderRegistry = default_registry,
        commands: Iterable[Command] | None = None,
        out: TextIO | None = None,
    ) -> None:
        self.config = config
        self.aggregator = create_aggregator(config, registry=registry)
        self.providers = list(self.aggregator.providers)
        self.commands = CommandRegistry(commands if commands is not None else builtin_commands())
        self.context = CommandContext(
            source=self.aggregator,
            service=self.aggregator,
            config=config,
            registry=self.commands,
            out=out or sys.stdout,
        )
        logger.info(
            "Provider chain: %s", " -> ".join(p.name for p in self.providers) or "<empty>"
        )

    def close(self) -> None:
        self.aggregator.close()

    def __enter__(self) -> "Application":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="finance-aggregator",
        description="Unified stock data access (local yahoo-db -> yahoo-db API -> Yahoo Finance). "
        "Without a command an interactive shell is started.",
    )
    parser.add_argument("--config", help="path to config.json (env FINANCE_AGGREGATOR_CONFIG)")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        type=str.upper,
        help="console log level (default WARNING for the CLI, INFO for 'serve')",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="optional one-shot command, e.g. read AAPL --days 5")
    return parser.parse_args(argv)


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    config_path = args.config or default_config_path()
    try:
        config = AppConfig.load(config_path)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return ExitCode.USAGE

    serving = bool(args.command) and args.command[0] == "serve"
    configure_logging(config.logging, console_level=args.log_level or ("INFO" if serving else "WARNING"))
    logger.info("finance-aggregator %s starting with config %s", __version__, config.path)

    with Application(config) as app:
        if not args.command:
            return _interactive(app)
        return run_once(app.context, args.command)


def _interactive(app: Application) -> int:
    """The interactive shell, with the HTTP API served in the background (server.auto_start)."""
    from .server import BackgroundApi

    server = app.config.server
    api = BackgroundApi(app.aggregator, app.aggregator, host=server.host, port=server.port, api_key=server.api_key)
    app.context.api = api
    if server.auto_start:
        try:
            api_line = f"HTTP API: {api.start()} (Owl and other consumers connect here; 'server stop' stops it)"
        except OSError as exc:
            logger.warning("HTTP API not started on %s: %s", api.url, exc)
            api_line = f"HTTP API NOT started on {api.url}: {exc} - is another aggregator running? ('server start' retries)"
    else:
        api_line = f"HTTP API off (server.auto_start=false); 'server start' serves it on {api.url}"
    banner = (
        f"finance-aggregator {__version__} - provider chain: "
        f"{' -> '.join(p.name for p in app.providers) or '<empty>'}\n"
        f"{api_line}\n"
        "Type 'help' for commands, 'exit' to quit."
    )
    try:
        return InteractiveShell(app.context, banner=banner).run()
    finally:
        api.stop()
