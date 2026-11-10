from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from fakes import FakeProvider
from finance_aggregator.app import Application, build_providers, main
from finance_aggregator.config import AppConfig
from finance_aggregator.core import (
    ConfigurationError,
    DateRange,
    InvalidRequestError,
    PriceHistory,
    normalize_symbol,
)
from finance_aggregator.providers import (
    MisconfiguredProvider,
    ProviderContext,
    ProviderRegistry,
    YahooDbLocalProvider,
)


class TestModels:
    @pytest.mark.parametrize("raw,expected", [(" aapl ", "AAPL"), ("brk-b", "BRK-B"), ("^gspc", "^GSPC"), ("EURUSD=X", "EURUSD=X")])
    def test_symbol_normalisation(self, raw: str, expected: str) -> None:
        assert normalize_symbol(raw) == expected

    @pytest.mark.parametrize("raw", ["", "  ", "../x", "AAPL;DROP", "A" * 25, "a/b"])
    def test_symbol_rejection(self, raw: str) -> None:
        with pytest.raises(InvalidRequestError):
            normalize_symbol(raw)

    def test_date_range_options(self) -> None:
        today = date(2026, 11, 10)
        assert DateRange.from_options(today=today) == DateRange(date(2026, 10, 11), today)
        assert DateRange.from_options(days=5, today=today).start == date(2026, 11, 5)
        assert DateRange.from_options(months=1, today=today).start == date(2026, 10, 11)
        assert DateRange.from_options(start="2026-01-01", end="2026-01-31") == DateRange(date(2026, 1, 1), date(2026, 1, 31))

    @pytest.mark.parametrize(
        "kwargs",
        [{"days": 5, "months": 1}, {"days": 5, "start": "2026-01-01"}, {"end": "2026-01-01"},
         {"start": "2026-02-01", "end": "2026-01-01"}, {"start": "bad"}, {"days": 0}],
    )
    def test_date_range_rejection(self, kwargs: dict) -> None:
        with pytest.raises(InvalidRequestError):
            DateRange.from_options(**kwargs)

    def test_history_bars_are_sorted(self) -> None:
        from fakes import make_bars

        bars = make_bars(date(2026, 1, 1), 3)
        history = PriceHistory("X", DateRange(date(2026, 1, 1), date(2026, 1, 3)), tuple(reversed(bars)))
        assert history.first_date == date(2026, 1, 1) and history.last_date == date(2026, 1, 3)


class TestConfig:
    def test_load_resolves_paths_relative_to_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"providers": [{"type": "yahoo_db_local", "options": {"db_path": "x.db"}}],
                                   "logging": {"file": "logs/app.log"}}))
        config = AppConfig.load(cfg)
        assert config.base_dir == cfg.parent.resolve()
        assert config.logging.file == (cfg.parent / "logs" / "app.log").resolve()
        assert config.providers[0].name == "yahoo_db_local"
        assert config.server.port == 5100

    @pytest.mark.parametrize(
        "raw",
        [[], {"providers": {}}, {"providers": [{"name": "x"}]},
         {"providers": [{"type": "a", "name": "n"}, {"type": "b", "name": "n"}]},
         {"server": {"port": 70000}}, {"logging": {"level": "LOUD"}}, {"plugins": "x"}],
    )
    def test_invalid_config(self, raw: object, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            AppConfig.from_dict(raw, base_dir=tmp_path)

    def test_missing_and_malformed_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            AppConfig.load(tmp_path / "nope.json")
        bad = tmp_path / "bad.json"
        bad.write_text("{oops")
        with pytest.raises(ConfigurationError, match="Invalid JSON"):
            AppConfig.load(bad)

    def test_shipped_config_is_valid(self) -> None:
        config = AppConfig.load(Path(__file__).resolve().parent.parent / "config.json")
        assert [p.type for p in config.providers] == ["yahoo_db_local", "yahoo_db_api", "yahoo_finance"]


class TestRegistryAndComposition:
    def test_unknown_type_becomes_misconfigured_placeholder(self, tmp_path: Path) -> None:
        config = AppConfig.from_dict(
            {"providers": [{"type": "does_not_exist", "name": "ghost"}, {"type": "yahoo_db_local", "options": {"db_path": "x.db"}}]},
            base_dir=tmp_path,
        )
        providers = build_providers(config, ProviderRegistry(), ProviderContext(tmp_path))
        assert all(isinstance(p, MisconfiguredProvider) for p in providers)
        assert providers[0].check_status().detail.startswith("misconfigured: Unknown provider type")

    def test_custom_provider_registration_and_order(self, tmp_path: Path) -> None:
        registry = ProviderRegistry()
        registry.register("fake", lambda name, options, ctx: FakeProvider(name))
        config = AppConfig.from_dict(
            {"providers": [{"type": "fake", "name": "first"}, {"type": "fake", "name": "off", "enabled": False},
                           {"type": "fake", "name": "second"}]},
            base_dir=tmp_path,
        )
        app = Application(config, registry=registry)
        assert [p.name for p in app.providers] == ["first", "second"]
        assert app.aggregator.get_ticker_info("AAPL").source == "first"

    def test_duplicate_registration_rejected(self) -> None:
        registry = ProviderRegistry()
        registry.register("fake", lambda n, o, c: FakeProvider(n))
        with pytest.raises(ValueError):
            registry.register("fake", lambda n, o, c: FakeProvider(n))

    def test_builtin_providers_registered(self) -> None:
        from finance_aggregator.providers import default_registry

        assert {"yahoo_db_local", "yahoo_db_api", "yahoo_finance"} <= set(default_registry.kinds)

    def test_missing_db_path_is_configuration_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            YahooDbLocalProvider.from_config("l", {}, ProviderContext(tmp_path))


class TestMain:
    def test_one_shot_read_against_local_db(self, yahoo_db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "providers": [{"type": "yahoo_db_local", "name": "local", "options": {"db_path": str(yahoo_db_file)}}],
            "aggregator": {"history_max_staleness_days": None},
            "logging": {"file": None},
        }))
        code = main(["--config", str(cfg), "read", "AAPL", "--start", "2026-01-01", "--end", "2026-01-31", "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["info"]["source"] == "local"
        assert payload["history"]["count"] == 3

    def test_exit_codes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"providers": [{"type": "yahoo_db_local", "options": {"db_path": "none.db"}}],
                                   "logging": {"file": None}}))
        assert main(["--config", str(cfg), "read", "AAPL"]) == 4  # nothing could answer
        assert main(["--config", str(cfg), "bogus"]) == 2
        assert main(["--config", str(tmp_path / "missing.json"), "help"]) == 2
        assert main(["--config", str(cfg), "help"]) == 0
        assert "Unavailable" in capsys.readouterr().err
