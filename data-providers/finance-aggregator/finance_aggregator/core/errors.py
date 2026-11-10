"""Exception hierarchy shared by providers, services and consumers.

Consumers only need to distinguish two outcomes besides success:
  * DataNotFoundError        - the data does not exist in any source that could be queried.
  * ProviderUnavailableError - the source(s) could not answer (offline, misconfigured, failing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .models import ProviderAttempt


class FinanceError(Exception):
    """Base class for all finance aggregator errors."""


class ConfigurationError(FinanceError):
    """Invalid or missing configuration."""


class InvalidRequestError(FinanceError, ValueError):
    """The request itself is invalid (bad symbol, bad date range...)."""


class DataNotFoundError(FinanceError):
    """The requested data does not exist in the queried source(s)."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        attempts: Sequence["ProviderAttempt"] = (),
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.attempts = tuple(attempts)

    @property
    def unchecked_providers(self) -> tuple[str, ...]:
        """Providers that could not be queried, so 'not found' is only as complete as the rest."""
        from .models import AttemptOutcome

        return tuple(
            a.provider
            for a in self.attempts
            if a.outcome in (AttemptOutcome.UNAVAILABLE, AttemptOutcome.ERROR)
        )


class ProviderError(FinanceError):
    """A provider failed while answering (bad response, schema mismatch, unexpected failure)."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        attempts: Sequence["ProviderAttempt"] = (),
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.attempts = tuple(attempts)


class ProviderUnavailableError(ProviderError):
    """A provider cannot be reached or is not usable right now (offline, missing file, rate-limited)."""
