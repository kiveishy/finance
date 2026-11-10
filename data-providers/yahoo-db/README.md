# Stock Data Service v0.0.0.6

A Flask-based web application that connects to Yahoo Finance API, retrieves stock market data, and stores it in a local SQLite database. Includes a web dashboard, RESTful API, and NYSE/NYSE Arca ticker synchronization.

## What's New in v0.0.0.6

- **Persistent Database Connection** — The application now opens a single database connection at startup and reuses it for all requests, eliminating the overhead of connecting and disconnecting on every API call.
- **Automatic Reconnection** — If the database connection drops (e.g. file lock timeout, I/O error), the connection is transparently re-established before the next operation.
- **Retry Mechanism** — All database operations are wrapped with an exponential-backoff retry decorator (up to 3 attempts). If an operation fails due to a transient SQLite error, it is retried automatically after a short delay.
- **Graceful Shutdown** — An `atexit` hook ensures the database connection is cleanly closed when the Flask application exits.

## Project Overview

This service provides:

1. **Web Dashboard** - Browser-based interface to monitor and control the service
2. **Real-time Status** - View current synchronization status and statistics
3. **Start/Stop Sync** - Control database synchronization from the web interface
4. **Add Tickers** - Add new stock tickers with automatic data download
5. **RESTful API** - External access for other applications
6. **Historical Data** - Fetch stock prices by date range (days, months, years)
7. **NYSE/NYSE Arca Sync** - Sync NYSE and NYSE Arca tickers to database with full historical data

## Features

### Web Interface
- 📊 **Dashboard** - View current status, ticker count, and last sync time
- 🔄 **Sync Controls** - Start and stop synchronization with one click
- ➕ **Add Tickers** - Add new stock symbols to track
- 📈 **Progress Tracking** - Real-time progress during synchronization

### REST API
- Get list of all tickers in database
- Get detailed ticker information
- Get historical stock prices (by days, months, years, or date range)
- Add new tickers programmatically
- Control synchronization

### NYSE & NYSE Arca Synchronization
- Download complete NYSE and NYSE Arca ticker lists from official sources
- Compare with existing database to find new tickers
- Download 10 years of daily historical data for new tickers
- Support for ETFs and other NYSE Arca-listed securities (BITB, SPY, QQQ, etc.)
- Progress tracking and detailed statistics
- Dry-run mode to preview changes
- Sync NYSE only, NYSE Arca only, or both exchanges

## Setup Instructions

### Prerequisites

- Python 3.10 or higher (Debian/Ubuntu: `python3-venv` recommended; without it `run.sh` bootstraps pip via get-pip.py)

### Installation

The run scripts create the virtual environment and install/validate the dependencies themselves.
The venv lives next to the app: `.venv-linux` on Linux/macOS and `.venv-windows` on Windows.
The scripts only call pip when a requirement is missing or `requirements.txt` changed. A venv
that was moved or renamed is recreated automatically.

```bash
./run.sh --setup-only        # Linux/macOS: prepare the environment only
```

```bat
run.cmd --setup-only         :: Windows (uses ..\..\..\..\python\3.14\python.exe when present)
```

| Environment variable | Default | Purpose |
|----------------------|---------|---------|
| `YAHOODB_PYTHON` | `python3` (Windows: the shared Python 3.14, else `python`) | Interpreter used to create the venv |
| `YAHOODB_VENV` | `.venv-linux` / `.venv-windows` | Venv directory, e.g. a native-filesystem path under WSL |
| `YAHOODB_REQUIREMENTS` | `requirements.txt` | Requirements file |

Configure the service by editing `config.json`:
- Database file path
- Yahoo Finance parameters
- Web server settings (host, port)

## Usage Instructions

### Start the Web Application

```bash
./run.sh          # Linux/macOS
run.cmd           # Windows
```

The web dashboard will be available at: `http://localhost:5000`

### Sync NYSE & NYSE Arca Tickers

To download NYSE and NYSE Arca tickers and add new ones to the database with 10 years of historical data:

```bash
# Run the sync script (syncs both NYSE and NYSE Arca)
python sync_nyse.py

# Or use the run script
./run_nyse_sync.sh
```

#### Sync Options

```bash
# Dry run - see what would be synced without making changes
python sync_nyse.py --dry-run

# Limit the number of tickers to process per exchange
python sync_nyse.py --limit 100

# Sync only NYSE tickers (skip NYSE Arca)
python sync_nyse.py --nyse-only

# Sync only NYSE Arca tickers (skip NYSE) - great for ETFs like BITB
python sync_nyse.py --nysearca-only

# Adjust delay between API calls (default: 0.5 seconds)
python sync_nyse.py --delay 1.0

# Specify a different config file
python sync_nyse.py --config /path/to/config.json

# Combine options - sync only NYSE Arca with limit
python sync_nyse.py --nysearca-only --limit 50 --delay 0.5
```

#### Command Line Arguments

| Argument | Short | Default | Description |
|----------|-------|---------|-------------|
| `--limit` | `-l` | None | Maximum number of new tickers to process per exchange |
| `--delay` | `-d` | 0.5 | Delay in seconds between API calls |
| `--dry-run` | - | False | Preview changes without modifying database |
| `--nyse-only` | - | False | Only sync NYSE tickers (skip NYSE Arca) |
| `--nysearca-only` | - | False | Only sync NYSE Arca tickers (skip NYSE) |
| `--config` | `-c` | config.json | Path to configuration file |

### Configuration Options

Edit `config.json` to customize behavior:

```json
{
    "version": "0.0.0.6",
    "database": {
        "file_path": "./data/stocks.db"
    },
    "yahoo_finance": {
        "data_interval": "1d",
        "history_period": "10y"
    },
    "scheduling": {
        "enabled": true,
        "run_time": "06:00",
        "timezone": "America/New_York"
    },
    "files": {
        "nasdaq_tickers": "./data/nasdaq_tickers.json",
        "nyse_tickers": "./data/nyse_tickers.json",
        "nysearca_tickers": "./data/nysearca_tickers.json"
    },
    "web": {
        "host": "0.0.0.0",
        "port": 5000,
        "debug": false
    }
}
```

| Parameter | Description |
|-----------|-------------|
| `database.file_path` | Path to SQLite database file |
| `yahoo_finance.data_interval` | Data granularity (1d = daily) |
| `yahoo_finance.history_period` | Historical data period (10y = 10 years) |
| `scheduling.run_time` | Daily sync time (if scheduled) |
| `files.nasdaq_tickers` | Path to cached NASDAQ ticker list |
| `files.nyse_tickers` | Path to cached NYSE ticker list |
| `files.nysearca_tickers` | Path to cached NYSE Arca ticker list |
| `web.host` | Web server host (0.0.0.0 for all interfaces) |
| `web.port` | Web server port (default: 5000) |
| `web.debug` | Enable Flask debug mode |

## REST API

See [API.md](API.md) for complete API documentation.

## Connection Handling

### How It Works

In previous versions, every HTTP request and sync operation opened a new SQLite connection and closed it afterwards. In v0.0.0.6 the `DatabaseManager` keeps a **single persistent connection** that is reused across the entire application lifetime.

### Automatic Reconnection

Before every database operation the manager runs a lightweight health check (`SELECT 1`). If the connection is broken, it is transparently closed and reopened. This handles scenarios such as:

- The database file being temporarily locked by another process
- Operating-system-level I/O interruptions
- Unexpected connection loss during long-running sync jobs

### Retry Mechanism

All database methods are wrapped with a `@_with_retry` decorator:

- **Max retries:** 3 attempts per operation
- **Backoff:** Exponential — 0.5 s, 1 s, 2 s between attempts
- On each retry the connection is forcibly re-established before the operation is re-attempted

If all retries are exhausted, the original `sqlite3.Error` is raised so callers can handle it.

### Graceful Shutdown

- **Flask app (`main.py`):** An `atexit` handler calls `db.shutdown()` when the process exits.
- **Sync script (`sync_nyse.py`):** A `finally` block in `main()` ensures `db.shutdown()` is called even on errors or `KeyboardInterrupt`.

### Dependencies

The connection handling uses only the Python standard library (`sqlite3`, `threading`, `functools`, `time`) — no additional packages are required.

### Quick Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tickers` | Get all tickers |
| GET | `/api/ticker/{symbol}` | Get ticker details |
| GET | `/api/ticker/{symbol}/history` | Get historical data |
| GET | `/api/search?q=apple&limit=10` | Search tickers by symbol or company name |
| GET | `/api/ticker/{symbol}/splits` | Splits already taken care of, and the ticker's full split check |
| POST | `/api/ticker/{symbol}/check-splits[?force=1]` | Check a ticker for splits now and re-download its history if needed |
| POST | `/api/ticker` | Add new ticker |
| POST | `/api/sync/start` | Start synchronization |
| POST | `/api/sync/stop` | Stop synchronization |
| GET | `/api/sync/status` | Get sync status |

### Example Usage

```bash
# Get all tickers
curl http://localhost:5000/api/tickers

# Get ticker info
curl http://localhost:5000/api/ticker/AAPL

# Get last 30 days of history
curl http://localhost:5000/api/ticker/AAPL/history?days=30

# Get history by date range
curl "http://localhost:5000/api/ticker/AAPL/history?start_date=2025-01-01&end_date=2025-01-31"

# Add a new ticker
curl -X POST http://localhost:5000/api/ticker \
  -H "Content-Type: application/json" \
  -d '{"symbol": "TSLA"}'

# Start sync
curl -X POST http://localhost:5000/api/sync/start
```

## Project Structure

```
0.0.0.4/
├── main.py              # Flask application and service logic
├── database.py          # SQLite database operations
├── yahoo_finance.py     # Yahoo Finance API client (NASDAQ, NYSE & NYSE Arca support)
├── split_check.py       # Stock split detection and history re-download
├── tests/               # pytest tests (python -m pytest tests -q)
├── sync_nyse.py         # NYSE and NYSE Arca ticker synchronization script
├── config.json          # Application configuration
├── requirements.txt     # Python dependencies
├── run.sh / run.cmd     # Run the web application (creates/validates the venv)
├── run_nyse_sync.sh / .bat  # Run NYSE/NYSE Arca sync (same venv)
├── service.sh           # systemd daemon management (install/start/stop/...)
├── tools/
│   └── ensure_env.py    # Dependency validation used by the run scripts
├── README.md            # This file
├── API.md               # API documentation
├── templates/
│   └── index.html       # Web dashboard template
└── data/
    ├── stocks.db        # SQLite database (created on first run)
    ├── nasdaq_tickers.json  # Cached NASDAQ ticker list
    ├── nyse_tickers.json    # Cached NYSE ticker list
    └── nysearca_tickers.json # Cached NYSE Arca ticker list
```

## Database Schema

### Tickers Table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| symbol | TEXT | Stock ticker symbol (unique) |
| short_name | TEXT | Company short name |
| long_name | TEXT | Company full name |
| sector | TEXT | Business sector |
| industry | TEXT | Industry classification |
| market_cap | REAL | Market capitalization |
| currency | TEXT | Trading currency |
| exchange | TEXT | Stock exchange (NASDAQ, NYSE, NYSE Arca, etc.) |
| country | TEXT | Country of origin |
| website | TEXT | Company website |
| description | TEXT | Company description |
| employees | INTEGER | Number of employees |
| created_at | TIMESTAMP | Record creation timestamp |
| updated_at | TIMESTAMP | Record update timestamp |

### Historical Data Table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| ticker_symbol | TEXT | Stock ticker symbol |
| date | DATE | Trading date |
| open_price | REAL | Opening price |
| close_price | REAL | Closing price |
| high_price | REAL | Highest price |
| low_price | REAL | Lowest price |
| volume | INTEGER | Trading volume |
| adj_close | REAL | Adjusted close price |
| created_at | TIMESTAMP | Record creation timestamp |

### Stock splits (`split_events`, `split_checks`)

Yahoo's history is split-adjusted as of the moment it is downloaded. The daily update only
re-downloads the last month, so after a split the older stored rows stayed on the old price scale
and the chart jumped. For example, ETHA (1:3 on 2026-10-06) was stored at ⅓ of the price before
2026-09-22. Every Yahoo download also carries the split events, and `split_check.py` uses them:

| When | What happens |
|------|--------------|
| First update of a ticker after upgrading | One **full check over its whole stored life**: history is downloaded from the first stored day. If it contains a split, stored rows that differ from Yahoo's adjusted prices are replaced in one transaction. If nothing differs, the split is only marked `verified`. The ticker gets a row in `split_checks`, so this runs once per ticker |
| Daily update | A split in the downloaded window that isn't in `split_events` yet triggers a full re-download from the first stored day. If the update was more than 25 days ago, the window covers the whole gap |
| New ticker / manual add | Splits in the first download are registered as handled (`initial_download`). That data is already adjusted |
| `POST /api/ticker/{symbol}/check-splits` | The same full check on demand; `?force=1` re-downloads even without a new split |

`split_events` (ticker, split_date, ratio, action, handled_at) is the record that a split was taken
care of, so the next day's update does not fix it again. `action` is `redownloaded`, `verified`,
`before_history` or `initial_download`. When a re-download fails, the split stays unregistered
and is retried on the next update. Every re-download is also logged in `update_history` as
`split_redownload`.

## Dependencies

The project requires the following Python packages (see `requirements.txt`):

- `yfinance>=0.2.0` - Yahoo Finance API client
- `pandas>=2.0.0` - Data manipulation library
- `schedule>=1.2.0` - Job scheduling library
- `flask>=3.0.0` - Web framework
- `flask-cors>=4.0.0` - Cross-Origin Resource Sharing support

## Error Handling

The sync script includes comprehensive error handling:

- **Network errors** - Retries and graceful degradation with fallback tickers for both NYSE and NYSE Arca
- **API rate limiting** - Configurable delay between requests
- **Database errors** - Transaction rollback and detailed error logging
- **Invalid data** - Skips invalid symbols with warning logs
- **Interruption** - Graceful shutdown on Ctrl+C with progress saved
- **Exchange-specific failures** - One exchange failing doesn't prevent the other from syncing

## Logs

- `stock_service.log` - Main application log file
- `nyse_sync.log` - NYSE synchronization log file

Both logs contain timestamps, log levels, and detailed messages for debugging.

## License

MIT License
