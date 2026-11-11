from pathlib import Path

from snp_monitor.browser import BrowserLauncher
from snp_monitor.config import BrowserConfig


class FakeProc:
    pid = 42
    returncode = 0

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0  # looks like a hand-off to a running Chrome: the watcher ignores it


def _launcher(config, calls, installed=None, opened=None):
    opened = [] if opened is None else opened

    def popen(args, **kwargs):
        calls.append(args)
        return FakeProc()

    return BrowserLauncher(config, popen=popen, open_default=opened.append,
                           installed_finder=lambda: installed, bind_children=lambda: None)


def test_local_chrome_is_used_with_private_profile(tmp_path: Path):
    chrome_dir = tmp_path / "chrome"
    chrome_dir.mkdir()
    (chrome_dir / "chrome.exe").write_bytes(b"")
    (chrome_dir / "chrome").write_bytes(b"")
    calls = []
    config = BrowserConfig(chrome="local", chrome_dir=chrome_dir, profile_dir=tmp_path / "profile")
    assert _launcher(config, calls).open("http://127.0.0.1:1") == "local"
    assert calls[0][1] == "--app=http://127.0.0.1:1"
    assert f"--user-data-dir={tmp_path / 'profile'}" in calls[0]


def test_missing_local_chrome_falls_back_to_installed_then_default(tmp_path: Path):
    calls, opened = [], []
    config = BrowserConfig(chrome="local", chrome_dir=tmp_path / "missing", profile_dir=tmp_path / "p")
    installed = tmp_path / "installed-chrome"
    assert _launcher(config, calls, installed=installed).open("http://x") == "installed"
    assert calls[0][0] == str(installed) and not any(a.startswith("--user-data-dir") for a in calls[0])
    assert _launcher(config, [], installed=None, opened=opened).open("http://x") == "default"
    assert opened == ["http://x"]
