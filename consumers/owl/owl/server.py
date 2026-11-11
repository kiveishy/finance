"""Owl HTTP server: REST API for the web UI plus static file hosting (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from finance_aggregator.core import (
    DataNotFoundError,
    FinanceError,
    HealthState,
    InvalidRequestError,
    ProviderError,
    ProviderUnavailableError,
    ServiceStatus,
    normalize_limit,
    normalize_symbol,
)

from . import __version__
from .charts import ChartRequest, ChartService
from .config import ServerConfig
from .errors import ConflictError, ResourceNotFoundError, ValidationError
from .market_data import MarketData
from .quotes import QuoteService
from .watchlist import WatchItem, WatchlistRepository

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
MAX_DRAIN_BYTES = 1024 * 1024
SETTING_KEYS = frozenset({"chart", "ui"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self' http: https:; frame-ancestors 'none'"
)


@dataclass
class Request:
    method: str
    path: str
    query: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class Response:
    status: HTTPStatus
    body: Any = None
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    def payload(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        return json.dumps(self.body, default=str).encode("utf-8")


def ok(body: Any, status: HTTPStatus = HTTPStatus.OK) -> Response:
    return Response(status, {"status": "success", "data": body})


def error(status: HTTPStatus, message: str) -> Response:
    return Response(status, {"status": "error", "error": message})


Handler = Callable[[Request], Response]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")
        self._routes.append((method, regex, handler))

    def resolve(self, method: str, path: str) -> tuple[Handler | None, dict[str, str], bool]:
        """Return (handler, params, path_exists)."""
        path_exists = False
        for route_method, regex, handler in self._routes:
            match = regex.match(path)
            if match:
                path_exists = True
                if route_method == method:
                    return handler, {k: unquote(v) for k, v in match.groupdict().items()}, True
        return None, {}, path_exists


class OwlApi:
    """Transport-independent REST API (unit-testable without sockets)."""

    def __init__(
        self,
        market: MarketData,
        watchlist: WatchlistRepository,
        charts: ChartService,
        *,
        status_ttl_seconds: float = 5.0,
        web_build: Callable[[], str] | None = None,
        quotes: QuoteService | None = None,
    ) -> None:
        self._market = market
        self._watchlist = watchlist
        self._charts = charts
        self._web_build = web_build
        self._quotes = quotes or QuoteService(market)
        self._status_ttl = status_ttl_seconds
        self._status_lock = threading.Lock()
        self._status_cache: tuple[float, ServiceStatus] | None = None
        self.router = Router()
        r = self.router
        r.add("GET", "/api/health", lambda req: ok({"version": __version__}))
        r.add("GET", "/api/status", self._status)
        r.add("GET", "/api/groups", lambda req: ok([g.to_dict() for g in self._watchlist.list_groups()]))
        r.add("POST", "/api/groups", self._create_group)
        r.add("PATCH", "/api/groups/{group_id}", self._rename_group)
        r.add("DELETE", "/api/groups/{group_id}", self._delete_group)
        r.add("POST", "/api/groups/{group_id}/items", self._add_item)
        r.add("DELETE", "/api/groups/{group_id}/items/{symbol}", self._remove_item)
        r.add("POST", "/api/groups/{group_id}/items/{symbol}/move", self._move_item)
        r.add("GET", "/api/quotes", self._get_quotes)
        r.add("GET", "/api/ticker/{symbol}", self._ticker)
        r.add("GET", "/api/search", self._search)
        r.add("GET", "/api/chart/{symbol}", self._chart)
        r.add("GET", "/api/settings/{key}", self._get_setting)
        r.add("PUT", "/api/settings/{key}", self._put_setting)

    def handle(self, request: Request) -> Response:
        handler, params, path_exists = self.router.resolve(request.method, request.path)
        if handler is None:
            if path_exists:
                return error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")
            return error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        request.params = params
        try:
            return handler(request)
        except (InvalidRequestError, ValidationError) as exc:
            return error(HTTPStatus.BAD_REQUEST, str(exc))
        except ResourceNotFoundError as exc:
            return error(HTTPStatus.NOT_FOUND, str(exc))
        except DataNotFoundError as exc:
            logger.info("%s %s -> not found: %s", request.method, request.path, exc)
            return error(HTTPStatus.NOT_FOUND, "Requested market data not found")
        except ConflictError as exc:
            return error(HTTPStatus.CONFLICT, str(exc))
        except ProviderUnavailableError as exc:
            logger.warning("%s %s -> data provider unavailable: %s", request.method, request.path, exc)
            return error(HTTPStatus.SERVICE_UNAVAILABLE, "Market data provider unavailable")
        except ProviderError as exc:
            logger.error("%s %s -> data provider error: %s", request.method, request.path, exc)
            return error(HTTPStatus.BAD_GATEWAY, "Market data provider error")
        except (FinanceError, Exception):
            logger.exception("%s %s failed", request.method, request.path)
            return error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")

    # ---- handlers ----------------------------------------------------------------------------

    def _status(self, req: Request) -> Response:
        status, checked_at = self.provider_status(force=req.query.get("refresh") == "1")
        return ok({
            "version": __version__,
            "web_build": self._web_build() if self._web_build else None,
            "aggregator": status.to_dict(),
            "checked_at": datetime.fromtimestamp(checked_at, timezone.utc).isoformat(timespec="seconds"),
        })

    def provider_status(self, *, force: bool = False) -> tuple[ServiceStatus, float]:
        """Aggregator health, cached briefly so many polling clients don't probe every provider."""
        with self._status_lock:  # also collapses concurrent probes into one
            now = time.time()
            cached = self._status_cache
            if cached and not force and now - cached[0] < self._status_ttl:
                return cached[1], cached[0]
            try:
                status = self._market.check_status()
            except Exception as exc:  # noqa: BLE001 - status must always answer
                logger.exception("Data provider status check failed")
                status = ServiceStatus("aggregator", HealthState.UNAVAILABLE, (), f"status check failed: {exc}")
            if cached is None or cached[1].state != status.state:
                log = logger.info if status.state is not HealthState.UNAVAILABLE else logger.warning
                log("Data provider state: %s (%s)", status.state.value, status.detail)
            self._status_cache = (now, status)
            return status, now

    def _create_group(self, req: Request) -> Response:
        group = self._watchlist.create_group(_field(req, "name"))
        return ok(group.to_dict(), HTTPStatus.CREATED)

    def _rename_group(self, req: Request) -> Response:
        return ok(self._watchlist.rename_group(_group_id(req), _field(req, "name")).to_dict())

    def _delete_group(self, req: Request) -> Response:
        self._watchlist.delete_group(_group_id(req))
        return ok(None)

    def _add_item(self, req: Request) -> Response:
        group_id = _group_id(req)
        self._watchlist.get_group(group_id)  # 404 before any provider round-trip
        symbol = normalize_symbol(_field(req, "symbol"))
        info = self._market.get_ticker_info(symbol)  # rejects unknown symbols
        group = self._watchlist.add_item(group_id, WatchItem(symbol, info.display_name))
        return ok(group.to_dict(), HTTPStatus.CREATED)

    def _remove_item(self, req: Request) -> Response:
        return ok(self._watchlist.remove_item(_group_id(req), req.params["symbol"]).to_dict())

    def _move_item(self, req: Request) -> Response:
        body = req.body if isinstance(req.body, dict) else {}
        to_group = body.get("to")
        if isinstance(to_group, bool) or not isinstance(to_group, int) or to_group <= 0:
            raise ValidationError("JSON body with integer field 'to' (target group id) required")
        before = body.get("before")
        if before is not None and not isinstance(before, str):
            raise ValidationError("'before' must be a symbol")
        self._watchlist.move_item(_group_id(req), req.params["symbol"], to_group,
                                  copy=bool(body.get("copy", False)), before=before or None)
        return ok([g.to_dict() for g in self._watchlist.list_groups()])

    def _get_quotes(self, req: Request) -> Response:
        symbols = [s for s in req.query.get("symbols", "").split(",") if s.strip()]
        if not symbols:
            raise ValidationError("Query parameter 'symbols' required (comma separated)")
        return ok(self._quotes.quotes(symbols))

    def _ticker(self, req: Request) -> Response:
        return ok(self._market.get_ticker_info(req.params["symbol"]).to_dict())

    def _search(self, req: Request) -> Response:
        matches = self._market.search_symbols(req.query.get("q", ""), normalize_limit(req.query.get("limit")))
        membership: dict[str, list[str]] = {}
        for group in self._watchlist.list_groups():
            for symbol in group.symbols:
                membership.setdefault(symbol, []).append(group.name)
        return ok([{**m.to_dict(), "groups": membership.get(m.symbol, [])} for m in matches])

    def _chart(self, req: Request) -> Response:
        chart_request = ChartRequest.parse(
            req.params["symbol"], req.query.get("range"), req.query.get("interval"), req.query.get("indicators")
        )
        return ok(self._charts.build(chart_request))

    def _get_setting(self, req: Request) -> Response:
        return ok(self._watchlist.get_setting(_setting_key(req)))

    def _put_setting(self, req: Request) -> Response:
        key = _setting_key(req)
        if not isinstance(req.body, dict):
            raise ValidationError("Setting value must be a JSON object")
        self._watchlist.set_setting(key, req.body)
        return ok(req.body)


def _field(req: Request, name: str) -> str:
    if not isinstance(req.body, dict) or not isinstance(req.body.get(name), str):
        raise ValidationError(f"JSON body with string field {name!r} required")
    return req.body[name]


def _group_id(req: Request) -> int:
    raw = req.params.get("group_id", "")
    if not raw.isdigit():
        raise ValidationError("Group id must be a positive integer")
    return int(raw)


def _setting_key(req: Request) -> str:
    key = req.params["key"]
    if key not in SETTING_KEYS:
        raise ResourceNotFoundError(f"Unknown setting {key!r}")
    return key


class StaticFiles:
    """Serves the web UI from a single root directory; never outside it."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def build_id(self) -> str:
        """Fingerprint of the web files; changes when the UI is updated."""
        digest = hashlib.sha1()
        for path in sorted(p for p in self._root.rglob("*") if p.is_file()):
            stat = path.stat()
            digest.update(f"{path.relative_to(self._root).as_posix()}:{stat.st_mtime_ns}:{stat.st_size};".encode())
        return digest.hexdigest()[:12]

    def resolve(self, url_path: str) -> Path | None:
        relative = unquote(url_path).lstrip("/") or "index.html"
        target = (self._root / relative).resolve()
        if not target.is_relative_to(self._root):
            return None
        if target.is_dir():
            target = target / "index.html"
        return target if target.is_file() else None

    def serve(self, url_path: str, if_none_match: str | None = None) -> Response:
        target = self.resolve(url_path)
        if target is None:
            return error(HTTPStatus.NOT_FOUND, "Not found")
        stat = target.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        cache = "no-cache" if target.suffix in (".html", ".js", ".css", ".webmanifest") else "max-age=86400"
        headers = {"Cache-Control": cache, "ETag": etag}
        if if_none_match == etag:
            return Response(HTTPStatus.NOT_MODIFIED, b"", "text/plain", headers)
        content_type = _CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0]
        return Response(HTTPStatus.OK, target.read_bytes(), content_type or "application/octet-stream", headers)


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ttf": "font/ttf",
    ".ico": "image/x-icon",
}


class OwlHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port silently; there it isn't needed anyway.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, config: ServerConfig, api: OwlApi, static: StaticFiles) -> None:
        self.config = config
        self.api = api
        self.static = static
        super().__init__((config.host, config.port), _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    server: OwlHttpServer
    server_version = f"owl/{__version__}"

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(Response(HTTPStatus.NO_CONTENT, b"", "text/plain"), preflight=True)

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        if not path.startswith("/api/"):
            if method == "GET":
                self._send(self.server.static.serve(path, self.headers.get("If-None-Match")))
            else:
                self._send(error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed"))
            return
        rejection = self._authorize(method, path)
        if rejection is not None:
            self._send(rejection)
            return
        try:
            body = self._read_json() if method in _WRITE_METHODS else None
        except ValidationError as exc:
            self._send(error(HTTPStatus.BAD_REQUEST, str(exc)))
            return
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        request = Request(method, path, query, dict(self.headers.items()), body)
        self._send(self.server.api.handle(request))

    def _authorize(self, method: str, path: str) -> Response | None:
        api_key = self.server.config.api_key
        if api_key and path != "/api/health":
            supplied = self.headers.get("X-API-Key", "")
            if not hmac.compare_digest(supplied.encode(), api_key.encode()):
                return error(HTTPStatus.UNAUTHORIZED, "Missing or invalid API key")
        # Forces a CORS preflight for cross-site writes (CSRF protection without cookies).
        if method in _WRITE_METHODS and not self.headers.get("Content-Type", "").startswith("application/json"):
            return error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
        return None

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ValidationError("Invalid Content-Length") from exc
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            if length <= MAX_DRAIN_BYTES:
                # Consume the body so closing the socket doesn't reset it before the client reads the error.
                self.rfile.read(length)
            raise ValidationError(f"Request body exceeds {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else None
        except ValueError as exc:
            raise ValidationError("Request body is not valid JSON") from exc

    def _cors_headers(self, preflight: bool) -> dict[str, str]:
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin or origin not in self.server.config.cors_origins:
            return {}
        headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        if preflight:
            headers.update(
                {
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
                    "Access-Control-Max-Age": "600",
                }
            )
        return headers

    def _send(self, response: Response, *, preflight: bool = False) -> None:
        payload = response.payload()
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(payload)))
        headers = {**_SECURITY_HEADERS, **response.headers, **self._cors_headers(preflight)}
        if response.content_type.startswith("text/html"):
            headers["Content-Security-Policy"] = _CSP
        headers.setdefault("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("%s %s", self.address_string(), format % args)


class OwlServer:
    def __init__(self, config: ServerConfig, api: OwlApi, static: StaticFiles) -> None:
        self._httpd = OwlHttpServer(config, api, static)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
        return f"http://{shown}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="owl-http", daemon=True)
        self._thread.start()
        logger.info("Owl listening on %s (web root %s)", self.url, self._httpd.static.root)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "OwlServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
