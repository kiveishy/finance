from __future__ import annotations

import argparse
import io
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from fakes import FakeProvider, not_found, unavailable
from finance_aggregator.cli import (
    Command,
    CommandContext,
    CommandOutcome,
    CommandRegistry,
    ExitCode,
    InteractiveShell,
    builtin_commands,
)
from finance_aggregator.cli.base import CommandArgumentParser
from finance_aggregator.cli.shell import CommandDispatcher
from finance_aggregator.config import AppConfig
from finance_aggregator.services import AggregatorService


def make_context(out: io.StringIO, *providers: FakeProvider, extra: list[Command] | None = None) -> CommandContext:
    aggregator = AggregatorService(providers or (FakeProvider("fake"),))
    registry = CommandRegistry(builtin_commands() + (extra or []))
    config = AppConfig.from_dict({"providers": []}, base_dir=Path("."))
    return CommandContext(source=aggregator, service=aggregator, config=config, registry=registry, out=out)


def run_shell(ctx: CommandContext, lines: list[str]) -> int:
    feed = iter(lines)

    def fake_input(prompt: str) -> str:
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    return InteractiveShell(ctx, input_func=fake_input).run()


def test_help_lists_all_commands(out: io.StringIO) -> None:
    ctx = make_context(out)
    run_shell(ctx, ["help", "exit"])
    text = out.getvalue()
    for name in ("status", "read", "help", "exit / quit / q", "serve"):
        assert name in text


def test_help_for_single_command(out: io.StringIO) -> None:
    run_shell(make_context(out), ["help read"])
    assert "--days" in out.getvalue()


@pytest.mark.parametrize("word", ["exit", "quit", "q", "EXIT"])
def test_exit_aliases_stop_shell(out: io.StringIO, word: str) -> None:
    ctx = make_context(out)
    assert run_shell(ctx, [word, "this-would-error"]) == 0
    assert "Unknown command" not in out.getvalue()


def test_shell_survives_errors_and_bad_arguments(out: io.StringIO) -> None:
    ctx = make_context(out, FakeProvider("p", info=not_found()))
    run_shell(ctx, ["bogus", "read", "read AAPL --info", "read AAPL --days x", 'read "unterminated', "read --help", "exit"])
    text = out.getvalue()
    assert "Unknown command 'bogus'" in text
    assert "the following arguments are required: symbol" in text
    assert "Not found:" in text
    assert "invalid int value" in text
    assert "cannot parse input" in text
    assert "usage: read" in text


def test_read_prints_info_and_history_with_source(out: io.StringIO) -> None:
    ctx = make_context(out, FakeProvider("local", available=False), FakeProvider("api"))
    CommandDispatcher(ctx).dispatch(["read", "msft", "--days", "3"])
    text = out.getvalue()
    assert "MSFT from api" in text
    assert "sources: api (4 bars)" in text
    assert (date.today() - timedelta(days=3)).isoformat() in text


def test_read_json_output(out: io.StringIO) -> None:
    ctx = make_context(out, FakeProvider("api"))
    _, code = CommandDispatcher(ctx).dispatch(["read", "AAPL", "--start", "2026-01-01", "--end", "2026-01-03", "--json"])
    payload = json.loads(out.getvalue())
    assert code is ExitCode.OK
    assert payload["info"]["symbol"] == "AAPL"
    assert payload["history"]["count"] == 3
    assert {bar["source"] for bar in payload["history"]["data"]} == {"api"}
    assert payload["history"]["sources"] == {"api": 3}


def test_read_history_only_and_limit(out: io.StringIO) -> None:
    ctx = make_context(out, FakeProvider("api"))
    CommandDispatcher(ctx).dispatch(["read", "AAPL", "--history", "--days", "40", "--limit", "5"])
    text = out.getvalue()
    assert "Symbol" not in text
    assert "showing last 5 rows" in text


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["read", "AAPL", "--end", "2026-01-01"], ExitCode.USAGE),
        (["read", "../etc"], ExitCode.USAGE),
        (["read", "AAPL", "--info"], ExitCode.UNAVAILABLE),
    ],
)
def test_dispatcher_exit_codes(out: io.StringIO, argv: list[str], expected: ExitCode) -> None:
    ctx = make_context(out, FakeProvider("p", info=unavailable()))
    assert CommandDispatcher(ctx).dispatch(argv)[1] is expected


def test_status_command(out: io.StringIO) -> None:
    ctx = make_context(out, FakeProvider("local", probe=unavailable()), FakeProvider("api"))
    CommandDispatcher(ctx).dispatch(["status"])
    text = out.getvalue()
    assert "DEGRADED" in text and "1/2 providers available" in text
    assert text.index("local") < text.index("api")
    out.truncate(0), out.seek(0)
    CommandDispatcher(ctx).dispatch(["status", "--json"])
    assert json.loads(out.getvalue())["state"] == "degraded"


def test_custom_command_can_be_plugged_in(out: io.StringIO) -> None:
    class EchoCommand(Command):
        name = "echo"
        summary = "Echo words"

        def configure_parser(self, parser: CommandArgumentParser) -> None:
            parser.add_argument("words", nargs="*")

        def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
            ctx.out.write(" ".join(args.words) + "\n")
            return CommandOutcome.CONTINUE

    ctx = make_context(out, extra=[EchoCommand()])
    run_shell(ctx, ["echo hello world", "help", "exit"])
    assert "hello world" in out.getvalue()
    assert "Echo words" in out.getvalue()


def test_duplicate_command_alias_rejected() -> None:
    class Clash(Command):
        name = "leave"
        aliases = ("quit",)

        def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome:
            return CommandOutcome.EXIT

    with pytest.raises(ValueError):
        CommandRegistry(builtin_commands() + [Clash()])
