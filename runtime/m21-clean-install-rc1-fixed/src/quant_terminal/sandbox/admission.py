"""Public admission boundary for owner-supplied Python strategies.

The container adapter is preparatory until M16 runtime and hostile-workload
verification pass. No caller may turn a protocol message into execution yet.
"""

from __future__ import annotations

from quant_terminal.core.exceptions import DomainError

UNAVAILABLE_REASON = "isolation_backend_unverified"
BUILTIN_NAMES = frozenset({"buy_and_hold", "moving_average_cross", "simple_momentum"})
_SOURCE_FIELDS = frozenset(
    {
        "source",
        "source_path",
        "source_bytes",
        "strategy_source",
        "strategy_path",
        "dependencies",
        "execution_mode",
    }
)


def capabilities() -> dict[str, object]:
    return {
        "trusted_builtin": {"available": True},
        "isolated_python": {"available": False, "reason": UNAVAILABLE_REASON},
    }


def reject_isolated_python() -> None:
    """Fail before source parsing, staging, scheduler submission or launch."""
    raise DomainError(f"isolated_python unavailable: {UNAVAILABLE_REASON}")


def assert_trusted_request(value: object) -> None:
    """Reject mode confusion before writing a job request or launching a worker."""
    if isinstance(value, dict):
        if _SOURCE_FIELDS.intersection(value):
            raise DomainError(
                "Source packages and execution modes are not admitted to trusted jobs"
            )
        if "strategy" in value:
            name = value["strategy"]
            if not isinstance(name, str) or name not in BUILTIN_NAMES:
                raise DomainError("Unsupported built-in strategy")
        for child in value.values():
            assert_trusted_request(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_trusted_request(child)
