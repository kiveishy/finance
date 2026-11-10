"""Opens the owl UI in Chrome: the local copy shipped with the repo, or the Chrome installed on the PC."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable

from .config import BrowserConfig

logger = logging.getLogger(__name__)

LOCAL_EXECUTABLES = ("chrome.exe",) if sys.platform == "win32" else ("chrome", "google-chrome")
INSTALLED_LINUX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
WINDOW_SIZE = (1440, 900)


def find_local_chrome(chrome_dir: Path | None) -> Path | None:
    if chrome_dir is None:
        return None
    for name in LOCAL_EXECUTABLES:
        candidate = chrome_dir / name
        if candidate.is_file():
            return candidate
    return None


def find_installed_chrome() -> Path | None:
    """Chrome installed on this PC (registry/known folders on Windows, PATH on Linux/macOS)."""
    if sys.platform == "win32":
        candidates = [_registry_chrome()]
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        candidates = [Path(found) for name in INSTALLED_LINUX_NAMES if (found := shutil.which(name))]
    return next((c for c in candidates if c is not None and c.is_file()), None)


def _registry_chrome() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                return Path(winreg.QueryValue(key, None))
        except OSError:
            continue
    return None


class BrowserLauncher:
    """Opens the UI once per call; closes the local Chrome window it started when owl stops."""

    def __init__(
        self,
        config: BrowserConfig,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        open_default: Callable[[str], object] = webbrowser.open,
        installed_finder: Callable[[], Path | None] = find_installed_chrome,
    ) -> None:
        self._config = config
        self._popen = popen
        self._open_default = open_default
        self._installed_finder = installed_finder
        self._lock = threading.Lock()
        self._local_proc: subprocess.Popen | None = None

    def open(self, url: str) -> str:
        """Open `url`; returns how it was opened: "local", "installed" or "default"."""
        with self._lock:
            if self._config.chrome == "local":
                local = find_local_chrome(self._config.chrome_dir)
                if local:
                    self._local_proc = self._launch(local, url, profile_dir=self._config.profile_dir)
                    logger.info("Opened owl in local Chrome %s (pid %d)", local, self._local_proc.pid)
                    return "local"
                logger.warning("Local Chrome not found in %s - using the Chrome installed on this PC", self._config.chrome_dir)
            installed = self._installed_finder()
            if installed:
                # The user's own profile: hands the window to an already running Chrome.
                self._launch(installed, url, profile_dir=None)
                logger.info("Opened owl in installed Chrome %s", installed)
                return "installed"
            logger.warning("No Chrome found - opening owl in the default browser")
            self._open_default(url)
            return "default"

    def close(self) -> None:
        with self._lock:
            proc, self._local_proc = self._local_proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _launch(self, chrome: Path, url: str, *, profile_dir: Path | None) -> subprocess.Popen:
        args = [str(chrome), f"--app={url}" if self._config.app_window else url, "--new-window"]
        env = None
        if profile_dir is not None:
            profile_dir.mkdir(parents=True, exist_ok=True)
            width, height = WINDOW_SIZE
            args += [
                f"--user-data-dir={profile_dir}",
                f"--window-size={width},{height}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-client-side-phishing-detection",
                "--disable-features=Translate",
            ]
            # Silences the "Google API keys are missing" bar of a Chrome build without keys.
            env = dict(os.environ, GOOGLE_API_KEY="no", GOOGLE_DEFAULT_CLIENT_ID="no", GOOGLE_DEFAULT_CLIENT_SECRET="no")
        return self._popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
