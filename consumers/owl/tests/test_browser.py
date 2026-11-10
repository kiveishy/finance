from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from finance_aggregator.core import ConfigurationError
from owl.browser import BrowserLauncher, LOCAL_EXECUTABLES, find_local_chrome
from owl.config import BrowserConfig, OwlConfig

URL = "http://127.0.0.1:8710/"


class FakeProc:
    pid = 4242

    def __init__(self) -> None:
        self.terminated = False

    def poll(self) -> int | None:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.terminated = True


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Any]] = []
        self.defaults: list[str] = []
        self.procs: list[FakeProc] = []

    def popen(self, args: list[str], **kwargs: Any) -> FakeProc:
        self.calls.append((args, kwargs.get("env")))
        self.procs.append(FakeProc())
        return self.procs[-1]

    def launcher(self, config: BrowserConfig, installed: Path | None = None) -> BrowserLauncher:
        return BrowserLauncher(config, popen=self.popen, open_default=self.defaults.append,  # type: ignore[arg-type]
                               installed_finder=lambda: installed)


def _local_chrome(tmp_path: Path) -> Path:
    chrome_dir = tmp_path / "chrome"
    chrome_dir.mkdir()
    (chrome_dir / LOCAL_EXECUTABLES[0]).write_bytes(b"")
    return chrome_dir


def test_local_chrome_opens_private_app_window_and_is_closed_on_stop(tmp_path: Path) -> None:
    rec = Recorder()
    chrome_dir = _local_chrome(tmp_path)
    launcher = rec.launcher(BrowserConfig(chrome="local", chrome_dir=chrome_dir, profile_dir=tmp_path / "profile"))
    assert launcher.open(URL) == "local"
    args, env = rec.calls[0]
    assert args[0] == str(chrome_dir / LOCAL_EXECUTABLES[0])
    assert f"--app={URL}" in args and f"--user-data-dir={tmp_path / 'profile'}" in args
    assert (tmp_path / "profile").is_dir() and env["GOOGLE_API_KEY"] == "no"
    launcher.close()
    assert rec.procs[0].terminated


def test_missing_local_chrome_falls_back_to_installed_then_default(tmp_path: Path) -> None:
    rec = Recorder()
    installed = tmp_path / "installed-chrome.exe"
    config = BrowserConfig(chrome="local", chrome_dir=tmp_path / "nowhere", profile_dir=tmp_path / "p")
    assert rec.launcher(config, installed).open(URL) == "installed"
    args, env = rec.calls[0]
    assert args[:2] == [str(installed), f"--app={URL}"] and env is None
    assert not any(a.startswith("--user-data-dir") for a in args)  # the user's own Chrome profile
    assert rec.launcher(config, None).open(URL) == "default"
    assert rec.defaults == [URL]


def test_installed_choice_ignores_local_copy(tmp_path: Path) -> None:
    rec = Recorder()
    config = BrowserConfig(chrome="installed", chrome_dir=_local_chrome(tmp_path), app_window=False)
    launcher = rec.launcher(config, tmp_path / "installed")
    assert launcher.open(URL) == "installed"
    assert rec.calls[0][0][:2] == [str(tmp_path / "installed"), URL]
    launcher.close()  # nothing of ours to close
    assert not rec.procs[0].terminated


def test_find_local_chrome(tmp_path: Path) -> None:
    assert find_local_chrome(None) is None
    assert find_local_chrome(tmp_path) is None
    assert find_local_chrome(_local_chrome(tmp_path)) is not None


def test_browser_config_parsing(tmp_path: Path) -> None:
    base = {"aggregator": {"mode": "remote"}}
    default = OwlConfig.from_dict(base, base_dir=tmp_path / "a" / "b")
    assert default.browser.chrome == "local"
    assert default.browser.chrome_dir == (tmp_path / "common" / "chrome").resolve()
    custom = OwlConfig.from_dict(
        {**base, "browser": {"chrome": "installed", "chrome_dir": "c", "app_window": False, "profile_dir": "prof"}},
        base_dir=tmp_path,
    )
    assert (custom.browser.chrome, custom.browser.app_window) == ("installed", False)
    assert custom.browser.chrome_dir == tmp_path / "c" and custom.browser.profile_dir == tmp_path / "prof"
    with pytest.raises(ConfigurationError, match="browser.chrome"):
        OwlConfig.from_dict({**base, "browser": {"chrome": "edge"}}, base_dir=tmp_path)


def test_repo_config_points_at_shared_chrome() -> None:
    config = OwlConfig.load(Path(__file__).resolve().parent.parent / "config.json")
    assert config.browser.chrome_dir == (Path(__file__).resolve().parents[3] / "common" / "chrome")
