"""Validated initial application configuration; no live runtime is enabled."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .core.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class AppConfig:
    workspace: Path
    initial_capital: Decimal
    seed: int
    timezone: str = "UTC"
    max_workers: int = 2
    timeout_seconds: int = 300
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, Path) or not self.workspace.is_absolute():
            raise ConfigurationError("An absolute local workspace path is required")
        if str(self.workspace).startswith(("\\\\", "//")):
            raise ConfigurationError("Network workspaces are unsupported")
        if not isinstance(self.initial_capital, Decimal):
            raise ConfigurationError("Initial capital must be Decimal")
        if not self.initial_capital.is_finite() or self.initial_capital <= 0:
            raise ConfigurationError("Initial capital must be positive and finite")
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            raise ConfigurationError("Seed must be an unsigned64 integer")
        if self.timezone != "UTC":
            raise ConfigurationError("Internal timezone must be UTC")
        if type(self.max_workers) is not int or not 1 <= self.max_workers <= 3:
            raise ConfigurationError("Worker count must be between1 and3")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 3600:
            raise ConfigurationError("Timeout must be between1 and3600 seconds")
        if self.live_enabled is not False:
            raise ConfigurationError("Live runtime is disabled")
