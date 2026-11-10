"""Provider registry: maps the configuration ``type`` key to a provider factory."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TypeVar

from ..core.errors import ConfigurationError, ProviderUnavailableError
from ..core.interfaces import DataProvider
from ..core.models import DateRange, PriceHistory, TickerInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderContext:
    """Environment information a factory may need (path resolution, bundled resources)."""

    base_dir: Path
    bundle_dir: Path | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()


class ProviderFactory(Protocol):
    def __call__(self, name: str, options: Mapping[str, Any], context: ProviderContext) -> DataProvider: ...


P = TypeVar("P", bound=type[DataProvider])


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, kind: str, factory: ProviderFactory, *, replace: bool = False) -> None:
        if not kind:
            raise ValueError("Provider kind must be a non-empty string")
        if kind in self._factories and not replace:
            raise ValueError(f"Provider kind {kind!r} is already registered")
        self._factories[kind] = factory

    def provider(self, cls: P) -> P:
        """Class decorator: registers ``cls.from_config`` under ``cls.kind``."""
        from_config = getattr(cls, "from_config", None)
        if from_config is None:
            raise TypeError(f"{cls.__name__} must define a from_config(name, options, context) classmethod")
        self.register(cls.kind, from_config)
        return cls

    def create(
        self, kind: str, name: str, options: Mapping[str, Any], context: ProviderContext
    ) -> DataProvider:
        factory = self._factories.get(kind)
        if factory is None:
            known = ", ".join(sorted(self._factories)) or "none"
            raise ConfigurationError(f"Unknown provider type {kind!r} (known: {known})")
        return factory(name, options, context)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


#: Registry used by the built-in providers and by plugins.
default_registry = ProviderRegistry()


def register_provider(cls: P) -> P:
    """Register a DataProvider subclass in the default registry."""
    return default_registry.provider(cls)


class MisconfiguredProvider(DataProvider):
    """Null object standing in for a provider that could not be constructed.

    Keeps the rest of the chain working while making the problem visible in `status`.
    """

    def __init__(self, name: str, kind: str, reason: str) -> None:
        super().__init__(name)
        self.kind = kind  # type: ignore[misc]  # per-instance kind of the failed provider
        self._reason = reason

    def is_available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return f"misconfigured: {self._reason}"

    def _fail(self) -> ProviderUnavailableError:
        return ProviderUnavailableError(self.unavailable_reason(), provider=self.name)

    def _fetch_ticker_info(self, symbol: str) -> TickerInfo:
        raise self._fail()

    def _fetch_price_history(self, symbol: str, date_range: DateRange) -> PriceHistory:
        raise self._fail()

    def _probe(self) -> tuple[str, Mapping[str, Any]]:
        raise self._fail()


OptionT = TypeVar("OptionT")


def option(
    options: Mapping[str, Any],
    key: str,
    default: OptionT,
    convert: Callable[[Any], OptionT] | None = None,
) -> OptionT:
    """Read a typed option with a clear ConfigurationError on bad values."""
    if key not in options or options[key] is None:
        return default
    value = options[key]
    converter = convert or type(default)
    try:
        return converter(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid value for option {key!r}: {value!r}") from exc
