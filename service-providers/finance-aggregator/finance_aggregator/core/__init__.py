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
    "TickerInfo",
    "normalize_symbol",
    "parse_date",
]
