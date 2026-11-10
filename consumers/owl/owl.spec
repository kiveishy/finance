# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec shared by build.cmd (Windows) and build.sh (Linux).
#   python -m PyInstaller --noconfirm --clean --distpath dist/<os> --workpath build/<os> owl.spec
import json
import os
import sys
from pathlib import Path

app_dir = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
dist_dir = Path(DISTPATH).resolve()  # noqa: F821 - provided by PyInstaller
aggregator_dir = Path(os.environ.get("OWL_AGGREGATOR_DIR", app_dir.parent.parent / "data-providers" / "finance-aggregator")).resolve()
aggregator_config_path = aggregator_dir / "config.json"
yahoo_db_client = Path(os.environ.get("YAHOO_DB_DIR", aggregator_dir.parent / "yahoo-db")).resolve() / "yahoodb-api.py"
for required in (aggregator_dir / "finance_aggregator", yahoo_db_client):
    if not required.exists():
        raise SystemExit(f"required source not found: {required}")

a = Analysis(  # noqa: F821
    [str(app_dir / "main.py")],
    pathex=[str(app_dir), str(aggregator_dir)],
    datas=[(str(app_dir / "web"), "web"), (str(yahoo_db_client), "yahoo_db")],
    # requests is only imported by the yahoo-db client, which is loaded by path at runtime
    hiddenimports=["requests"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "_pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="owl",
    # Windows: no terminal window - the UI is the Chrome window, errors go to logs/owl.log and a message box.
    console=sys.platform != "win32",
    upx=False,
    # Long-running server: keep the onefile extraction out of %TEMP% (Storage Sense cleans it).
    runtime_tmpdir=r"%LOCALAPPDATA%\owl\runtime" if sys.platform == "win32" else None,
)


def _rebase(value, source_dir: Path) -> str:
    """Re-express a path relative to `source_dir` as a path relative to the dist folder."""
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return value
    target = (source_dir / value).resolve()
    try:
        return os.path.relpath(target, dist_dir)
    except ValueError:  # different drive on Windows
        return str(target)


def _write_once(path: Path, content: dict) -> None:
    if path.exists():
        print(f"[spec] keeping existing {path}")
        return
    path.write_text(json.dumps(content, indent=4) + "\n", encoding="utf-8")
    print(f"[spec] wrote {path}")


# Standalone layout: owl(.exe) + config.json + aggregator.json (provider chain for embedded mode).
aggregator_config = json.loads(aggregator_config_path.read_text(encoding="utf-8"))
for provider in aggregator_config.get("providers", []):
    options = provider.get("options") or {}
    for key in ("db_path", "client_source_dir"):
        if key in options:
            options[key] = _rebase(options[key], aggregator_dir)
aggregator_config.pop("logging", None)  # owl owns logging
_write_once(dist_dir / "aggregator.json", aggregator_config)

owl_config = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
owl_config["aggregator"]["config_file"] = "aggregator.json"
browser = owl_config.setdefault("browser", {})
browser["chrome_dir"] = _rebase(browser.get("chrome_dir") or "../../common/chrome", app_dir)
_write_once(dist_dir / "config.json", owl_config)
