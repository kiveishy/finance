"""Core domain: models, errors and interfaces shared by all providers, services and consumers."""

from .errors import (
    ConfigurationError,
    DataNotFoundError,
    FinanceError,
    InvalidRequestError,
    ProviderError,
    ProviderUnavailableError,
)
from .interfaces import DataProvider, FinanceService, StockDataSource
from .models import (
    AttemptOutcome,
    DateRange,
    HealthState,
    PriceBar,
    PriceHistory,
    ProviderAttempt,
    ProviderStatus,
    ServiceStatus,
    TickerInfo,
    normalize_symbol,
    parse_date,
)
from .search import SymbolMatch, merge_matches, normalize_limit, normalize_query, relevance

__all__ = [
    "AttemptOutcome",
    "ConfigurationError",
    "DataNotFoundError",
    "DataProvider",
    "DateRange",
    "FinanceError",
    "FinanceService",
    "HealthState",
    "InvalidRequestError",
    "PriceBar",
    "PriceHistory",
    "ProviderAttempt",
    "ProviderError",
    "ProviderStatus",
    "ProviderUnavailableError",
    "ServiceStatus",
    "StockDataSource",
    "SymbolMatch",
    "TickerInfo",
    "merge_matches",
    "normalize_limit",
    "normalize_query",
    "normalize_symbol",
    "parse_date",
    "relevance",
]
