"""Independent synthetic accounting and recovery acceptance for M17."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from quant_terminal.backtesting.strategies import BuyAndHold
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import Bar
from quant_terminal.paper.clock import LiveClock
from quant_terminal.paper.engine import PaperSession

BASE = datetime(2026, 9, 30, 14, 31, tzinfo=UTC)


class Time:
    def __init__(self) -> None:
        self.value = BASE

    def __call__(self) -> datetime:
        return self.value


def payload() -> dict[str, object]:
    return {
        "instrument": {
            "instrument_id": "PAPER",
            "symbol": "PAPER",
            "currency": "USD",
            "asset_class": "etf",
            "calendar": "XNYS",
            "timezone": "America/New_York",
            "tick_size": "0.01",
            "quantity_step": "1",
        },
        "strategy": "buy_and_hold",
        "parameters": {"quantity": "10"},
        "initial_cash": "10000",
        "max_order_notional": "2000",
        "execution": {
            "commission_per_unit": "0.10",
            "commission_minimum": "1",
            "slippage_kind": "none",
            "maximum_participation": "0.5",
        },
        "stale_seconds": 30,
    }


def observation(number: int, price: str = "100", volume: str = "8") -> Bar:
    end = BASE + timedelta(minutes=number - 1)
    return Bar(
        InstrumentId("PAPER"),
        "1m",
        end - timedelta(minutes=1),
        end,
        end,
        Decimal(price),
        Decimal(price),
        Decimal(price),
        Decimal(price),
        Decimal(volume),
        "synthetic-independent-fixture",
    )


def session(tmp_path: Path) -> tuple[PaperSession, Time]:
    time = Time()
    paper = PaperSession.create(tmp_path / "paper.sqlite", payload(), clock=LiveClock(time))
    paper.reconnect("connect", paper.snapshot()["reconciliation"])
    return paper, time


def push(
    paper: PaperSession, time: Time, number: int, price: str = "100", volume: str = "8"
) -> dict[str, object]:
    item = observation(number, price, volume)
    time.value = item.end
    return paper.ingest(f"bar-{number}", number, item)


def test_exact_partial_fill_accounting_and_restart(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    first = push(paper, time, 1)
    assert first["portfolio"]["cash"] == "10000"
    assert first["orders"][0]["filled_quantity"] == "0"
    second = push(paper, time, 2, "101")
    # Four units at 101, one minimum fee: 10000 - 404 - 1 = 9595.
    assert Decimal(second["portfolio"]["cash"]) == Decimal("9595")
    assert Decimal(second["portfolio"]["nav"]) == Decimal("9999")
    assert second["orders"][0]["status"] == "partially_filled"
    recovered = PaperSession(paper.path, clock=LiveClock(time))
    assert recovered.snapshot()["portfolio"] == second["portfolio"]
    assert recovered.snapshot()["orders"] == second["orders"]
    assert recovered.snapshot()["connected"] is False
    time.value = observation(3).end
    with pytest.raises(DomainError):
        recovered.ingest("blocked", 3, observation(3))
    recovered.reconnect("restart-connect", recovered.snapshot()["reconciliation"])
    third = push(recovered, time, 3, "102")
    # Next four cost 408, minimum already paid, no incremental fee.
    assert Decimal(third["portfolio"]["cash"]) == Decimal("9187")
    assert Decimal(third["portfolio"]["nav"]) == Decimal("10003")
    fourth = push(recovered, time, 4, "103")
    assert Decimal(fourth["portfolio"]["cash"]) == Decimal("8981")
    assert Decimal(fourth["portfolio"]["nav"]) == Decimal("10011")
    assert fourth["orders"][0]["status"] == "filled"
    assert fourth["portfolio"]["reconciliation_error"] == "0"
    assert len(fourth["orders"]) == 1


def test_disconnect_and_matching_reconnect_preserve_pending_order(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    before = push(paper, time, 1)
    paper.disconnect("link-down")
    time.value = observation(2).end
    with pytest.raises(DomainError):
        paper.ingest("while-disconnected", 2, observation(2))
    assert paper.snapshot()["portfolio"] == before["portfolio"]
    assert paper.snapshot()["orders"] == before["orders"]
    paper.reconnect("link-up", paper.snapshot()["reconciliation"])
    assert push(paper, time, 2)["orders"][0]["filled_quantity"] == "4"


def test_duplicate_bar_and_fill_have_no_economic_effect(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    before = push(paper, time, 2)
    assert paper.ingest("bar-2", 2, observation(2))["duplicate"] is True
    fill = before["orders"][0]["fills"][0]
    assert paper.confirm_fill(fill)["duplicate"] is True
    assert paper.confirm_fill(fill)["portfolio"] == before["portfolio"]
    assert paper.snapshot()["orders"] == before["orders"]


@pytest.mark.parametrize("fault", ["missing", "old_sequence", "old_bar", "stale", "future"])
def test_invalid_observation_halts_without_fills(tmp_path: Path, fault: str) -> None:
    paper, time = session(tmp_path)
    before = push(paper, time, 1)
    sequence, item = 2, observation(2)
    time.value = item.end
    if fault == "missing":
        sequence = 3
    elif fault == "old_sequence":
        sequence = 1
    elif fault == "old_bar":
        item = observation(1)
    elif fault == "stale":
        time.value += timedelta(seconds=31)
    else:
        time.value -= timedelta(seconds=1)
    with pytest.raises(DomainError):
        paper.ingest("bad-observation", sequence, item)
    after = paper.snapshot()
    assert after["connected"] is False
    assert after["portfolio"] == before["portfolio"]
    assert after["orders"] == before["orders"]
    assert after["last_sequence"] == 1


def test_reconciliation_mismatch_never_overwrites_cash(tmp_path: Path) -> None:
    paper, _ = session(tmp_path)
    paper.disconnect("disconnect")
    before = paper.snapshot()
    wrong = deepcopy(before["reconciliation"])
    wrong["cash"] = "999999999"
    with pytest.raises(DomainError, match="mismatch"):
        paper.reconnect("wrong-reconnect", wrong)
    assert paper.snapshot()["portfolio"] == before["portfolio"]
    assert paper.snapshot()["orders"] == before["orders"]
    assert paper.snapshot()["connected"] is False


def test_conflicting_fill_halts_future_processing(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    before = push(paper, time, 2)
    fill = deepcopy(before["orders"][0]["fills"][0])
    fill["quantity"] = "999"
    with pytest.raises(DomainError):
        paper.confirm_fill(fill)
    assert paper.snapshot()["connected"] is False
    assert paper.snapshot()["portfolio"] == before["portfolio"]
    time.value = observation(3).end
    with pytest.raises(DomainError):
        paper.ingest("after-bad-fill", 3, observation(3))


def test_failed_sql_write_rolls_back_economics_and_allows_retry(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    before = push(paper, time, 1)
    with sqlite3.connect(paper.path) as db:
        db.execute(
            "CREATE TRIGGER fail_write BEFORE INSERT ON events BEGIN "
            "SELECT RAISE(ABORT, 'injected write failure'); END"
        )
    time.value = observation(2).end
    with pytest.raises(DomainError):
        paper.ingest("bar-2", 2, observation(2))
    assert paper.snapshot() == {key: value for key, value in before.items() if key != "duplicate"}
    with sqlite3.connect(paper.path) as db:
        db.execute("DROP TRIGGER fail_write")
    assert paper.ingest("bar-2", 2, observation(2))["orders"][0]["filled_quantity"] == "4"


@pytest.mark.parametrize(
    "column,value",
    [("payload", "{}"), ("projection", "bad"), ("event_id", "changed-id"), ("request_hash", "bad")],
)
def test_corrupted_journal_rejects_restart(tmp_path: Path, column: str, value: str) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    with sqlite3.connect(paper.path) as db:
        db.execute(f"UPDATE events SET {column}=? WHERE id=2", (value,))
    with pytest.raises(DomainError):
        PaperSession(paper.path, clock=LiveClock(time))


def test_concurrent_same_event_commits_only_one_fill_batch(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    other = PaperSession(paper.path, clock=LiveClock(time))
    other.reconnect("second-operator", other.snapshot()["reconciliation"])
    time.value = observation(2).end
    barrier = Barrier(2)

    def write(writer: PaperSession) -> dict[str, object]:
        barrier.wait(timeout=5)
        return writer.ingest("same-bar", 2, observation(2))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (paper, other)))
    assert sorted(result["duplicate"] for result in results) == [False, True]
    final = paper.snapshot()
    assert final["orders"][0]["filled_quantity"] == "4"
    assert len(final["orders"][0]["fills"]) == 1
    assert Decimal(final["portfolio"]["cash"]) == Decimal("9599")


def test_conflicting_duplicate_observation_halts(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    before = push(paper, time, 1)
    with pytest.raises(DomainError):
        paper.ingest("bar-1", 1, observation(1, "99"))
    after = paper.snapshot()
    assert after["connected"] is False
    assert after["portfolio"] == before["portfolio"]
    assert after["orders"] == before["orders"]


def test_restart_replay_never_reexecutes_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    before = push(paper, time, 2)

    def forbidden(*args: object) -> None:
        raise AssertionError("Strategy callback must not execute during durable replay")

    monkeypatch.setattr(BuyAndHold, "on_bar", forbidden)
    recovered = PaperSession(paper.path, clock=LiveClock(time))
    assert recovered.snapshot()["portfolio"] == before["portfolio"]
    assert recovered.snapshot()["orders"] == before["orders"]


def test_repeated_same_bad_fill_fault_is_durable_across_operators(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    before = push(paper, time, 2)
    wrong = deepcopy(before["orders"][0]["fills"][0])
    wrong["quantity"] = "999"
    with pytest.raises(DomainError):
        paper.confirm_fill(wrong)
    paper.reconnect("reconcile-first-fault", paper.snapshot()["reconciliation"])
    other = PaperSession(paper.path, clock=LiveClock(time))
    other.reconnect("other-after-reconcile", other.snapshot()["reconciliation"])
    with pytest.raises(DomainError):
        paper.confirm_fill(wrong)
    assert other.snapshot()["connected"] is False
    time.value = observation(3).end
    with pytest.raises(DomainError):
        other.ingest("other-must-halt", 3, observation(3))
    assert other.snapshot()["portfolio"] == before["portfolio"]


def test_deleted_committed_tail_rejects_restart(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    push(paper, time, 2)
    with sqlite3.connect(paper.path) as db:
        db.execute("DELETE FROM events WHERE id=(SELECT MAX(id) FROM events)")
    with pytest.raises(DomainError):
        PaperSession(paper.path, clock=LiveClock(time))


def test_corrupted_head_rejects_restart(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    with sqlite3.connect(paper.path) as db:
        db.execute("DELETE FROM head")
    with pytest.raises(DomainError):
        PaperSession(paper.path, clock=LiveClock(time))


def test_concurrent_distinct_ids_same_sequence_halt_without_second_fill(tmp_path: Path) -> None:
    paper, time = session(tmp_path)
    push(paper, time, 1)
    other = PaperSession(paper.path, clock=LiveClock(time))
    other.reconnect("operator-two", other.snapshot()["reconciliation"])
    time.value = observation(2).end
    barrier = Barrier(2)

    def write(pair: tuple[PaperSession, str]) -> object:
        writer, event_id = pair
        barrier.wait(timeout=5)
        try:
            return writer.ingest(event_id, 2, observation(2))
        except DomainError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ((paper, "writer-one"), (other, "writer-two"))))
    assert sum(isinstance(value, DomainError) for value in outcomes) == 1
    final = paper.snapshot()
    assert final["connected"] is False
    assert final["last_sequence"] == 2
    assert final["orders"][0]["filled_quantity"] == "4"
    assert len(final["orders"][0]["fills"]) == 1
    assert Decimal(final["portfolio"]["cash"]) == Decimal("9599")
