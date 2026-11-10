"""Built-in CLI commands. Add a new command by subclassing `Command` and registering it."""

from __future__ import annotations

import argparse
import json
import signal
import threading
from typing import Any, Callable

from ..core.models import DateRange
from .base import Command, CommandArgumentParser, CommandContext, CommandError, CommandOutcome
from .formatting import render_history, render_status, render_ticker_info


def _print_json(ctx: CommandContext, payload: Any) -> None:
    ctx.out.write(json.dumps(payload, indent=2, default=str) + "\n")


class HelpCommand(Command):
    name = "help"
    aliases = ("?",)
    summary = "Show available commands or detailed help for one command"

    def configure_parser(self, parser: CommandArgumentParser) -> None:
        parser.add_argument("command", nargs="?", help="command to describe")

    def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
        if args.command:
            ctx.registry.resolve(args.command).create_parser(ctx.out).print_help()
            return CommandOutcome.CONTINUE
        commands = sorted(ctx.registry, key=lambda c: c.name)
        width = max(len(self._label(c)) for c in commands)
        ctx.out.write("Available commands:\n")
        for command in commands:
            ctx.out.write(f"  {self._label(command).ljust(width)}  {command.summary}\n")
        ctx.out.write("\nType 'help <command>' or '<command> --help' for details.\n")
        return CommandOutcome.CONTINUE

    @staticmethod
    def _label(command: Command) -> str:
        return " / ".join((command.name, *command.aliases))


class ExitCommand(Command):
    name = "exit"
    aliases = ("quit", "q")
    summary = "Leave the interactive shell"

    def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
        return CommandOutcome.EXIT


class StatusCommand(Command):
    name = "status"
    summary = "Show aggregator and provider status (in fallback priority order)"

    def configure_parser(self, parser: CommandArgumentParser) -> None:
        parser.add_argument("--json", action="store_true", help="print machine readable JSON")

    def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
        status = ctx.service.check_status()
        if args.json:
            _print_json(ctx, status.to_dict())
        else:
            ctx.out.write(render_status(status) + "\n")
        return CommandOutcome.CONTINUE


class ReadCommand(Command):
    name = "read"
    aliases = ("get",)
    summary = "Retrieve stock information and price history for a ticker"

    def configure_parser(self, parser: CommandArgumentParser) -> None:
        parser.add_argument("symbol", help="ticker symbol, e.g. AAPL, BRK-B, ^GSPC")
        what = parser.add_mutually_exclusive_group()
        what.add_argument("--info", action="store_true", help="only ticker information")
        what.add_argument("--history", action="store_true", help="only price history")
        period = parser.add_mutually_exclusive_group()
        period.add_argument("--days", type=int, help="history for the last N days (default 30)")
        period.add_argument("--months", type=int, help="history for the last N months")
        period.add_argument("--years", type=int, help="history for the last N years")
        period.add_argument("--start", help="history start date YYYY-MM-DD")
        parser.add_argument("--end", help="history end date YYYY-MM-DD (with --start, default today)")
        parser.add_argument("--limit", type=int, default=25, help="max history rows to print, 0 = all (default 25)")
        parser.add_argument("--json", action="store_true", help="print machine readable JSON")

    def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
        if args.end and not args.start:
            raise CommandError("--end requires --start")
        want_info = not args.history
        want_history = not args.info
        result: dict[str, Any] = {}

        if want_info:
            info = ctx.source.get_ticker_info(args.symbol)
            result["info"] = info.to_dict()
            if not args.json:
                ctx.out.write(render_ticker_info(info) + "\n")
        if want_history:
            date_range = DateRange.from_options(
                days=args.days, months=args.months, years=args.years, start=args.start, end=args.end
            )
            history = ctx.source.get_price_history(args.symbol, date_range)
            result["history"] = history.to_dict()
            if not args.json:
                ctx.out.write(("\n" if want_info else "") + render_history(history, limit=args.limit) + "\n")
        if args.json:
            _print_json(ctx, result)
        return CommandOutcome.CONTINUE


class ServeCommand(Command):
    name = "serve"
    summary = "Run the HTTP API server (foreground; used by the Linux daemon). Ctrl+C to stop"

    def configure_parser(self, parser: CommandArgumentParser) -> None:
        parser.add_argument("--host", help="bind address (default from config)")
        parser.add_argument("--port", type=int, help="port (default from config)")

    def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
        from ..server import ApiServer

        stop = threading.Event()
        restore = self._install_sigterm(stop)
        server = ApiServer(
            ctx.source,
            ctx.service,
            host=args.host or ctx.config.server.host,
            port=args.port if args.port is not None else ctx.config.server.port,
            api_key=ctx.config.server.api_key,
        )
        try:
            with server:
                ctx.out.write(f"Serving finance API on {server.url} (Ctrl+C to stop)\n")
                ctx.out.flush()
                # Short waits keep Ctrl+C responsive on Windows.
                while not stop.wait(0.5):
                    pass
        except KeyboardInterrupt:
            pass
        finally:
            restore()
        ctx.out.write("Server stopped\n")
        return CommandOutcome.CONTINUE

    @staticmethod
    def _install_sigterm(stop: threading.Event) -> Callable[[], None]:
        if threading.current_thread() is not threading.main_thread():
            return lambda: None
        previous = signal.signal(signal.SIGTERM, lambda *_: stop.set())

        def restore() -> None:
            signal.signal(signal.SIGTERM, previous)

        return restore


def builtin_commands() -> list[Command]:
    return [HelpCommand(), StatusCommand(), ReadCommand(), ServeCommand(), ExitCommand()]
