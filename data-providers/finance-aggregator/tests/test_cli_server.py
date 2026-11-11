"""The interactive CLI serves the HTTP API in the background (server.auto_start) and controls it via 'server'."""

from __future__ import annotations

import io
import json
import socket
import urllib.request
from pathlib import Path

import pytest

from fakes import FakeProvider
from finance_aggregator import app as app_module
from finance_aggregator.cli import CommandContext, CommandRegistry, InteractiveShell, builtin_commands
from finance_aggregator.config import AppConfig
from finance_aggregator.core.errors import ConfigurationError
from finance_aggregator.server import BackgroundApi
from finance_aggregator.services import AggregatorService


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _health(url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{url}/api/health", timeout=5) as response:
        return json.loads(response.read())


def _shell(ctx: CommandContext, lines: list[str], on_line=None) -> int:
    feed = iter(lines)

    def fake_input(prompt: str) -> str:
        try:
            line = next(feed)
        except StopIteration:
            raise EOFError from None
        if on_line:
            on_line(line)
        return line

    return InteractiveShell(ctx, input_func=fake_input).run()


def _context(out: io.StringIO, port: int) -> tuple[CommandContext, BackgroundApi]:
    aggregator = AggregatorService([FakeProvider("fake")])
    config = AppConfig.from_dict({"providers": [], "server": {"port": port}}, base_dir=Path("."))
    api = BackgroundApi(aggregator, aggregator, host="127.0.0.1", port=port)
    ctx = CommandContext(source=aggregator, service=aggregator, config=config,
                         registry=CommandRegistry(builtin_commands()), out=out, api=api)
    return ctx, api


def test_server_command_starts_and_stops_the_api(out: io.StringIO) -> None:
    ctx, api = _context(out, _free_port())
    seen: list[dict] = []
    try:
        _shell(ctx, ["server", "server start", "status", "server stop", "server"],
               on_line=lambda line: seen.append(_health(api.url)) if line == "status" else None)
    finally:
        api.stop()
    text = out.getvalue()
    assert "stopped, would listen on" in text and "HTTP API running at http://127.0.0.1:" in text
    assert "HTTP API stopped" in text
    assert seen and seen[0]["status"] == "success"  # the API answered while the shell was in use


def test_serve_does_not_start_a_second_server_next_to_the_background_one(out: io.StringIO) -> None:
    ctx, api = _context(out, _free_port())
    api.start()
    try:
        _shell(ctx, ["serve"])
    finally:
        api.stop()
    assert "already runs at" in out.getvalue()


def test_port_in_use_is_reported_and_the_shell_still_works(out: io.StringIO) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        ctx, api = _context(out, blocker.getsockname()[1])
        code = _shell(ctx, ["server start", "help"])
    assert code == 0
    assert "cannot start the HTTP API" in out.getvalue() and "Available commands" in out.getvalue()


def test_run_without_command_serves_the_api_with_the_shell(tmp_path: Path, monkeypatch, capsys) -> None:
    port = _free_port()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"providers": [], "server": {"port": port}, "logging": {"file": None}}))
    answers: list[dict] = []

    def fake_run(self) -> int:  # stands in for the user typing in the shell
        answers.append(_health(f"http://127.0.0.1:{port}"))
        return 0

    monkeypatch.setattr(InteractiveShell, "run", fake_run)
    assert app_module.main(["--config", str(config)]) == 0
    assert answers and answers[0]["status"] == "success"
    with pytest.raises(OSError):  # stopped again when the shell ends
        _health(f"http://127.0.0.1:{port}")


def test_auto_start_must_be_boolean() -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_dict({"providers": [], "server": {"auto_start": "yes"}}, base_dir=Path("."))
    assert AppConfig.from_dict({"providers": []}, base_dir=Path(".")).server.auto_start is True
