"""Typed boundary failures."""


class DomainError(ValueError):
    """Invalid domain input."""


class TimeOrderError(DomainError):
    """An event would move observable time backwards."""


class FutureDataError(DomainError):
    """Requested data is unavailable at the capability time."""


class ConfigurationError(DomainError):
    """Configuration violates supported operation boundaries."""
