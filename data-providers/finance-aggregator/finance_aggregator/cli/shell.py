"""Command dispatching, interactive shell and one-shot execution."""

from __future__ import annotations

import logging
import shlex
import sys
from enum import IntEnum
from typing import Callable, Sequence, TextIO

from ..core.errors import (
    DataNotFoundError,
    FinanceError,
    InvalidRequestError,
    ProviderError,
    ProviderUnavailableError,
)
from .base import CommandContext, CommandError, CommandOutcome

logger = logging.getLogger(__name__)


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    UNAVAILABLE = 4


class CommandDispatcher:
    """Runs one command and turns every failure into a message plus an exit code."""

    def __init__(self, ctx: CommandContext, *, err: TextIO | None = None) -> None:
        self._ctx = ctx
        self._err = err or ctx.out

    def dispatch(self, argv: Sequence[str]) -> tuple[CommandOutcome, ExitCode]:
        if not argv:
            return CommandOutcome.CONTINUE, ExitCode.OK
        try:
            command = self._ctx.registry.resolve(argv[0])
            return command.run(list(argv[1:]), self._ctx), ExitCode.OK
        except CommandError as exc:
            self._report(f"Error: {exc}")
            return CommandOutcome.CONTINUE, ExitCode.USAGE
        except InvalidRequestError as exc:
            self._report(f"Invalid request: {exc}")
            return CommandOutcome.CONTINUE, ExitCode.USAGE
        except DataNotFoundError as exc:
            self._report(f"Not found: {exc}")
            return CommandOutcome.CONTINUE, ExitCode.NOT_FOUND
        except ProviderUnavailableError as exc:
            self._report(f"Unavailable: {exc}")
            return CommandOutcome.CONTINUE, ExitCode.UNAVAILABLE
        except (ProviderError, FinanceError) as exc:
            self._report(f"Error: {exc}")
            return CommandOutcome.CONTINUE, ExitCode.ERROR
        except Exception as exc:  # noqa: BLE001 - the shell must keep running
            logger.exception("Command %r failed", argv[0])
            self._report(f"Unexpected error: {type(exc).__name__}: {exc} (details in log)")
            return CommandOutcome.CONTINUE, ExitCode.ERROR

    def _report(self, message: str) -> None:
        self._err.write(message.rstrip() + "\n")
        self._err.flush()


class InteractiveShell:
    def __init__(
        self,
        ctx: CommandContext,
        *,
        prompt: str = "finance> ",
        banner: str = "",
        input_func: Callable[[str], str] = input,
    ) -> None:
        self._ctx = ctx
        self._dispatcher = CommandDispatcher(ctx)
        self._prompt = prompt
        self._banner = banner
        self._input = input_func

    def run(self) -> int:
        _enable_line_editing()
        if self._banner:
            self._ctx.out.write(self._banner + "\n")
        while True:
            try:
                line = self._input(self._prompt)
            except EOFError:
                self._ctx.out.write("\n")
                return ExitCode.OK
            except KeyboardInterrupt:
                self._ctx.out.write("\n(Use 'exit' to quit)\n")
                continue
            try:
                argv = shlex.split(line, posix=True)
            except ValueError as exc:
                self._ctx.out.write(f"Error: cannot parse input: {exc}\n")
                continue
            try:
                outcome, _ = self._dispatcher.dispatch(argv)
            except KeyboardInterrupt:
                self._ctx.out.write("\nInterrupted\n")
                continue
            if outcome is CommandOutcome.EXIT:
                return ExitCode.OK


def run_once(ctx: CommandContext, argv: Sequence[str]) -> int:
    _, code = CommandDispatcher(ctx, err=sys.stderr).dispatch(argv)
    return int(code)


def _enable_line_editing() -> None:
    try:
        import readline  # noqa: F401 - side effect: history/arrow keys on POSIX
    except ImportError:
        pass
