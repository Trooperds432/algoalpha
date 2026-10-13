"""Walk-forward planning and IS capability checks."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import DatasetIdentity
from quant_terminal.walk_forward import (
    TrainingContext,
    WalkForwardSpec,
    WindowMode,
    plan_windows,
)

START = datetime(2025, 1, 1, tzinfo=UTC)
DATASET = DatasetIdentity("d" * 64, "a" * 64, "b" * 64, 3, ("ETF-1",))


def _spec(mode: WindowMode, *, end_days: int = 155) -> WalkForwardSpec:
    return WalkForwardSpec(
        "wf-1",
        {"capital": Decimal("10000"), "settings": {"fee": Decimal("0.01")}},
        (DATASET,),
        "c" * 64,
        START,
        START + timedelta(days=end_days),
        timedelta(days=60),
        timedelta(days=30),
        timedelta(days=30),
        mode,
    )


def test_rolling_windows_have_fixed_training_and_contiguous_oos() -> None:
    windows = plan_windows(_spec(WindowMode.ROLLING))
    assert len(windows) == 4
    assert windows[0].train_start == START
    assert windows[1].train_start == START + timedelta(days=30)
    assert windows[1].train_end == windows[1].oos_start
    assert windows[-1].oos_end == START + timedelta(days=155)
    assert windows[-1].oos_end - windows[-1].oos_start == timedelta(days=5)
    assert all(
        left.oos_end == right.oos_start for left, right in zip(windows, windows[1:], strict=False)
    )


def test_expanding_windows_anchor_training_start_and_spec_freezes_identity() -> None:
    identity = {"nested": {"capital": Decimal("10000")}}
    spec = WalkForwardSpec(
        "wf-1",
        identity,
        (DATASET,),
        "c" * 64,
        START,
        START + timedelta(days=155),
        timedelta(days=60),
        timedelta(days=30),
        timedelta(days=30),
        WindowMode.EXPANDING,
    )
    first_fp = spec.fingerprint
    identity["nested"]["capital"] = Decimal("1")
    windows = plan_windows(spec)
    assert windows[0].train_start == windows[-1].train_start == START
    assert spec.fingerprint == first_fp
    context = TrainingContext(
        0, windows[0].train_start, windows[0].train_end, (DATASET,), spec.fingerprint
    )
    assert not hasattr(context, "oos_start")
    assert not hasattr(context, "oos_end")


def test_walk_forward_rejects_oos_gaps_invalid_durations_and_short_history() -> None:
    with pytest.raises(DomainError, match="step must equal"):
        WalkForwardSpec(
            "bad",
            {"capital": 1},
            (DATASET,),
            "c" * 64,
            START,
            START + timedelta(days=100),
            timedelta(days=60),
            timedelta(days=30),
            timedelta(days=15),
            WindowMode.ROLLING,
        )
    with pytest.raises(DomainError, match="at least one full IS"):
        WalkForwardSpec(
            "short",
            {"capital": 1},
            (DATASET,),
            "c" * 64,
            START,
            START + timedelta(days=60),
            timedelta(days=60),
            timedelta(days=30),
            timedelta(days=30),
            WindowMode.ROLLING,
        )
