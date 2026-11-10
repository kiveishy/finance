"""Owl-specific errors (on top of the finance_aggregator error contract)."""

from __future__ import annotations

from finance_aggregator.core.errors import FinanceError, InvalidRequestError


class OwlError(FinanceError):
    """Base class for owl errors."""


class ResourceNotFoundError(OwlError):
    """A watchlist resource (group, symbol in group) does not exist."""


class ConflictError(OwlError):
    """The change conflicts with existing state (duplicate group name or symbol)."""


class ValidationError(InvalidRequestError, OwlError):
    """Invalid user input."""
