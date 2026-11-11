# Stock Data Service API Documentation v0.0.0.3

This document describes the RESTful API endpoints available in the Stock Data Service.

## Base URL

```
http://localhost:5000
```

## Response Format

All API responses are in JSON format with a consistent structure:

**Success Response:**
```json
{
    "status": "success",
    "data": { ... }
}
```

**Error Response:**
```json
{
    "status": "error",
    "error": "Error message description"
}
```

---

## Endpoints

### 1. Get All Tickers

Get a list of all tickers stored in the database.

**Endpoint:** `GET /api/tickers`

**Response:**
```json
{
    "status": "success",
    "count": 150,
    "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", ...]
}
```

**Example:**
```bash
curl http://localhost:5000/api/tickers
```

---

### 2. Get Ticker Information

Get detailed information about a specific ticker.

**Endpoint:** `GET /api/ticker/{symbol}`

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| symbol | string | Stock ticker symbol (e.g., AAPL) |

**Response:**
```json
{
    "status": "success",
    "data": {
        "symbol": "AAPL",
        "short_name": "Apple Inc.",
        "long_name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "market_cap": 3000000000000,
        "currency": "USD",
        "exchange": "NASDAQ",
        "quote_type": "EQUITY",
        "country": "United States",
        "website": "https://www.apple.com",
        "description": "Apple Inc. designs, manufactures...",
        "employees": 164000,
        "created_at": "2025-01-15T10:30:00",
        "updated_at": "2025-01-31T08:00:00"
    }
}
```

**Error Response (404):**
```json
{
    "status": "error",
    "error": "Ticker INVALID not found"
}
```

**Example:**
```bash
curl http://localhost:5000/api/ticker/AAPL
```

---

### 3. Get Historical Stock Prices

Get historical stock price data for a ticker. Supports multiple query methods.

**Endpoint:** `GET /api/ticker/{symbol}/history`

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| symbol | string | Stock ticker symbol (e.g., AAPL) |

**Query Parameters (choose one method):**

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| days | integer | Number of days from today | `?days=30` |
| months | integer | Number of months from today | `?months=6` |
| years | integer | Number of years from today | `?years=2` |
| start_date | string | Start date (YYYY-MM-DD) | `?start_date=2025-01-01` |
| end_date | string | End date (YYYY-MM-DD) | `?end_date=2025-01-31` |

**Note:** If no parameters are provided, defaults to last 30 days.

**Response:**
```json
{
    "status": "success",
    "symbol": "AAPL",
    "start_date": "2025-01-01",
    "end_date": "2025-01-31",
    "count": 21,
    "data": [
        {
            "date": "2025-01-02",
            "open": 185.50,
            "close": 187.25,
            "high": 188.00,
            "low": 184.75,
            "volume": 45678900,
            "adj_close": 187.25
        },
        {
            "date": "2025-01-03",
            "open": 187.00,
            "close": 189.50,
            "high": 190.00,
            "low": 186.50,
            "volume": 52341200,
            "adj_close": 189.50
        }
    ]
}
```

**Examples:**

```bash
# Get last 30 days (default)
curl http://localhost:5000/api/ticker/AAPL/history

# Get last 90 days
curl http://localhost:5000/api/ticker/AAPL/history?days=90

# Get last 6 months
curl http://localhost:5000/api/ticker/AAPL/history?months=6

# Get last 2 years
curl http://localhost:5000/api/ticker/AAPL/history?years=2

# Get specific date range
curl "http://localhost:5000/api/ticker/AAPL/history?start_date=2025-01-01&end_date=2025-01-31"
```

---

### 4. Add New Ticker

Add a new ticker to the database. Historical data is automatically downloaded.

**Endpoint:** `POST /api/ticker`

**Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
    "symbol": "TSLA"
}
```

**Success Response:**
```json
{
    "status": "success",
    "message": "Ticker TSLA added successfully",
    "records_added": 2520
}
```

**Error Response (ticker exists):**
```json
{
    "status": "error",
    "error": "Ticker TSLA already exists"
}
```

**Example:**
```bash
curl -X POST http://localhost:5000/api/ticker \
  -H "Content-Type: application/json" \
  -d '{"symbol": "TSLA"}'
```

---

### 5. Start Synchronization

Start the database synchronization process with Yahoo Finance.

**Endpoint:** `POST /api/sync/start`

**Response:**
```json
{
    "status": "success",
    "message": "Sync started"
}
```

**Error Response (already running):**
```json
{
    "status": "error",
    "error": "Sync already running"
}
```

**Example:**
```bash
curl -X POST http://localhost:5000/api/sync/start
```

---

### 6. Stop Synchronization

Stop the currently running synchronization process.

**Endpoint:** `POST /api/sync/stop`

**Response:**
```json
{
    "status": "success",
    "message": "Sync stop requested"
}
```

**Error Response (not running):**
```json
{
    "status": "error",
    "error": "No sync is running"
}
```

**Example:**
```bash
curl -X POST http://localhost:5000/api/sync/stop
```

---

### 7. Get Synchronization Status

Get the current synchronization status and progress.

**Endpoint:** `GET /api/sync/status`

**Response:**
```json
{
    "sync_running": true,
    "sync_status": "running",
    "last_sync_time": "2025-01-31T08:00:00.123456",
    "ticker_count": 150,
    "progress": {
        "current": 45,
        "total": 150,
        "current_ticker": "GOOGL"
    }
}
```

**Status Values:**
| Status | Description |
|--------|-------------|
| `idle` | No sync is running |
| `running` | Sync is in progress |
| `stopping` | Stop requested, finishing current ticker |
| `completed` | Last sync completed successfully |
| `error: ...` | Last sync failed with error |

**Example:**
```bash
curl http://localhost:5000/api/sync/status
```

---

### 8. Search Tickers

Search tickers by symbol or company name (case-insensitive). Results are ranked: exact symbol,
symbol prefix, name prefix, word prefix in the name, then substring matches.

**Endpoint:** `GET /api/search`

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| q | string | Search text, 1-64 characters (e.g. `AAP`, `apple`) |
| limit | integer | Maximum results, 1-50 (default 10) |

**Response:**
```json
{
    "status": "success",
    "query": "apple",
    "count": 2,
    "data": [
        {"symbol": "AAPL", "short_name": "Apple Inc.", "long_name": "Apple Inc.", "exchange": "NMS", "quote_type": "EQUITY", "score": 75},
        {"symbol": "APLE", "short_name": "Apple Hospitality REIT, Inc.", "long_name": "Apple Hospitality REIT, Inc.", "exchange": "NYQ", "quote_type": "EQUITY", "score": 75}
    ]
}
```

**Error Response (400):** missing/too long `q` or `limit` out of range.

**Example:**
```bash
curl "http://localhost:5000/api/search?q=apple&limit=5"
```

---

### 9. Stock Splits of a Ticker

**Endpoint:** `GET /api/ticker/{symbol}/splits`

Splits already taken care of, and the result of the ticker's full split check (`null` until the
first update after upgrading).

**Response:**
```json
{
    "status": "success",
    "symbol": "ETHA",
    "check": {"result": "redownloaded", "mismatched_bars": 542, "checked_at": "2026-11-10 21:01:41"},
    "data": [{"split_date": "2026-10-06", "ratio": 0.3333, "action": "redownloaded", "handled_at": "2026-11-10 21:01:41"}]
}
```

### 10. Check a Ticker for Splits Now

**Endpoint:** `POST /api/ticker/{symbol}/check-splits`

Downloads the ticker's history from its first stored day. If it contains a split that is not
registered yet and the stored rows differ, the stored history is replaced (one transaction).
`?force=1` re-downloads even without a new split.

**Response:**
```json
{"status": "success", "symbol": "ETHA", "action": "redownloaded", "records": 579, "mismatched_bars": 542,
 "splits": [{"date": "2026-10-06", "ratio": 0.3333}]}
```

`action` is `redownloaded`, `verified` (split already reflected in the stored data), `updated`
(no new split) or `no_history`.

**Error Responses:** 404 unknown ticker, 503 Yahoo Finance history unavailable.

```bash
curl -X POST http://localhost:5000/api/ticker/ETHA/check-splits
```

---

### 11. Daily Update Schedule

**Endpoint:** `GET /api/schedule`

The schedule, the next run (UTC), and the last scheduled run.

```json
{
    "status": "success",
    "data": {
        "enabled": true,
        "run_time": "23:30",
        "timezone": "Asia/Jerusalem",
        "catch_up": true,
        "next_run": "2026-11-11T21:30:00+00:00",
        "next_run_is_catch_up": false,
        "running_since": null,
        "last_run": {"scheduled_for": "2026-11-10T21:30:00+00:00", "started_at": "...", "finished_at": "...", "result": "completed"},
        "server_time": "2026-11-11T12:00:00+00:00"
    }
}
```

**Endpoint:** `PUT /api/schedule` (`Content-Type: application/json`)

Change the schedule; it applies at once and is saved in `config.json`. All fields are optional:

```json
{"enabled": true, "run_time": "23:30", "timezone": "Asia/Jerusalem", "catch_up": true}
```

`timezone` is an IANA name or `"local"` (the server's clock). **Error Responses:** 400 invalid value
(e.g. `run_time` not `HH:MM`, unknown time zone), 415 not JSON, 500 `config.json` not writable.

```bash
curl -X PUT http://localhost:5000/api/schedule -H "Content-Type: application/json" -d '{"run_time": "23:30"}'
```

---

## HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request (invalid parameters) |
| 404 | Not Found (ticker not in database) |
| 500 | Internal Server Error |

---

## Rate Limiting

The API does not currently implement rate limiting, but please be considerate of server resources. The Yahoo Finance API has its own rate limits that may affect data retrieval.

---

## CORS

Cross-Origin Resource Sharing (CORS) allows web applications on other domains to **read** the API (GET).
Requests with a JSON body (adding a ticker, changing the schedule) need a CORS preflight, which other origins
don't pass, so other web sites can't send them in the background. Scripts and the finance aggregator are not
affected (CORS only applies to browsers).

---

## Usage Examples

### Python

```python
import requests

BASE_URL = "http://localhost:5000"

# Get all tickers
response = requests.get(f"{BASE_URL}/api/tickers")
tickers = response.json()["tickers"]

# Get ticker info
response = requests.get(f"{BASE_URL}/api/ticker/AAPL")
info = response.json()["data"]

# Get historical data
response = requests.get(f"{BASE_URL}/api/ticker/AAPL/history", params={"days": 30})
history = response.json()["data"]

# Add new ticker
response = requests.post(
    f"{BASE_URL}/api/ticker",
    json={"symbol": "NVDA"}
)
result = response.json()
```

### JavaScript

```javascript
const BASE_URL = 'http://localhost:5000';

// Get all tickers
fetch(`${BASE_URL}/api/tickers`)
    .then(response => response.json())
    .then(data => console.log(data.tickers));

// Get ticker info
fetch(`${BASE_URL}/api/ticker/AAPL`)
    .then(response => response.json())
    .then(data => console.log(data.data));

// Get historical data
fetch(`${BASE_URL}/api/ticker/AAPL/history?days=30`)
    .then(response => response.json())
    .then(data => console.log(data.data));

// Add new ticker
fetch(`${BASE_URL}/api/ticker`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol: 'NVDA' })
})
    .then(response => response.json())
    .then(data => console.log(data));
```

### cURL

```bash
# Complete workflow example

# 1. Check status
curl http://localhost:5000/api/sync/status

# 2. Add a new ticker
curl -X POST http://localhost:5000/api/ticker \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AMZN"}'

# 3. Get ticker info
curl http://localhost:5000/api/ticker/AMZN

# 4. Get last week of prices
curl http://localhost:5000/api/ticker/AMZN/history?days=7

# 5. Start full sync
curl -X POST http://localhost:5000/api/sync/start

# 6. Monitor progress
curl http://localhost:5000/api/sync/status

# 7. Stop sync if needed
curl -X POST http://localhost:5000/api/sync/stop
```
