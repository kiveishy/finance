"""Read-only JSON HTTP API exposing a StockDataSource (used by the `serve` command / daemon).

Endpoints (mirroring the yahoo-db API shape):
    GET /api/health
    GET /api/status
    GET /api/ticker/<symbol>
    GET /api/ticker/<symbol>/history?days=N|months=N|years=N|start_date=YYYY-MM-DD[&end_date=YYYY-MM-DD]
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from .. import __version__
from ..core.errors import (
    DataNotFoundError,
    FinanceError,
    InvalidRequestError,
    ProviderError,
    ProviderUnavailableError,
)
from ..core.interfaces import FinanceService, StockDataSource
from ..core.models import DateRange, normalize_symbol

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(r"^/api/ticker/(?P<symbol>[^/]+)$")
_HISTORY_RE = re.compile(r"^/api/ticker/(?P<symbol>[^/]+)/history$")


@dataclass(frozen=True)
class ApiResponse:
    status: HTTPStatus
    body: Mapping[str, Any]


class ApiApplication:
    """Transport-independent request handling (unit-testable without sockets)."""

    def __init__(self, source: StockDataSource, service: FinanceService, api_key: str | None = None) -> None:
        self._source = source
        self._service = service
        self._api_key = api_key

    def handle_get(self, raw_path: str, headers: Mapping[str, str]) -> ApiResponse:
        url = urlsplit(raw_path)
        path = url.path.rstrip("/") or "/"
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}

        if path == "/api/health":
            return ApiResponse(HTTPStatus.OK, {"status": "success", "version": __version__})
        if self._api_key and not hmac.compare_digest(
            headers.get("X-API-Key", "").encode(), self._api_key.encode()
        ):
            return _error(HTTPStatus.UNAUTHORIZED, "Missing or invalid API key")
        try:
            if path == "/api/status":
                return ApiResponse(HTTPStatus.OK, {"status": "success", "data": self._service.check_status().to_dict()})
            if match := _HISTORY_RE.match(path):
                return self._history(unquote(match["symbol"]), query)
            if match := _TICKER_RE.match(path):
                return self._ticker(unquote(match["symbol"]))
            return _error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        except InvalidRequestError as exc:
            return _error(HTTPStatus.BAD_REQUEST, str(exc))
        except DataNotFoundError as exc:
            logger.info("GET %s -> not found: %s", path, exc)
            return _error(HTTPStatus.NOT_FOUND, "Requested data not found", exc.attempts)
        except ProviderUnavailableError as exc:
            logger.warning("GET %s -> unavailable: %s", path, exc)
            return _error(HTTPStatus.SERVICE_UNAVAILABLE, "No data provider could serve the request", exc.attempts)
        except ProviderError as exc:
            logger.error("GET %s -> provider error: %s", path, exc)
            return _error(HTTPStatus.BAD_GATEWAY, "Data provider error", exc.attempts)
        except FinanceError:
            logger.exception("GET %s failed", path)
            return _error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")
        except Exception:
            logger.exception("GET %s failed unexpectedly", path)
            return _error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")

    def _ticker(self, raw_symbol: str) -> ApiResponse:
        info = self._source.get_ticker_info(normalize_symbol(raw_symbol))
        return ApiResponse(HTTPStatus.OK, {"status": "success", "source": info.source, "data": info.to_dict()})

    def _history(self, raw_symbol: str, query: Mapping[str, str]) -> ApiResponse:
        date_range = DateRange.from_options(
            days=_query_int(query, "days"),
            months=_query_int(query, "months"),
            years=_query_int(query, "years"),
            start=query.get("start_date"),
            end=query.get("end_date"),
        )
        history = self._source.get_price_history(normalize_symbol(raw_symbol), date_range)
        return ApiResponse(HTTPStatus.OK, {"status": "success", **history.to_dict()})


def _query_int(query: Mapping[str, str], key: str) -> int | None:
    if key not in query:
        return None
    try:
        return int(query[key])
    except ValueError as exc:
        raise InvalidRequestError(f"Query parameter {key!r} must be an integer") from exc


def _error(status: HTTPStatus, message: str, attempts: Any = ()) -> ApiResponse:
    body: dict[str, Any] = {"status": "error", "error": message}
    if attempts:
        # Provider names/outcomes only; details (paths, hosts) stay in the server log.
        body["attempts"] = [{"provider": a.provider, "outcome": a.outcome.value} for a in attempts]
    return ApiResponse(status, body)


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = f"finance-aggregator/{__version__}"
    server: "_ApiHttpServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        response = self.server.application.handle_get(self.path, self.headers)
        payload = json.dumps(response.body, default=str).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        logger.info("%s %s", self.address_string(), format % args)


class _ApiHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], application: ApiApplication) -> None:
        super().__init__(address, _RequestHandler)
        self.application = application


class ApiServer:
    """Owns the listening socket and its serving thread."""

    def __init__(
        self,
        source: StockDataSource,
        service: FinanceService,
        *,
        host: str = "127.0.0.1",
        port: int = 5100,
        api_key: str | None = None,
    ) -> None:
        self._httpd = _ApiHttpServer((host, port), ApiApplication(source, service, api_key))
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    @property
    def url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="api-server", daemon=True)
        self._thread.start()
        logger.info("API server listening on %s", self.url)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("API server stopped")

    def __enter__(self) -> "ApiServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
