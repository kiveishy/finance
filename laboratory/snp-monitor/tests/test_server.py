import http.client
import json
from pathlib import Path

import pytest

from snp_monitor.config import AppConfig, ConfigurationError, ServerConfig
from snp_monitor.server import Api, Server, StaticFiles

from test_monitor import FakeQuotes, StaticProvider
from snp_monitor.monitor import SnpMonitor


@pytest.fixture
def web_root(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    return root


@pytest.fixture
def server(web_root: Path):
    monitor = SnpMonitor(StaticProvider(), FakeQuotes(), clock=lambda: 1000.0)
    monitor.refresh()
    static = StaticFiles(web_root)
    with Server(ServerConfig(port=0), Api(monitor, web_build=static.build_id), static) as running:
        yield running


def _request(server: Server, method: str, path: str, headers=None, body=None):
    host, port = server.url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_health_identifies_the_app(server):
    response, data = _request(server, "GET", "/api/health")
    body = json.loads(data)
    assert response.status == 200 and body["data"]["app"] == "snp-monitor"
    assert body["data"]["web_build"]


def test_snapshot(server):
    response, data = _request(server, "GET", "/api/snapshot")
    body = json.loads(data)["data"]
    assert response.status == 200
    assert len(body["stocks"]) == 5 and body["refresh_seconds"] == 60.0
    assert response.getheader("Cache-Control") == "no-store"


def test_refresh_requires_json_and_is_rate_limited(server):
    response, _ = _request(server, "POST", "/api/refresh")
    assert response.status == 415
    response, _ = _request(server, "POST", "/api/refresh", {"Content-Type": "application/json"}, "{}")
    assert response.status == 429  # the fixture refreshed moments ago (fixed clock)


def test_static_files_and_security_headers(server):
    response, data = _request(server, "GET", "/")
    assert response.status == 200 and data == b"<html>hi</html>"
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    assert response.getheader("X-Content-Type-Options") == "nosniff"


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/..%5Csecret.txt"])
def test_static_files_never_leave_the_web_root(server, path):
    response, data = _request(server, "GET", path)
    assert response.status == 404 and b"secret" not in data


def test_unknown_api_and_wrong_method(server):
    assert _request(server, "GET", "/api/nope")[0].status == 404
    assert _request(server, "GET", "/api/refresh")[0].status == 405


def test_config_defaults_and_validation(tmp_path: Path):
    config = AppConfig.from_dict({}, base_dir=tmp_path)
    assert config.server.port == 8720 and config.browser.chrome == "local"
    assert config.data.cache_dir == (tmp_path / "data").resolve()
    assert config.browser.chrome_dir == (tmp_path / "../../common/chrome").resolve()
    with pytest.raises(ConfigurationError):
        AppConfig.from_dict({"browser": {"chrome": "firefox"}}, base_dir=tmp_path)
    with pytest.raises(ConfigurationError):
        AppConfig.from_dict({"data": {"quote_refresh_seconds": 1}}, base_dir=tmp_path)
    with pytest.raises(ConfigurationError):
        AppConfig.from_dict({"data": {"constituent_sources": ["nasdaq"]}}, base_dir=tmp_path)


def test_shipped_config_is_valid():
    config = AppConfig.load(Path(__file__).resolve().parent.parent / "config.json")
    assert config.data.constituent_sources == ("wikipedia", "github-datasets")
