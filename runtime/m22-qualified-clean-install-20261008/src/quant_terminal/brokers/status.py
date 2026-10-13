"""Explicit broker mode status for the terminal and its loopback API."""

from typing import Final

BROKER_STATUS: Final[dict[str, object]] = {
    "provider": "Trading 212",
    "mode": "demo_architecture_only",
    "connection_enabled": False,
    "order_submission_enabled": False,
    "live_mode_available": False,
    "reason": "M18 architecture only; external connection and orders are disabled by the build",
}
