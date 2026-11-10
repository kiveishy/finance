"""
YahooDB API Client
==================
A Python client class that wraps all REST endpoints exposed by the
Yahoo-DB Flask application (main.py).

Usage example:
    from yahoodb-api import YahooDB          # or: import importlib; ...
    # -- or using importlib for the hyphenated filename --
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "yahoodb_api",
        pathlib.Path(__file__).parent / "yahoodb-api.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    YahooDB = mod.YahooDB

    db = YahooDB(host="localhost", port=5000)
    info = db.get_ticker_info("TSLA")
    print(info)
"""

import requests
from typing import Optional, Dict, Any, List


class YahooDBError(Exception):
    """Raised when the Yahoo-DB API returns an error response."""

    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class YahooDB:
    """
    Client for the Yahoo-DB REST API.

    All methods raise ``YahooDBError`` on HTTP errors or when the server
    returns ``"status": "error"`` in the JSON body.

    Args:
        host:    Hostname or IP of the Yahoo-DB server. Default ``localhost``.
        port:    Port the Flask app is listening on. Default ``5000``.
        timeout: Seconds to wait for a response. Default ``30``.
    """

    def __init__(self, host: str = "localhost", port: int = 5000, timeout: int = 30):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise YahooDBError(f"GET {url} failed: {exc}") from exc
        self._raise_for_error(resp)
        return resp.json()

    def _post(self, path: str, json: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.post(url, json=json, timeout=self.timeout)
        except requests.RequestException as exc:
            raise YahooDBError(f"POST {url} failed: {exc}") from exc
        self._raise_for_error(resp)
        return resp.json()

    def _raise_for_error(self, resp: requests.Response) -> None:
        """Raise YahooDBError if the response indicates failure."""
        if resp.status_code >= 400:
            try:
                body = resp.json()
                msg = body.get("error", resp.text)
            except Exception:
                msg = resp.text
            raise YahooDBError(msg, status_code=resp.status_code, response=body if resp.text else None)
        # Also treat application-level errors
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("status") == "error":
                raise YahooDBError(
                    body.get("error", "Unknown error from server"),
                    status_code=resp.status_code,
                    response=body,
                )
        except YahooDBError:
            raise
        except Exception:
            pass  # Non-JSON or non-dict responses are fine

    # ------------------------------------------------------------------
    # Service status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """
        Get current service status.

        Returns dict with keys: sync_running, sync_status, last_sync_time,
        ticker_count, progress.

        Endpoint: GET /status
        """
        return self._get("/status")

    # ------------------------------------------------------------------
    # Sync control
    # ------------------------------------------------------------------

    def start_sync(self) -> dict:
        """
        Start the background database synchronization.

        Endpoint: POST /api/sync/start
        """
        return self._post("/api/sync/start")

    def stop_sync(self) -> dict:
        """
        Stop an ongoing background synchronization.

        Endpoint: POST /api/sync/stop
        """
        return self._post("/api/sync/stop")

    def get_sync_status(self) -> dict:
        """
        Get the current synchronization status.

        Endpoint: GET /api/sync/status
        """
        return self._get("/api/sync/status")

    # ------------------------------------------------------------------
    # Ticker management
    # ------------------------------------------------------------------

    def get_all_tickers(self) -> List[str]:
        """
        Return the list of all ticker symbols stored in the database.

        Endpoint: GET /api/tickers

        Returns:
            List of ticker symbol strings (e.g. ['AAPL', 'TSLA', ...]).
        """
        data = self._get("/api/tickers")
        return data.get("tickers", [])

    def add_ticker(self, symbol: str) -> dict:
        """
        Add a new ticker to the database and download its historical data.

        Args:
            symbol: Ticker symbol, e.g. ``"AAPL"``.

        Endpoint: POST /api/ticker  body: {"symbol": "<symbol>"}

        Returns:
            Dict with keys: message, status, records_added.
        """
        return self._post("/api/ticker", json={"symbol": symbol.upper().strip()})

    def get_ticker_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get detailed metadata for a ticker (name, sector, market cap, …).

        Args:
            symbol: Ticker symbol, e.g. ``"TSLA"``.

        Endpoint: GET /api/ticker/<symbol>

        Returns:
            Dict with ticker fields: symbol, short_name, long_name, sector,
            industry, market_cap, currency, exchange, quote_type, country,
            website, description, employees, created_at, updated_at.
        """
        data = self._get(f"/api/ticker/{symbol.upper().strip()}")
        return data.get("data", data)

    def get_ticker_history(
        self,
        symbol: str,
        *,
        days: Optional[int] = None,
        months: Optional[int] = None,
        years: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get historical OHLCV price data for a ticker.

        Pass at most one of ``days`` / ``months`` / ``years`` **or** a
        ``start_date`` + ``end_date`` pair.  When nothing is passed the
        server returns the last 30 days.

        Args:
            symbol:     Ticker symbol, e.g. ``"TSLA"``.
            days:       Number of calendar days to look back.
            months:     Number of months to look back (server uses 30 d/month).
            years:      Number of years to look back (server uses 365 d/year).
            start_date: Start date string ``"YYYY-MM-DD"``.
            end_date:   End date string ``"YYYY-MM-DD"``.

        Endpoint: GET /api/ticker/<symbol>/history

        Returns:
            List of dicts with keys: date, open, close, high, low, volume,
            adj_close — sorted ascending by date.
        """
        params: Dict[str, Any] = {}
        if days is not None:
            params["days"] = days
        elif months is not None:
            params["months"] = months
        elif years is not None:
            params["years"] = years
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        data = self._get(
            f"/api/ticker/{symbol.upper().strip()}/history",
            params=params or None,
        )
        return data.get("data", [])
