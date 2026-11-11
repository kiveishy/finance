# snp-monitor

A desktop web app that shows all **S&P 500** stocks with their current prices. Above the list are two pie charts:

| Chart | What a slice means |
|-------|--------------------|
| **Share of stock price** | One stock's price as % of the sum of all S&P 500 stock prices: `price / Σ prices` |
| **Share of company value** | One company's market cap as % of the total S&P 500 market cap: `market cap / Σ market caps` |

The charts show the top 10/15/20/30 slices plus an "Other …" slice. Hover a slice or legend entry for details,
or click it to find the stock in the list. The list can be sorted by any column and filtered by text or sector.
Prices refresh every 60 s.

Companies with two share classes (Alphabet GOOGL/GOOG, Fox FOXA/FOX, News Corp NWSA/NWS) are counted **once**
in the company-value chart. Yahoo reports the whole company's value on each share class, and the two lines are
grouped by their SEC CIK.

## Run

```bat
run.cmd                 :: Windows: create/check .venv-windows, then start and open the UI
run.cmd --no-browser    :: start the server only (http://127.0.0.1:8720)
run.cmd --setup-only    :: only prepare the virtual environment
```

```bash
./run.sh                # Linux/macOS (.venv-linux)
```

On each start, the run scripts create the virtual environment if it is missing, or recreate it if it is
incomplete or was moved. They then check the dependencies (`tools/ensure_env.py`) and install them only when
something is missing or `requirements.txt` changed.

The base interpreter is `..\..\..\..\python\3.14\python.exe` (the shared Python 3.14). On Linux it is `python3`.
You can override it with `SNP_PYTHON`. Other overrides: `SNP_VENV`, `SNP_REQUIREMENTS`, `SNP_MONITOR_CONFIG`.

Only one instance runs per port. Starting the app again just opens the window of the running instance.

## Build an executable

```bat
build.cmd               :: tests + PyInstaller -> dist\windows\snp-monitor.exe (+ config.json)
build.cmd --skip-tests
```

```bash
./build.sh              # -> dist/linux/snp-monitor
```

The Windows exe has no terminal window: errors appear in a message box and in `logs\snp-monitor.log`.
Closing the Chrome window stops the app. Close a running `snp-monitor.exe` before rebuilding.

## Browser

`config.json` → `browser`:

| Key | Default | Meaning |
|-----|---------|---------|
| `chrome` | `"local"` | `"local"`: the Chrome in `chrome_dir` (repo `common/chrome`) with its own profile. `"installed"`: the Chrome installed on the PC. Falls back to the installed Chrome, then to the default browser |
| `chrome_dir` | `../../common/chrome` | Folder of the local `chrome.exe` (rebased automatically in the built `dist` config) |
| `app_window` | `true` | Window without tabs/address bar |
| `exit_on_close` | `true` | Stop the app when the local Chrome window is closed |
| `profile_dir` | `%LOCALAPPDATA%\snp-monitor\chrome-profile` | Profile of the local Chrome |

## Data

| What | Source |
|------|--------|
| Member list | [Wikipedia](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies), then the [datasets CSV](https://github.com/datasets/s-and-p-500-companies) as a fallback. Cached in `data/constituents.json` for `constituents_refresh_hours` (24 h). When offline it uses the stale cache, then the bundled `snp_monitor/sp500_snapshot.json` |
| Prices, day change, market cap | Yahoo Finance batch quotes via yfinance's session (all ~503 symbols in about 5 s) |

`config.json` → `data`: `constituent_sources`, `constituents_refresh_hours`, `quote_refresh_seconds` (≥ 10),
`cache_dir`, `request_timeout_seconds`.

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | `{version, app: "snp-monitor", web_build}` |
| GET | `/api/snapshot` | All stocks (price, change, market cap, `price_share`, `company_share`), the companies (market cap, share), a summary, and `error` if the last refresh failed |
| POST | `/api/refresh` | Request an immediate refresh (`Content-Type: application/json`). Returns 429 within 10 s of the previous one |

The server binds to `127.0.0.1` by default. It is read-only, has no login, and does not use a CDN: the UI is plain
HTML/CSS/JS with hand-drawn SVG charts, so it also works without internet access to CDNs.

## Tests

```bat
.venv-windows\Scripts\python -m pytest -q
```
