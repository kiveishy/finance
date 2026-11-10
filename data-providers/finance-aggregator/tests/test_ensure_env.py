from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ensure_env", Path(__file__).resolve().parent.parent / "tools" / "ensure_env.py"
)
assert _SPEC and _SPEC.loader
ensure_env = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ensure_env)


def test_nested_requirements_and_comments(tmp_path: Path) -> None:
    (tmp_path / "base.txt").write_text("pytest>=1.0  # inline\n# comment\n\n--index-url https://x\n")
    (tmp_path / "dev.txt").write_text("-r base.txt\nnot-a-real-package-xyz==1.0\n")
    lines, files = ensure_env.read_requirements(tmp_path / "dev.txt")
    assert lines == ["pytest>=1.0", "not-a-real-package-xyz==1.0"]
    assert {f.name for f in files} == {"dev.txt", "base.txt"}


def test_unsatisfied_detects_missing_and_wrong_versions() -> None:
    problems = ensure_env.unsatisfied(["pytest>=1.0", "pytest<1.0", "not-a-real-package-xyz"])
    assert len(problems) == 2
    assert any("installed" in p for p in problems)
    assert any("not installed" in p for p in problems)


def test_stamp_is_per_requirements_set() -> None:
    assert ensure_env.stamp_path([Path("requirements.txt")]) != ensure_env.stamp_path([Path("requirements-dev.txt")])
