from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

from fakes import TODAY, FakeMarket, market_with
from finance_aggregator.core import ConfigurationError, HealthState, ProviderUnavailableError, ServiceStatus
from owl.app import OwlApplication
from owl.charts import ChartService
from owl.config import OwlConfig, ServerConfig
from owl.server import OwlApi, Request, StaticFiles
from owl.watchlist import SqliteWatchlistRepository

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


@pytest.fixture
def market() -> FakeMarket:
    m = market_with("AAPL", days=400)
    m.names["NVDA"] = "NVIDIA Corporation"
    return m


@pytest.fixture
def api(tmp_path: Path, market: FakeMarket) -> Iterator[OwlApi]:
    repo = SqliteWatchlistRepository(tmp_path / "owl.db")
    yield OwlApi(market, repo, ChartService(market, today=lambda: TODAY))
    repo.close()


def call(api: OwlApi, method: str, path: str, body: Any = None, **query: str) -> tuple[int, Any]:
    response = api.handle(Request(method, path, query, {}, body))
    return int(response.status), response.body


class TestApi:
    def test_groups_crud(self, api: OwlApi) -> None:
        status, groups = call(api, "GET", "/api/groups")
        assert status == 200 and groups["data"][0]["name"] == "Watchlist"
        status, created = call(api, "POST", "/api/groups", {"name": "Semis"})
        assert status == 201
        group_id = created["data"]["id"]
        assert call(api, "PATCH", f"/api/groups/{group_id}", {"name": "Chips"})[1]["data"]["name"] == "Chips"
        assert call(api, "POST", "/api/groups", {"name": "chips"})[0] == 409
        assert call(api, "DELETE", f"/api/groups/{group_id}")[0] == 200
        assert call(api, "DELETE", f"/api/groups/{group_id}")[0] == 404

    def test_add_symbol_validates_against_market_data(self, api: OwlApi) -> None:
        group_id = call(api, "POST", "/api/groups", {"name": "Semis"})[1]["data"]["id"]
        status, body = call(api, "POST", f"/api/groups/{group_id}/items", {"symbol": "nvda"})
        assert status == 201
        assert body["data"]["items"] == [{"symbol": "NVDA", "name": "NVIDIA Corporation"}]
        assert call(api, "POST", f"/api/groups/{group_id}/items", {"symbol": "ZZZZ"})[0] == 404
        assert call(api, "POST", f"/api/groups/{group_id}/items", {"symbol": "bad/sym"})[0] == 400
        assert call(api, "POST", "/api/groups/999/items", {"symbol": "NVDA"})[0] == 404
        assert call(api, "DELETE", f"/api/groups/{group_id}/items/NVDA")[1]["data"]["items"] == []

    @pytest.mark.parametrize("body", [None, {}, {"name": 5}, ["Semis"]])
    def test_bad_bodies(self, api: OwlApi, body: Any) -> None:
        assert call(api, "POST", "/api/groups", body)[0] == 400

    def test_chart_endpoint(self, api: OwlApi) -> None:
        status, body = call(api, "GET", "/api/chart/aapl", range="3M", indicators="sma:20,ema:9")
        assert status == 200
        assert body["data"]["symbol"] == "AAPL"
        assert [i["key"] for i in body["data"]["indicators"]] == ["sma:20", "ema:9"]
        assert call(api, "GET", "/api/chart/AAPL", range="9Y")[0] == 400
        assert call(api, "GET", "/api/chart/MSFT")[0] == 404

    def test_provider_unavailable_is_503_without_details(self, api: OwlApi, market: FakeMarket) -> None:
        def down(*_: Any) -> Any:
            raise ProviderUnavailableError("C:\\secret\\stocks.db locked")

        market.get_price_history = down  # type: ignore[method-assign]
        status, body = call(api, "GET", "/api/chart/AAPL")
        assert status == 503 and "secret" not in json.dumps(body)

    def test_settings_whitelist(self, api: OwlApi) -> None:
        assert call(api, "PUT", "/api/settings/chart", {"indicators": []})[0] == 200
        assert call(api, "GET", "/api/settings/chart")[1]["data"] == {"indicators": []}
        assert call(api, "PUT", "/api/settings/other", {"x": 1})[0] == 404
        assert call(api, "PUT", "/api/settings/chart", [1, 2])[0] == 400

    def test_routing_errors(self, api: OwlApi) -> None:
        assert call(api, "GET", "/api/nope")[0] == 404
        assert call(api, "PUT", "/api/groups")[0] == 405
        assert call(api, "PATCH", "/api/groups/abc", {"name": "x"})[0] == 400

    def test_search_by_symbol_or_name_with_group_membership(self, api: OwlApi) -> None:
        status, body = call(api, "GET", "/api/search", q="nvidia")
        assert status == 200
        assert [(m["symbol"], m["name"], m["groups"]) for m in body["data"]] == [("NVDA", "NVIDIA Corporation", [])]
        hits = call(api, "GET", "/api/search", q="aap")[1]["data"]
        assert hits[0]["symbol"] == "AAPL" and hits[0]["groups"] == ["Watchlist"]
        assert call(api, "GET", "/api/search", q="zzqq")[1]["data"] == []
        assert call(api, "GET", "/api/search", q="")[0] == 400
        assert call(api, "GET", "/api/search", q="a", limit="0")[0] == 400


class TestProviderStatus:
    def test_status_is_cached_and_refresh_forces_a_new_check(self, api: OwlApi, market: FakeMarket) -> None:
        status, body = call(api, "GET", "/api/status")
        assert status == 200 and body["data"]["aggregator"]["state"] == "available"
        assert body["data"]["checked_at"]
        call(api, "GET", "/api/status")
        assert market.status_calls == 1
        market.status = ServiceStatus("fake", HealthState.UNAVAILABLE, (), "0/1 providers available")
        assert call(api, "GET", "/api/status")[1]["data"]["aggregator"]["state"] == "available"  # cached
        assert call(api, "GET", "/api/status", refresh="1")[1]["data"]["aggregator"]["state"] == "unavailable"
        assert market.status_calls == 2

    def test_cache_expires(self, tmp_path: Path, market: FakeMarket) -> None:
        repo = SqliteWatchlistRepository(tmp_path / "o.db")
        api = OwlApi(market, repo, ChartService(market), status_ttl_seconds=0)
        call(api, "GET", "/api/status")
        call(api, "GET", "/api/status")
        assert market.status_calls == 2
        repo.close()

    def test_failing_status_check_reports_unavailable(self, api: OwlApi, market: FakeMarket) -> None:
        market.status = RuntimeError("aggregator crashed")
        status, body = call(api, "GET", "/api/status", refresh="1")
        assert status == 200
        assert body["data"]["aggregator"]["state"] == "unavailable"

    def test_recovery_after_outage(self, api: OwlApi, market: FakeMarket) -> None:
        healthy = market.status
        market.status = ServiceStatus("fake", HealthState.UNAVAILABLE, (), "down")
        assert call(api, "GET", "/api/status", refresh="1")[1]["data"]["aggregator"]["state"] == "unavailable"
        market.status = healthy
        assert call(api, "GET", "/api/status", refresh="1")[1]["data"]["aggregator"]["state"] == "available"


class TestStaticFiles:
    def test_serves_index_and_assets(self) -> None:
        static = StaticFiles(WEB_ROOT)
        index = static.serve("/")
        assert index.status == 200 and b"<title>Owl</title>" in index.body
        assert static.serve("/js/app.js").content_type.startswith("text/javascript")

    @pytest.mark.parametrize("path", ["/../config.json", "/..%2f..%2fconfig.json", "/%2e%2e/owl/app.py", "/missing.js"])
    def test_traversal_and_missing(self, path: str) -> None:
        assert StaticFiles(WEB_ROOT).serve(path).status == 404


@pytest.fixture
def running(tmp_path: Path, market: FakeMarket) -> Iterator[tuple[str, OwlApplication]]:
    def start(**server: Any) -> tuple[str, OwlApplication]:
        config = OwlConfig.from_dict(
            {"aggregator": {"mode": "remote"}, "server": {"port": 0, **server},
             "storage": {"database": str(tmp_path / "owl.db")}, "web": {"root": str(WEB_ROOT)}},
            base_dir=tmp_path,
        )
        app = OwlApplication(config, market=market)
        srv = app.create_server()
        srv.start()
        started.append((srv, app))
        return srv.url, app

    started: list[Any] = []
    yield start  # type: ignore[misc]
    for srv, app in started:
        srv.stop()
        app.close()


def http(url: str, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class TestHttpServer:
    def test_static_api_and_headers(self, running: Any) -> None:
        url, _ = running()
        status, headers, body = http(url + "/")
        assert status == 200 and "Content-Security-Policy" in headers and headers["X-Content-Type-Options"] == "nosniff"
        status, headers, _ = http(url + "/css/owl.css")
        assert http(url + "/css/owl.css", headers={"If-None-Match": headers["ETag"]})[0] == 304
        status, _, body = http(url + "/api/groups")
        assert status == 200 and json.loads(body)["data"][0]["name"] == "Watchlist"

    def test_write_requires_json_content_type(self, running: Any) -> None:
        url, _ = running()
        assert http(url + "/api/groups", "POST", {"name": "X"}, {"Content-Type": "text/plain"})[0] == 415
        assert http(url + "/api/groups", "POST", {"name": "X"}, {"Content-Type": "application/json"})[0] == 201

    def test_cors_only_for_allowed_origins(self, running: Any) -> None:
        url, _ = running()
        _, headers, _ = http(url + "/api/groups", "OPTIONS", headers={"Origin": "capacitor://localhost"})
        assert headers.get("Access-Control-Allow-Origin") == "capacitor://localhost"
        assert "X-API-Key" in headers.get("Access-Control-Allow-Headers", "")
        _, headers, _ = http(url + "/api/groups", "OPTIONS", headers={"Origin": "https://evil.example"})
        assert "Access-Control-Allow-Origin" not in headers

    def test_api_key(self, running: Any) -> None:
        url, _ = running(api_key="s3cret")
        assert http(url + "/api/health")[0] == 200
        assert http(url + "/api/groups")[0] == 401
        assert http(url + "/api/groups", headers={"X-API-Key": "s3cret"})[0] == 200
        assert http(url + "/")[0] == 200  # UI itself is public; data needs the key

    def test_body_limit(self, running: Any) -> None:
        url, _ = running()
        status, _, _ = http(url + "/api/groups", "POST", {"name": "x" * 70_000}, {"Content-Type": "application/json"})
        assert status == 400


def test_refuses_insecure_remote_binding(tmp_path: Path, market: FakeMarket) -> None:
    config = OwlConfig.from_dict(
        {"aggregator": {"mode": "remote"}, "server": {"host": "0.0.0.0"}, "storage": {"database": str(tmp_path / "o.db")}},
        base_dir=tmp_path,
    )
    app = OwlApplication(config, market=market)
    with pytest.raises(ConfigurationError, match="api_key"):
        app.create_server()
    insecure_ok = replace(config, server=replace(config.server, port=0, allow_insecure_remote=True))
    OwlApplication(insecure_ok, market=market, watchlist=app.watchlist).create_server()._httpd.server_close()
    app.close()


def test_server_config_loopback_detection() -> None:
    assert ServerConfig(host="127.0.0.1").is_loopback and ServerConfig(host="localhost").is_loopback
    assert ServerConfig(host="::1").is_loopback
    assert not ServerConfig(host="0.0.0.0").is_loopback and not ServerConfig(host="192.168.1.5").is_loopback
