"""Extensible command framework for the interactive CLI.

A command is a `Command` subclass registered in a `CommandRegistry`. Commands receive their
dependencies through `CommandContext` (no globals), which keeps them unit-testable.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar, Iterable, Iterator, NoReturn, TextIO

if TYPE_CHECKING:
    from ..config import AppConfig
    from ..core.interfaces import FinanceService, StockDataSource


class CommandOutcome(Enum):
    CONTINUE = "continue"
    EXIT = "exit"


class CommandError(Exception):
    """Invalid command usage (unknown command, bad arguments)."""


class _HelpShown(Exception):
    """Raised by the argument parser after printing --help."""


class CommandArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that never calls sys.exit (the shell must survive bad input)."""

    def __init__(self, *args: object, out: TextIO, **kwargs: object) -> None:
        super().__init__(*args, add_help=True, **kwargs)  # type: ignore[arg-type]
        self._out = out

    def print_help(self, file: TextIO | None = None) -> None:
        super().print_help(file or self._out)

    def error(self, message: str) -> NoReturn:
        raise CommandError(f"{message}\nusage: {self.format_usage().removeprefix('usage: ').strip()}")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if status:
            raise CommandError(message or "invalid arguments")
        raise _HelpShown()


@dataclass
class CommandContext:
    source: "StockDataSource"
    service: "FinanceService"
    config: "AppConfig"
    registry: "CommandRegistry"
    out: TextIO


class Command(ABC):
    name: ClassVar[str]
    aliases: ClassVar[tuple[str, ...]] = ()
    summary: ClassVar[str] = ""

    def configure_parser(self, parser: CommandArgumentParser) -> None:
        """Declare arguments. Default: none."""

    def create_parser(self, out: TextIO) -> CommandArgumentParser:
        parser = CommandArgumentParser(prog=self.name, description=self.summary, out=out)
        self.configure_parser(parser)
        return parser

    def run(self, argv: list[str], ctx: CommandContext) -> CommandOutcome:
        try:
            args = self.create_parser(ctx.out).parse_args(argv)
        except _HelpShown:
            return CommandOutcome.CONTINUE
        return self.execute(args, ctx)

    @abstractmethod
    def execute(self, args: argparse.Namespace, ctx: CommandContext) -> CommandOutcome: ...


class CommandRegistry:
    def __init__(self, commands: Iterable[Command] = ()) -> None:
        self._commands: dict[str, Command] = {}
        self._lookup: dict[str, Command] = {}
        for command in commands:
            self.register(command)

    def register(self, command: Command) -> None:
        for key in (command.name, *command.aliases):
            if key in self._lookup:
                raise ValueError(f"Command name/alias {key!r} already registered")
        self._commands[command.name] = command
        for key in (command.name, *command.aliases):
            self._lookup[key] = command

    def resolve(self, name: str) -> Command:
        command = self._lookup.get(name.lower())
        if command is None:
            raise CommandError(f"Unknown command {name!r}. Type 'help' for the list of commands.")
        return command

    def __iter__(self) -> Iterator[Command]:
        return iter(self._commands.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.lower() in self._lookup
