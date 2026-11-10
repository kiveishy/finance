# Owl - stock viewer

Owl is a web-based stock viewer that consumes the [finance aggregator](../../data-providers/finance-aggregator/README.md).
It looks like VS Code (Dark+ workbench) and draws its charts in the TradingView style, using
TradingView Lightweight Charts. Because it is a web app, it runs in any browser, as a standalone
executable on Windows and Linux, and as an Android APK (a Capacitor wrapper around the same UI).

## Features

| Area | What you get |
|------|--------------|
| **Watchlist** | Groups shown as folders in the Explorer, with symbols inside. You can create, rename and delete groups, and add or remove symbols. Symbols are validated against the aggregator and company names are shown next to them. The same symbol can be in several groups |
| **Interactive search** | **Ctrl+P** (Go to symbol) and **Add symbol** suggest matches as you type, by ticker *or* company name (`nvid`, `apple`, `berkshire`). **Add symbol** asks for the stock first and only then for the group (skipped when there is a single group or when started from a group's **+** button); groups that already contain the stock are marked. Each match shows the symbol, the company name with the typed text highlighted, the exchange and type (Stock, ETF, Index, Future, ...), and which watchlist groups already contain it ("already in this group" when adding). Watchlist symbols appear instantly, and data-provider results follow after a 200 ms debounce. Arrow keys move through the list. Enter takes the highlighted match; pressed while results are still loading, it waits for them. A typed ticker that isn't listed is still accepted |
| **Ranges** | 1D · 1W · 1M · 3M · 6M · 1Y · 2Y · 5Y · ALL |
| **Candle interval** | Auto (daily up to 2Y, weekly for 5Y, monthly for ALL) or forced D / W / M |
| **Indicators** | MA 20 / MA 50 / MA 200 presets, plus custom SMA/EMA with any period (1-1000, up to 10). Settings are saved on the server, so desktop and phone share them. MAs are computed with enough extra history (warm-up) that they are valid from the first visible bar |
| **Chart** | Candles and indicator lines, with **volume as bars in a resizable pane at the bottom**. Mouse-wheel / pinch zoom and drag to scroll, plus zoom in, zoom out and reset buttons. A crosshair legend shows OHLC, change, volume, MA values and the **data provider of the bar** |
| **VS Code look** | Activity bar, Explorer tree, editor tabs (one per symbol, middle-click closes), breadcrumbs, a command-palette style quick input (**Ctrl+P** opens Go to symbol), notifications, and a status bar showing aggregator health and price bars per provider |
| **Mobile** | Below 768 px the sidebar becomes a drawer (☰), rows get touch-sized, and the toolbar scrolls horizontally |
| **Connection status** | The status bar always shows the data provider state: *online*, *degraded* (amber, with the list of providers that are down), *data provider unavailable*, *owl server offline*, or *unauthorized*. When data can't be loaded, a red banner explains why, shows a reconnect countdown, and offers **Retry now** and **Details** (each provider's state in fallback order). Owl reconnects automatically: it checks every 30 s while healthy, and after a failure retries with backoff (3 → 5 → 10 → 15 s). It also checks immediately when a request fails, when the network comes back, or when the app returns to the foreground. On recovery a notification appears and the open chart and watchlist reload by themselves. The server caches provider status for 5 s (`/api/status?refresh=1` bypasses the cache), so many clients don't hammer the providers |

## Architecture

```mermaid
flowchart LR
    subgraph Clients
        B[Browser]
        A[Android APK<br/>Capacitor WebView]
    end
    subgraph Owl server [owl - Python, stdlib HTTP]
        S[StaticFiles<br/>web/]
        API[OwlApi<br/>REST]
        CH[ChartService<br/>ranges, resampling, indicators]
        WL[(SQLite<br/>groups + settings)]
    end
    subgraph Data provider
        AGG[finance aggregator<br/>embedded or remote]
    end
    B --> S
    B --> API
    A -- configurable URL, CORS, API key --> API
    API --> WL
    API --> CH --> AGG
```

| Module | Responsibility |
|--------|----------------|
| `owl/market_data.py` | Owl's link to the data provider. In `embedded` mode it builds the aggregator's provider chain in-process (`finance_aggregator.app.create_aggregator`). In `remote` mode, `RemoteAggregatorSource` calls the aggregator's `serve` HTTP API. Both implement the aggregator contract (`StockDataSource` + `FinanceService`), so the rest of owl does not know which one it uses |
| `owl/charts.py` | `ChartRange`, `Interval` (daily → weekly/monthly resampling, which keeps the mixed provider tags), and the `Indicator` registry (`SimpleMovingAverage`, `ExponentialMovingAverage`). `ChartService` works out the fetch window (range + indicator warm-up, at least 400 days for daily charts) and builds the payload |
| `owl/watchlist.py` | `WatchlistRepository` interface, with `SqliteWatchlistRepository` as the implementation (groups, items, settings) |
| `owl/server.py` | Router, `OwlApi` (transport independent), `StaticFiles` (path-traversal safe, ETag), and the HTTP handler (CORS, API key, JSON-only writes, body size limit, security headers / CSP) |
| `owl/app.py` | Composition root (`OwlApplication`) and the command line |
| `web/` | No-build ES modules: `api.js` (REST client), `ui.js` (DOM helpers, quick input, notifications), `explorer.js`, `chart.js` (Lightweight Charts wrapper), `app.js` (workbench wiring). Vendored libraries are in `web/vendor/` (licenses included) |

**Data provenance.** The aggregator tags every price bar with the provider that supplied it, and
owl passes that tag all the way to the UI: the crosshair legend shows the source of the hovered
bar, and the status bar shows bars per provider. For example, `yahoo-db-api 444 · yahoo-finance 8`
means yahoo-db was stale and its tail was filled from Yahoo Finance.

### Adding an indicator

Subclass `Indicator` in `owl/charts.py`, implement `compute(closes)`, and register it in
`INDICATORS`. The REST API accepts the new type at once (`indicators=rsi:14`). For the UI, add
the type to `INDICATOR_RE` in `web/js/app.js`.

## REST API

All responses have the shape `{"status": "success", "data": ...}` or `{"status": "error", "error": "..."}`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Liveness check (no API key needed) |
| GET | `/api/status` | Owl version plus aggregator/provider status |
| GET | `/api/groups` | Groups with their symbols |
| POST | `/api/groups` `{"name"}` | Create a group |
| PATCH | `/api/groups/{id}` `{"name"}` | Rename a group |
| DELETE | `/api/groups/{id}` | Delete a group |
| POST | `/api/groups/{id}/items` `{"symbol"}` | Add a symbol (validated through the aggregator) |
| DELETE | `/api/groups/{id}/items/{symbol}` | Remove a symbol |
| GET | `/api/ticker/{symbol}` | Ticker information |
| GET | `/api/search?q=QUERY[&limit=N]` | Type-ahead symbol search through the aggregator (ticker or company name). Each match also lists the watchlist `groups` that contain it |
| GET | `/api/chart/{symbol}?range=6M&interval=D&indicators=sma:20,ema:9` | Bars (with `source`), indicator series, quote, `visible_from`, `sources` |
| GET/PUT | `/api/settings/chart` | Indicator settings (shared across devices) |

Status codes: 400 invalid input, 401 API key, 404 not found, 409 conflict, 415 non-JSON write,
502 provider error, 503 provider unavailable.

## Configuration (`config.json`)

| Key | Default | Description |
|-----|---------|-------------|
| `aggregator.mode` | `embedded` | `embedded`: run the provider chain in-process. `remote`: use a running aggregator (`finance-aggregator serve`) |
| `aggregator.config_file` | `../../data-providers/finance-aggregator/config.json` | Aggregator config for embedded mode |
| `aggregator.url` / `api_key` / `timeout_seconds` | `http://127.0.0.1:5100` | Remote mode connection |
| `server.host` / `port` | `127.0.0.1` / `8710` | Listen address |
| `server.api_key` | `null` | When set, every `/api/*` call except health needs `X-API-Key`. Can also be set with the env var `OWL_API_KEY` |
| `server.cors_origins` | `http://localhost`, `https://localhost`, `capacitor://localhost` | Origins allowed to call the API cross-origin (the Android app). `*` is rejected |
| `server.open_browser` | `true` | Open the UI when the server starts |
| `server.allow_insecure_remote` | `false` | Owl **refuses** to listen on a non-loopback address without an API key unless this is `true` |
| `browser.chrome` | `local` | Which Chrome opens the UI. `local`: the Chrome shipped in the repo (`browser.chrome_dir`), with a private profile, closed again when owl stops. `installed`: the Chrome installed on the PC, with the user's own profile. A missing local Chrome falls back to the installed one, and a missing Chrome to the default browser |
| `browser.chrome_dir` | `../../common/chrome` | Folder with the local `chrome.exe` (Windows build; on Linux the installed Chrome is used) |
| `browser.app_window` | `true` | Open as an app window (`--app`, no tabs or address bar) instead of a normal tab |
| `browser.exit_on_close` | `true` | Closing the local Chrome window stops owl. The local Chrome also ends with owl, even when owl is killed. This doesn't apply to the installed Chrome or the default browser, because owl can't tell when those windows close |
| `browser.profile_dir` | `null` | Profile of the local Chrome. Default `%LOCALAPPDATA%\owl\chrome-profile` (Linux: `~/.local/share/owl/chrome-profile`) |
| `storage.database` | `./data/owl.db` | Watchlist and settings database |
| `web.root` | `null` | Override the UI folder (default: `web/`, or the copy bundled in the executable) |
| `logging.*` | | Same keys as the aggregator (`level`, `file`, `max_bytes`, `backup_count`) |

Override the config path with `--config <path>` or the env var `OWL_CONFIG`. Command line
options: `--host`, `--port`, `--no-browser`, `--log-level`, `--version`.

## Run, build and dependencies

The run scripts share the aggregator's environment tooling: venv creation, detection and repair
of a moved venv, the `get-pip` fallback on Ubuntu, and the requirement/version check with a hash
stamp. Owl's `requirements.txt` includes the aggregator's requirements.

| | Windows | Linux |
|--|---------|-------|
| Run from source | `run.cmd [--no-browser]` | `./run.sh [--no-browser]` |
| Prepare the environment only | `run.cmd --setup-only` | `./run.sh --setup-only` |
| Standalone executable | `build.cmd [--skip-tests]` → `dist\windows\owl.exe` | `./build.sh [--skip-tests]` → `dist/linux/owl` |
| Virtual environment | `.venv-windows` (from `FINANCE_PYTHON`, otherwise the shared `python\3.14`) | `.venv-linux` (`python3`) |

Environment overrides: `OWL_VENV`, `OWL_AGGREGATOR_DIR`, `FINANCE_PYTHON`. On WSL with the
checkout on `/mnt/c`, put the venv on the Linux file system, e.g.
`OWL_VENV=$HOME/.venvs/owl ./build.sh`.

Each executable bundles the web UI, the aggregator package and the yahoo-db REST client. The
build writes `config.json` and `aggregator.json` (the provider chain) next to the executable;
existing files are never overwritten. Owl stores its data in `data/owl.db` next to the config.
`owl.exe` has no terminal window. The UI is the Chrome window, and closing it stops owl
(`browser.exit_on_close`). Startup errors, such as a bad config or a port in use, appear in a
message box and in `logs/owl.log`. Starting owl again while it runs just opens a window on the
running instance.
The local Chrome (~376 MB) is not bundled: the generated `config.json` points `browser.chrome_dir`
at the repo's `common/chrome`. To ship the executable elsewhere, copy that folder next to it and
set `"chrome_dir": "chrome"`.

## Android APK

`mobile/` is a Capacitor 8 project that packages `web/` into an Android app. The phone does not
run Python, so the app talks to an owl server over the network.

1. Install the build prerequisites: Node.js 20+, JDK 21, and the Android SDK (Android Studio, or
   the command line tools with `platforms;android-36` and `build-tools`). Set `ANDROID_HOME`.
2. Build:
   - Windows: `build-apk.cmd` → `dist\android\owl-debug.apk`
   - Linux: `./build-apk.sh`
   - Add `release` for an unsigned release APK, then sign it with `apksigner`.
3. Run an owl server the phone can reach:
   ```json
   "server": {"host": "0.0.0.0", "port": 8710, "api_key": "<long random string>"}
   ```
4. Install the APK. On first start the app asks for the server URL (e.g.
   `http://192.168.1.20:8710`) and the API key. You can change them later with Settings (gear
   icon).

The app's origin is `http://localhost`, which is already in `cors_origins`, and plain-HTTP
requests to the LAN server are allowed (`server.cleartext`). For access over the internet, put
owl behind HTTPS (a reverse proxy).

## Tests

```
.venv-windows\Scripts\python -m pytest
```

| Test file | What it covers |
|-----------|----------------|
| `test_charts.py` | SMA/EMA maths, indicator parsing, weekly/monthly resampling (including mixed provider tags), ranges, warm-up window, quote, 1D/ALL behaviour |
| `test_watchlist.py` | Group and item CRUD, conflicts, cascade delete, seeding exactly once, persistence, settings size limit |
| `test_server.py` | REST API status codes, no leaking of provider details, static path traversal, ETag/304, CSP, CORS allow-list, API key, JSON-only writes, body limit, refusing to bind insecurely |
| `test_market_data.py` | `RemoteAggregatorSource` against the **real aggregator HTTP server** (bar sources preserved, errors translated, unreachable server), embedded mode, config validation |

## Limitations and next steps

- **Daily data only.** The aggregator supplies daily bars, so 1D/1W show the most recent daily
  candles. Intraday bars (1m-1h) would need intraday support in the aggregator (Yahoo Finance
  provider) first.
- **No PWA service worker yet.** The UI needs the server to be reachable.
- The Linux daemon (`service.sh`) currently exists only for the aggregator. Owl can be run the
  same way: `owl --no-browser`.

## Third-party components

| Component | Location | License |
|-----------|----------|---------|
| TradingView Lightweight Charts™ 5.2.1 | `web/vendor/lightweight-charts/` | Apache-2.0. The TradingView attribution logo is kept on the chart, as the license notice requires |
| VS Code Codicons 0.0.36 | `web/vendor/codicons/` | CC-BY-4.0 (icons) / MIT (code), © Microsoft |
| Capacitor 8 | `mobile/` (npm) | MIT |
