from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

import pytest

from fakes import FakeProvider, broken, not_found, unavailable
from finance_aggregator.server import ApiApplication, ApiServer
from finance_aggregator.services import AggregatorService


def app_with(*providers: FakeProvider, api_key: str | None = None) -> ApiApplication:
    aggregator = AggregatorService(providers)
    return ApiApplication(aggregator, aggregator, api_key)


def test_ticker_endpoint() -> None:
    response = app_with(FakeProvider("api")).handle_get("/api/ticker/aapl", {})
    assert response.status == 200
    assert response.body["source"] == "api" and response.body["data"]["symbol"] == "AAPL"


def test_history_endpoint_matches_yahoo_db_shape() -> None:
    response = app_with(FakeProvider("api")).handle_get(
        "/api/ticker/AAPL/history?start_date=2026-01-01&end_date=2026-01-05", {}
    )
    assert response.status == 200
    assert {"symbol", "start_date", "end_date", "count", "data", "source"} <= set(response.body)
    assert response.body["count"] == 5


@pytest.mark.parametrize(
    "path,status",
    [
        ("/api/ticker/AAPL/history?days=abc", 400),
        ("/api/ticker/AAPL/history?days=5&months=1", 400),
        ("/api/ticker/..%2Fsync%2Fstart", 400),
        ("/api/unknown", 404),
    ],
)
def test_bad_requests(path: str, status: int) -> None:
    assert app_with(FakeProvider("api")).handle_get(path, {}).status == status


def test_not_found_vs_unavailable_vs_error_status_codes() -> None:
    assert app_with(FakeProvider("a", info=not_found())).handle_get("/api/ticker/X", {}).status == 404
    unavailable_response = app_with(FakeProvider("a", info=unavailable())).handle_get("/api/ticker/X", {})
    assert unavailable_response.status == 503
    assert unavailable_response.body["attempts"] == [{"provider": "a", "outcome": "unavailable"}]
    assert "offline" not in json.dumps(unavailable_response.body), "internal details must not leak"
    assert app_with(FakeProvider("a", info=broken())).handle_get("/api/ticker/X", {}).status == 503


def test_unexpected_failure_does_not_leak_details() -> None:
    class Exploding(AggregatorService):
        def get_ticker_info(self, symbol: str):  # type: ignore[no-untyped-def]
            raise RuntimeError("C:\\secret\\path")

    agg = Exploding([])
    response = ApiApplication(agg, agg).handle_get("/api/ticker/AAPL", {})
    assert response.status == 500
    assert "secret" not in json.dumps(response.body)


def test_api_key_enforced_except_health() -> None:
    app = app_with(FakeProvider("api"), api_key="s3cret")
    assert app.handle_get("/api/health", {}).status == 200
    assert app.handle_get("/api/ticker/AAPL", {}).status == 401
    assert app.handle_get("/api/ticker/AAPL", {"X-API-Key": "wrong"}).status == 401
    assert app.handle_get("/api/ticker/AAPL", {"X-API-Key": "s3cret"}).status == 200


@pytest.fixture
def running_server() -> Iterator[ApiServer]:
    aggregator = AggregatorService([FakeProvider("local", available=False), FakeProvider("api")])
    with ApiServer(aggregator, aggregator, host="127.0.0.1", port=0) as server:
        yield server


def test_real_socket_round_trip(running_server: ApiServer) -> None:
    with urllib.request.urlopen(f"{running_server.url}/api/ticker/MSFT", timeout=5) as resp:
        body = json.loads(resp.read())
    assert resp.status == 200 and body["source"] == "api"
    with urllib.request.urlopen(f"{running_server.url}/api/status", timeout=5) as resp:
        status = json.loads(resp.read())["data"]
    assert status["state"] == "degraded"
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"{running_server.url}/api/ticker/bad$sym", timeout=5)
    assert caught.value.code == 400
