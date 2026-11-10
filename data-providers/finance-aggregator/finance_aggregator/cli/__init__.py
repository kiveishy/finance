"""Interactive command line interface."""

from .base import (
    Command,
    CommandArgumentParser,
    CommandContext,
    CommandError,
    CommandOutcome,
    CommandRegistry,
)
from .commands import builtin_commands
from .shell import CommandDispatcher, ExitCode, InteractiveShell, run_once

__all__ = [
    "Command",
    "CommandArgumentParser",
    "CommandContext",
    "CommandDispatcher",
    "CommandError",
    "CommandOutcome",
    "CommandRegistry",
    "ExitCode",
    "InteractiveShell",
    "builtin_commands",
    "run_once",
]
