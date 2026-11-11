# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec shared by build.cmd (Windows) and build.sh (Linux).
#   python -m PyInstaller --noconfirm --clean --distpath dist/<os> --workpath build/<os> owl.spec
import json
import os
import sys
from pathlib import Path

app_dir = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
dist_dir = Path(DISTPATH).resolve()  # noqa: F821 - provided by PyInstaller
# Only the aggregator's data contracts (finance_aggregator.core, stdlib-only) are bundled; owl gets data over HTTP.
aggregator_dir = Path(os.environ.get("OWL_AGGREGATOR_DIR", app_dir.parent.parent / "data-providers" / "finance-aggregator")).resolve()
if not (aggregator_dir / "finance_aggregator").exists():
    raise SystemExit(f"required source not found: {aggregator_dir / 'finance_aggregator'}")

a = Analysis(  # noqa: F821
    [str(app_dir / "main.py")],
    pathex=[str(app_dir), str(aggregator_dir)],
    datas=[(str(app_dir / "web"), "web")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "_pytest", "yfinance", "pandas", "numpy", "requests"],
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


# Standalone layout: owl(.exe) + config.json. An existing config.json is kept (it holds your settings),
# only the settings of the removed embedded aggregator and split correction are dropped from it.
config_path = dist_dir / "config.json"
if config_path.exists():
    owl_config = json.loads(config_path.read_text(encoding="utf-8"))
    obsolete = [key for key in ("mode", "config_file") if key in owl_config.get("aggregator", {})]
    if obsolete or "splits" in owl_config:
        for key in obsolete:
            owl_config["aggregator"].pop(key)
        owl_config.pop("splits", None)
        config_path.write_text(json.dumps(owl_config, indent=4) + "\n", encoding="utf-8")
        print(f"[spec] removed obsolete settings from {config_path}")
    else:
        print(f"[spec] keeping existing {config_path}")
    old_chain = dist_dir / "aggregator.json"
    if old_chain.exists():
        old_chain.unlink()
        print(f"[spec] removed {old_chain} (owl no longer runs the aggregator itself)")
else:
    owl_config = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
    browser = owl_config.setdefault("browser", {})
    browser["chrome_dir"] = _rebase(browser.get("chrome_dir") or "../../common/chrome", app_dir)
    config_path.write_text(json.dumps(owl_config, indent=4) + "\n", encoding="utf-8")
    print(f"[spec] wrote {config_path}")
