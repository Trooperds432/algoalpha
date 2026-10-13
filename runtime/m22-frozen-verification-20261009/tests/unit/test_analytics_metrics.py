from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from quant_terminal.accounting.ledger import PositionSnapshot
from quant_terminal.analytics import AnalyticsConfig, RiskOfRuinConfig, analyze
from quant_terminal.backtesting.models import BacktestResult, NAVPoint
from quant_terminal.core.identifiers import FillId, InstrumentId, OrderId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.orders.models import Fill, Side


def _point(day: int, nav: str, *, mark: str = "100") -> NAVPoint:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day)
    value = Decimal(nav)
    position = PositionSnapshot(
        InstrumentId("SPY"),
        "SPY",
        Decimal(0),
        Decimal(mark),
        Decimal(mark),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )
    return NAVPoint(
        timestamp,
        value,
        None,
        value,
        (position,),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )


def _result(nav: tuple[NAVPoint, ...], fills: tuple[Fill, ...] = ()) -> BacktestResult:
    return BacktestResult(
        "fixture",
        nav[0].timestamp,
        nav[-1].timestamp,
        nav,
        (),
        fills,
        (),
        sum((fill.commission for fill in fills), Decimal(0)),
        Decimal(0),
        (),
        (("currency", "USD"),),
    )


def _fill(sequence: int, side: Side, qty: str, price: str, fee: str, day: int) -> Fill:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day)
    instrument = InstrumentMeta(
        InstrumentId("SPY"),
        "SPY",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal("1"),
    )
    return Fill(
        FillId(f"fill-{sequence}"),
        OrderId(f"order-{sequence}"),
        instrument,
        side,
        Decimal(qty),
        Decimal(price),
        Decimal(price),
        Decimal(fee),
        timestamp,
        timestamp,
    )


def test_returns_sharpe_and_unrecovered_drawdown_match_hand_calculation() -> None:
    report = analyze(
        _result((_point(0, "100"), _point(1, "110"), _point(2, "99"), _point(3, "108.9")))
    )

    assert report.metric("total_return").value == Decimal("0.089")
    assert report.metric("maximum_drawdown").value == Decimal("0.1")
    assert report.metric("drawdown_duration_days").value == Decimal(2)
    assert report.metric("drawdown_duration_sessions").value == Decimal(2)
    assert report.metric("recovery_time_days").value is None
    assert report.metric("annualized_volatility").value == pytest.approx(
        Decimal("1.83303027798"), rel=Decimal("1e-9")
    )
    assert report.metric("sharpe").value == pytest.approx(
        Decimal("4.582575695"), rel=Decimal("1e-9")
    )


def test_recovered_drawdown_duration_matches_independent_path() -> None:
    report = analyze(
        _result(
            (
                _point(0, "100"),
                _point(1, "120"),
                _point(2, "90"),
                _point(3, "108"),
                _point(4, "120"),
            )
        )
    )

    assert report.metric("maximum_drawdown").value == Decimal("0.25")
    assert report.metric("drawdown_duration_days").value == Decimal(3)
    assert report.metric("drawdown_duration_sessions").value == Decimal(3)
    assert report.metric("recovery_time_days").value == Decimal(3)


def test_public_analytics_are_independent_of_callers_decimal_precision() -> None:
    result = _result((_point(0, "100"), _point(1, "103"), _point(2, "99")))
    expected = analyze(result)

    with localcontext() as context:
        context.prec = 6
        observed = analyze(result)

    assert observed == expected


def test_fifo_trade_statistics_allocate_commissions_per_matched_slice() -> None:
    result = _result(
        (_point(0, "100", mark="100"), _point(1, "100", mark="110"), _point(2, "100", mark="90")),
        (
            _fill(1, Side.BUY, "10", "100", "1", 0),
            _fill(2, Side.SELL, "4", "110", "0.4", 1),
            _fill(3, Side.SELL, "6", "90", "0.6", 2),
        ),
    )
    report = analyze(result)

    assert report.metric("trade_count").value == Decimal(2)
    assert report.metric("win_rate").value == Decimal("0.5")
    assert report.metric("profit_factor").value == pytest.approx(
        Decimal("39.2") / Decimal("61.2"), rel=Decimal("1e-30")
    )
    assert report.metric("average_win").value == Decimal("39.2")
    assert report.metric("average_loss").value == Decimal("-61.2")
    assert report.metric("expectancy").value == Decimal("-11")
    assert report.metric("commissions").value == Decimal("2.0")


def test_fifo_multi_lot_partial_close_and_reversal_allocate_all_fees() -> None:
    result = _result(
        tuple(_point(day, "100") for day in range(5)),
        (
            _fill(1, Side.BUY, "2", "100", "0.2", 0),
            _fill(2, Side.BUY, "3", "110", "0.3", 1),
            _fill(3, Side.SELL, "4", "120", "0.4", 2),
            _fill(4, Side.SELL, "3", "90", "0.3", 3),
        ),
    )
    report = analyze(result)

    # The first exit closes 2 + 2 units across FIFO lots. The final sell closes
    # the remaining 1 long and opens a 2-unit short, which remains unclosed.
    assert report.metric("trade_count").value == Decimal(3)
    assert report.metric("average_win").value == Decimal("29.6")
    assert report.metric("average_loss").value == Decimal("-20.2")
    assert report.metric("commissions").value == Decimal("1.2")


def test_thin_tail_estimates_are_flagged_and_ruin_bootstrap_is_seeded() -> None:
    result = _result((_point(0, "100"), _point(1, "99"), _point(2, "101")))
    config = AnalyticsConfig(
        risk_of_ruin=RiskOfRuinConfig(Decimal("0.1"), Decimal("0.5"), 20, 500, 7)
    )

    first = analyze(result, config)
    second = analyze(result, config)
    assert first.metric("var_95").status == "insufficient_sample"
    assert first.metric("expected_shortfall_95").status == "insufficient_sample"
    assert first.metric("risk_of_ruin").value == second.metric("risk_of_ruin").value
    assert first.metric("risk_of_ruin").value is None
    assert first.metric("alpha").value is None


def test_custom_confidence_label_preserves_trailing_integer_zero() -> None:
    report = analyze(
        _result((_point(0, "100"), _point(1, "101"))),
        AnalyticsConfig(var_confidences=(Decimal("0.90"),)),
    )

    assert report.metric("var_90").observations == 1
    assert report.metric("expected_shortfall_90").observations == 1


def test_explicit_risk_of_ruin_assumptions_produce_expected_probability() -> None:
    result = _result(
        (_point(0, "100"), _point(1, "50")),
        (
            _fill(1, Side.BUY, "1", "100", "0", 0),
            _fill(2, Side.SELL, "1", "50", "0", 1),
        ),
    )
    config = AnalyticsConfig(
        risk_of_ruin=RiskOfRuinConfig(Decimal("0.5"), Decimal("0.8"), 1, 100, 44)
    )

    report = analyze(result, config)

    assert report.metric("risk_of_ruin").value == Decimal(1)
    assert report.metric("risk_of_ruin").observations == 100


def test_no_trade_and_zero_variance_ratios_are_explicitly_unavailable() -> None:
    report = analyze(_result((_point(0, "100"), _point(1, "100"), _point(2, "100"))))

    assert report.metric("trade_count").value == 0
    assert report.metric("expectancy").value is None
    assert report.metric("profit_factor").value is None
    assert report.metric("sharpe").value is None
    assert report.metric("sortino").value is None


def test_returns_after_insolvency_are_not_joined_across_the_failed_path() -> None:
    report = analyze(_result((_point(0, "100"), _point(1, "0"), _point(2, "50"), _point(3, "60"))))

    assert report.metric("total_return").value == Decimal("-0.4")
    assert report.metric("annualized_arithmetic_return").value is None
    assert report.metric("sharpe").value is None
    assert dict(report.assumptions)["insolvent_path"] == "True"


def test_short_round_trip_and_position_excursions_respect_trade_direction() -> None:
    result = _result(
        (
            _point(0, "100", mark="100"),
            _point(1, "100", mark="105"),
            _point(2, "100", mark="90"),
        ),
        (
            _fill(1, Side.SELL, "2", "100", "0", 0),
            _fill(2, Side.BUY, "2", "90", "0", 2),
        ),
    )
    report = analyze(result)

    assert report.metric("average_win").value == Decimal("20")
    assert report.metric("average_mae_per_unit").value == Decimal("-5")
    assert report.metric("average_mfe_per_unit").value == Decimal("10")


def test_excursion_metrics_include_entry_baseline_for_an_all_adverse_path() -> None:
    result = _result(
        (_point(0, "100", mark="100"), _point(1, "90", mark="100")),
        (
            _fill(1, Side.BUY, "1", "110", "0", 0),
            _fill(2, Side.SELL, "1", "100", "0", 1),
        ),
    )

    report = analyze(result)

    assert report.metric("average_mae_per_unit").value == Decimal("-10")
    assert report.metric("average_mfe_per_unit").value == Decimal(0)


def test_breakeven_trade_breaks_consecutive_win_and_loss_runs() -> None:
    result = _result(
        tuple(_point(day, "100") for day in range(7)),
        (
            _fill(1, Side.BUY, "1", "100", "0", 0),
            _fill(2, Side.SELL, "1", "110", "0", 1),
            _fill(3, Side.BUY, "1", "100", "0", 2),
            _fill(4, Side.SELL, "1", "100", "0", 3),
            _fill(5, Side.BUY, "1", "100", "0", 4),
            _fill(6, Side.SELL, "1", "90", "0", 5),
            _fill(7, Side.BUY, "1", "100", "0", 5),
            _fill(8, Side.SELL, "1", "110", "0", 6),
        ),
    )

    report = analyze(result)

    assert report.metric("max_consecutive_wins").value == Decimal(1)
    assert report.metric("max_consecutive_losses").value == Decimal(1)


def test_var_and_fractional_tail_es_match_known_losses() -> None:
    from quant_terminal.analytics.metrics import _es, _quantile

    losses = [Decimal("-0.1"), Decimal("0"), Decimal("0.1"), Decimal("0.2"), Decimal("0.4")]

    assert _quantile(losses, Decimal("0.75")) == Decimal("0.2")
    assert _es(losses, Decimal("0.8")) == Decimal("0.4")


def test_configuration_rejects_nonfinite_confidence_values() -> None:
    with pytest.raises(ValueError):
        AnalyticsConfig(var_confidences=(Decimal("NaN"),))
