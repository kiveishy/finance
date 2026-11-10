"""Opens the owl UI in Chrome: the local copy shipped with the repo, or the Chrome installed on the PC."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable

from .config import BrowserConfig

logger = logging.getLogger(__name__)

LOCAL_EXECUTABLES = ("chrome.exe",) if sys.platform == "win32" else ("chrome", "google-chrome")
INSTALLED_LINUX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
WINDOW_SIZE = (1440, 900)
# A launch that only hands the window to an already running Chrome (same profile) exits this fast.
HANDOFF_SECONDS = 3.0


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


_job_handle: int | None = None


def kill_children_with_this_process() -> None:
    """Windows: put owl into a kill-on-close job, so Chrome ends with owl even when owl is killed."""
    global _job_handle
    if sys.platform != "win32" or _job_handle is not None:
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    job = kernel32.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800  # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    job_object_extended_limit_information = 9
    if not job or not kernel32.SetInformationJobObject(job, job_object_extended_limit_information,
                                                       ctypes.byref(limits), ctypes.sizeof(limits)) \
            or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        logger.info("Could not tie Chrome to owl's lifetime (Windows error %d)", ctypes.get_last_error())
        return
    _job_handle = job  # kept open for owl's lifetime; Windows closes it (and the job) when owl ends


class BrowserLauncher:
    """Opens the UI; reports when the local Chrome window it started is closed, and closes it when owl stops."""

    def __init__(
        self,
        config: BrowserConfig,
        *,
        on_closed: Callable[[], None] | None = None,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        open_default: Callable[[str], object] = webbrowser.open,
        installed_finder: Callable[[], Path | None] = find_installed_chrome,
        bind_children: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._on_closed = on_closed
        self._popen = popen
        self._open_default = open_default
        self._installed_finder = installed_finder
        self._bind_children = bind_children if bind_children is not None else kill_children_with_this_process
        self._lock = threading.Lock()
        self._local_proc: subprocess.Popen | None = None
        self._closing = False

    def open(self, url: str) -> str:
        """Open `url`; returns how it was opened: "local", "installed" or "default"."""
        with self._lock:
            if self._config.chrome == "local":
                local = find_local_chrome(self._config.chrome_dir)
                if local:
                    self._bind_children()
                    proc = self._launch(local, url, profile_dir=self._config.profile_dir)
                    logger.info("Opened owl in local Chrome %s (pid %d)", local, proc.pid)
                    if self._local_proc is None or self._local_proc.poll() is not None:
                        self._local_proc = proc
                        threading.Thread(target=self._watch, args=(proc, time.monotonic()),
                                         name="chrome-watch", daemon=True).start()
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
            self._closing = True
            proc, self._local_proc = self._local_proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _watch(self, proc: subprocess.Popen, started: float) -> None:
        proc.wait()
        with self._lock:
            if self._closing or proc is not self._local_proc:
                return
            self._local_proc = None
        if time.monotonic() - started < HANDOFF_SECONDS and proc.returncode == 0:
            logger.info("Chrome handed the owl window to an already running Chrome with the same profile; "
                        "owl keeps running when that window closes")
            return
        logger.info("Local Chrome window closed (exit code %s)", proc.returncode)
        if self._on_closed is not None:
            self._on_closed()

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
