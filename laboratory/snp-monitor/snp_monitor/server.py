"""HTTP server: JSON API plus the static web UI (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import sys
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import __version__
from .config import ServerConfig
from .monitor import SnpMonitor

logger = logging.getLogger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


@dataclass
class Response:
    status: HTTPStatus
    body: Any = None
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    def payload(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        return json.dumps(self.body, separators=(",", ":")).encode("utf-8")


def ok(body: Any, status: HTTPStatus = HTTPStatus.OK) -> Response:
    return Response(status, {"status": "success", "data": body})


def error(status: HTTPStatus, message: str) -> Response:
    return Response(status, {"status": "error", "error": message})


class Api:
    """Transport-independent API (unit-testable without sockets)."""

    def __init__(self, monitor: SnpMonitor, *, web_build: Any = None) -> None:
        self._monitor = monitor
        self._web_build = web_build

    def handle(self, method: str, path: str) -> Response:
        try:
            if path == "/api/health" and method == "GET":
                return ok({"version": __version__, "app": "snp-monitor",
                           "web_build": self._web_build() if self._web_build else None})
            if path == "/api/snapshot" and method == "GET":
                return ok(self._monitor.snapshot())
            if path == "/api/refresh" and method == "POST":
                if not self._monitor.request_refresh():
                    return error(HTTPStatus.TOO_MANY_REQUESTS, "Refreshed moments ago - try again in a few seconds")
                return ok({"requested": True}, HTTPStatus.ACCEPTED)
            if path in ("/api/health", "/api/snapshot", "/api/refresh"):
                return error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed")
            return error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
        except RuntimeError as exc:  # data not available (offline, still loading)
            return error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except Exception:
            logger.exception("%s %s failed", method, path)
            return error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")


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
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        if if_none_match == etag:
            return Response(HTTPStatus.NOT_MODIFIED, b"", "text/plain", headers)
        content_type = _CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0]
        return Response(HTTPStatus.OK, target.read_bytes(), content_type or "application/octet-stream", headers)


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port silently.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, config: ServerConfig, api: Api, static: StaticFiles) -> None:
        self.api = api
        self.static = static
        super().__init__((config.host, config.port), _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    server: _HttpServer
    server_version = f"snp-monitor/{__version__}"

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._send(error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed"))

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if not path.startswith("/api/"):
            if method == "GET":
                self._send(self.server.static.serve(path, self.headers.get("If-None-Match")))
            else:
                self._send(error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed"))
            return
        if method == "POST":
            # No body is used; JSON content type forces a CORS preflight, so other sites can't trigger it.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.close_connection = True
                self._send(error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json"))
                return
            length = self.headers.get("Content-Length", "0")
            if length.isdigit() and 0 < int(length) <= 64 * 1024:
                self.rfile.read(int(length))
            elif length not in ("", "0"):
                self.close_connection = True
        self._send(self.server.api.handle(method, path))

    def _send(self, response: Response) -> None:
        payload = response.payload()
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(payload)))
        headers = {**_SECURITY_HEADERS, **response.headers}
        if response.content_type.startswith("text/html"):
            headers["Content-Security-Policy"] = _CSP
        headers.setdefault("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("%s %s", self.address_string(), format % args)


class Server:
    def __init__(self, config: ServerConfig, api: Api, static: StaticFiles) -> None:
        self._httpd = _HttpServer(config, api, static)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
        return f"http://{shown}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="snp-http", daemon=True)
        self._thread.start()
        logger.info("snp-monitor listening on %s (web root %s)", self.url, self._httpd.static.root)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "Server":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
