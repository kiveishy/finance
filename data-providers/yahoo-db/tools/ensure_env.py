"""Validate/install the dependencies of the active virtual environment.

Used by run.cmd / run.sh (always executed with the venv interpreter):

    python tools/ensure_env.py requirements.txt [more-requirements.txt ...]

pip is only invoked when a requirement is missing/unsatisfied or a requirements file changed
since the last successful install (tracked by a hash stamp inside the venv).
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

MIN_PYTHON = (3, 10)
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def stamp_path(requirement_files: list[Path]) -> Path:
    # One stamp per requirements set, so different requirement files don't invalidate each other.
    key = "+".join(sorted(p.name for p in requirement_files))
    return Path(sys.prefix) / f".yahoo-db-{key}.sha256"


def _requirement_parser() -> Any:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        try:
            from pip._vendor.packaging.requirements import Requirement  # pip always ships it
        except ImportError:
            return None
    return Requirement


def read_requirements(path: Path, seen: set[Path] | None = None) -> tuple[list[str], list[Path]]:
    """Return (requirement lines, all files involved incl. nested -r includes)."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen:
        return [], []
    seen.add(path)
    lines: list[str] = []
    files = [path]
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            nested_lines, nested_files = read_requirements(path.parent / line.split(maxsplit=1)[1], seen)
            lines += nested_lines
            files += nested_files
        elif not line.startswith("-"):
            lines.append(line)
    return lines, files


def unsatisfied(requirements: list[str]) -> list[str]:
    requirement_cls = _requirement_parser()
    problems = []
    for line in requirements:
        if requirement_cls is not None:
            req = requirement_cls(line)
            if req.marker is not None and not req.marker.evaluate():
                continue
            name, spec = req.name, req.specifier
        else:
            match = _NAME_RE.match(line)
            name, spec = (match.group(1) if match else line), None
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"{line} (not installed)")
            continue
        if spec is not None and not spec.contains(installed, prereleases=True):
            problems.append(f"{line} (installed {installed})")
    return problems


def fingerprint(files: list[Path]) -> str:
    digest = hashlib.sha256(sys.version.encode())
    for file in sorted(files):
        digest.update(file.read_bytes())
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"[env] ERROR: Python {'.'.join(map(str, MIN_PYTHON))}+ required, found {sys.version.split()[0]}")
        return 1
    if sys.prefix == sys.base_prefix:
        print("[env] ERROR: not running inside a virtual environment")
        return 1
    if not argv:
        print("usage: ensure_env.py requirements.txt [...]")
        return 2
    if importlib.util.find_spec("pip") is None:
        print(f"[env] ERROR: pip is missing in {sys.prefix}; delete that folder and re-run "
              "(Debian/Ubuntu: sudo apt install python3-venv)")
        return 1

    requirement_files = [Path(a) for a in argv]
    lines: list[str] = []
    files: list[Path] = []
    for req_file in requirement_files:
        if not req_file.is_file():
            print(f"[env] ERROR: requirements file not found: {req_file}")
            return 1
        file_lines, file_list = read_requirements(req_file)
        lines += file_lines
        files += file_list

    current = fingerprint(files)
    stamp = stamp_path(requirement_files)
    stamp_ok = stamp.is_file() and stamp.read_text().strip() == current
    problems = unsatisfied(lines)
    if stamp_ok and not problems:
        print(f"[env] Dependencies OK ({len(lines)} requirements, Python {sys.version.split()[0]})")
        return 0

    reason = "; ".join(problems) if problems else "requirements changed"
    print(f"[env] Installing dependencies: {reason}")
    command = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    for req_file in requirement_files:
        command += ["-r", str(req_file)]
    if subprocess.call(command) != 0:
        print("[env] ERROR: pip install failed (check network/proxy settings)")
        return 1

    remaining = unsatisfied(lines)
    if remaining:
        print("[env] ERROR: still unsatisfied after install: " + "; ".join(remaining))
        return 1
    stamp.write_text(current)
    print("[env] Dependencies installed and validated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
