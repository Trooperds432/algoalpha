"""Durable operator queue lifecycle with a controlled protocol collaborator."""

import hashlib
import threading
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.application.isolated_jobs import IsolatedJobScheduler
from quant_terminal.application.jobs import JobStore, QueueFull, SchedulerLease
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.experiments import ExperimentStore
from quant_terminal.sandbox.protocol import SandboxBudget, SandboxResponse, decode_request

PROJECT = Path(__file__).parents[2]


class Backend:
    def __init__(self):
        self.entered = threading.Event()
        self.block = False
        self.live = False
        self.calls = 0

    def identity(self):
        return "controlled-backend"

    def execute(self, blob, cancel=None, *, expected_identity=None):
        assert expected_identity == self.identity()
        self.live = True
        self.calls += 1
        self.entered.set()
        try:
            if self.block:
                assert cancel is not None and cancel.wait(10)
                raise DomainError("controlled cancellation")
            request = decode_request(blob)
            return SandboxResponse(request.job_id, "ok", ())
        finally:
            self.live = False


class Owner:
    def __init__(self, root, backend):
        self.root, self.backend = root, backend
        self.ready = False
        self.close_failed = False
        self.closed = False
        self.before_close = None

    def start(self):
        self.ready = True

    def ensure_available(self):
        if not self.ready or self.closed:
            raise DomainError("owner unavailable")

    def close(self):
        assert not self.backend.live
        if self.before_close:
            self.before_close()
        if self.close_failed:
            raise DomainError("cleanup not proven")
        self.closed = True
        self.ready = False


def setup(tmp_path, *, capacity=8, timeout=120):
    root = tmp_path / "state"
    runtime = ResearchRuntime(root / "isolated-research", PROJECT)
    instrument = InstrumentMeta(
        InstrumentId("ETF-1"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal(".01"),
        Decimal(1),
    )
    snapshot = runtime.data.import_csv(
        PROJECT / "examples/data/synthetic_xnys_2026.csv",
        instrument=instrument,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    owners = []

    def factory():
        owner = Owner(root, Backend())
        owners.append(owner)
        return owner

    scheduler = IsolatedJobScheduler(root, PROJECT, factory, capacity=capacity, timeout=timeout)
    source = "opaque invalid Python never evaluated on host"
    package = {
        "source": source,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "dependencies": [],
    }
    request = {
        "instrument_ids": ["ETF-1"],
        "budget": asdict(SandboxBudget(5, 3, 64_000_000, 4, 32, 0, 0, 4096)),
        "snapshot_ids": [snapshot.snapshot_id],
        "start": "2026-11-25T14:30:00+00:00",
        "end": "2026-11-28T00:00:00+00:00",
        "currency": "USD",
        "initial_capital": "10000",
        "execution": {"commission_per_unit": ".1"},
        "seed": 41,
    }
    return scheduler, owners, package, request


def test_queue_capacity_serial_execution_and_cleanup_before_publication(tmp_path):
    scheduler, owners, package, request = setup(tmp_path, capacity=2)
    with scheduler:
        first = scheduler.submit(package, request)
        second = scheduler.submit(package, request)
        with pytest.raises(QueueFull):
            scheduler.submit(package, request)
        owners[0].before_close = lambda: assert_no_runs(scheduler)
        completed = scheduler.run_next()
        assert completed["id"] == first["id"] and completed["status"] == "succeeded"
        assert owners[0].closed and owners[0].backend.calls == 2
        assert scheduler.run_next()["id"] == second["id"]
        assert scheduler.store.get(second["id"])["status"] == "succeeded"
        assert len(ExperimentStore(scheduler.root / "isolated-research/runs").search()) == 2
    assert all(o.closed for o in owners)


def assert_no_runs(scheduler):
    assert not ExperimentStore(scheduler.root / "isolated-research/runs").search()


def test_cancelled_pending_source_never_executes(tmp_path):
    scheduler, owners, package, request = setup(tmp_path)
    with scheduler:
        job = scheduler.submit(package, request)
        assert scheduler.cancel(job["id"])["status"] == "cancelled"
        assert scheduler.run_next() is None
        assert owners[0].backend.calls == 0
        assert_no_runs(scheduler)


def test_active_cancel_signals_backend_and_only_finishes_after_cleanup(tmp_path):
    scheduler, owners, package, request = setup(tmp_path)
    with scheduler:
        owners[0].backend.block = True
        job = scheduler.submit(package, request)
        results = []
        thread = threading.Thread(target=lambda: results.append(scheduler.run_next()))
        thread.start()
        assert owners[0].backend.entered.wait(10)
        scheduler.cancel(job["id"])
        thread.join(10)
        assert not thread.is_alive()
        assert results[0]["status"] == "cancelled"
        assert owners[0].closed and not owners[0].backend.live
        assert_no_runs(scheduler)


def test_overall_timeout_cancels_protocol_and_does_not_publish(tmp_path):
    scheduler, owners, package, request = setup(tmp_path, timeout=1)
    with scheduler:
        owners[0].backend.block = True
        scheduler.submit(package, request)
        result = scheduler.run_next()
        assert result["status"] == "failed" and result["error"]["type"] == "Timeout"
        assert owners[0].closed and not owners[0].backend.live
        assert_no_runs(scheduler)


def test_cleanup_failure_retains_nonterminal_job_and_exclusive_lease(tmp_path):
    scheduler, owners, package, request = setup(tmp_path)
    scheduler.__enter__()
    try:
        job = scheduler.submit(package, request)
        owners[0].close_failed = True
        with pytest.raises(DomainError, match="cleanup"):
            scheduler.run_next()
        assert scheduler.store.get(job["id"])["status"] == "running"
        assert_no_runs(scheduler)
        with pytest.raises(DomainError):
            SchedulerLease(scheduler.lease.path).acquire()
        with pytest.raises(DomainError, match="unavailable"):
            scheduler.submit(package, request)
    finally:
        owners[0].close_failed = False
        scheduler.close()
    assert scheduler.store.get(job["id"])["status"] == "interrupted"
    lease = SchedulerLease(scheduler.lease.path)
    lease.acquire()
    lease.release()


def test_restart_recovers_interrupted_source_without_rerunning(tmp_path):
    scheduler, owners, package, request = setup(tmp_path)
    with scheduler:
        job = scheduler.submit(package, request)
        assert scheduler.store.claim()["id"] == job["id"]
    replacement = IsolatedJobScheduler(scheduler.root, PROJECT, scheduler.owner_factory)
    with replacement:
        assert replacement.store.get(job["id"])["status"] == "interrupted"
        assert replacement.run_next() is None
    assert all(o.backend.calls == 0 for o in owners)
    assert_no_runs(scheduler)


def test_shutdown_cancels_active_job_and_reaps_before_releasing_lease(tmp_path):
    scheduler, owners, package, request = setup(tmp_path)
    scheduler.__enter__()
    owners[0].backend.block = True
    job = scheduler.submit(package, request)
    thread = threading.Thread(target=scheduler.run_next)
    thread.start()
    assert owners[0].backend.entered.wait(10)
    scheduler.close(timeout=10)
    thread.join(10)
    assert not thread.is_alive() and owners[0].closed
    assert scheduler.store.get(job["id"])["status"] == "interrupted"
    assert_no_runs(scheduler)


def test_bad_source_or_mode_cannot_enter_either_queue(tmp_path):
    scheduler, _, package, request = setup(tmp_path)
    with scheduler:
        with pytest.raises(DomainError):
            scheduler.submit({**package, "source_sha256": "0" * 64}, request)
        with pytest.raises(DomainError):
            scheduler.submit(package, {**request, "verified": True})
        assert not scheduler.store.list()
    trusted = JobStore(tmp_path / "trusted")
    with pytest.raises(DomainError):
        trusted.submit("isolated-backtest", {})
    assert not trusted.list()


def test_independent_cancel_before_publication_wins_without_artifact(tmp_path, monkeypatch):
    scheduler, _, package, request = setup(tmp_path)
    with scheduler:
        job = scheduler.submit(package, request)
        original = scheduler.store.publish_after_cleanup

        def independent_cancel(identity, publish):
            JobStore(scheduler.store.root).cancel(identity)
            return original(identity, publish)

        monkeypatch.setattr(scheduler.store, "publish_after_cleanup", independent_cancel)
        result = scheduler.run_next()
        assert result["id"] == job["id"] and result["status"] == "cancelled"
        assert scheduler._active is None
        assert_no_runs(scheduler)


def test_publication_commit_excludes_independent_cancel_write(tmp_path, monkeypatch):
    scheduler, _, package, request = setup(tmp_path)
    with scheduler:
        job = scheduler.submit(package, request)
        entered, cancel_started, cancel_finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        original = scheduler.store.cancel
        outcomes = []

        def cancel():
            assert entered.wait(10)
            cancel_started.set()
            outcomes.append(JobStore(scheduler.store.root).cancel(job["id"]))
            cancel_finished.set()

        import quant_terminal.application.isolated_jobs as module

        register = module.IsolatedResearchRuntime._register

        def publishing(runtime, manifest, result, artifacts):
            entered.set()
            assert cancel_started.wait(10)
            assert not cancel_finished.wait(0.2)
            return register(runtime, manifest, result, artifacts)

        monkeypatch.setattr(module.IsolatedResearchRuntime, "_register", publishing)
        thread = threading.Thread(target=cancel)
        thread.start()
        completed = scheduler.run_next()
        thread.join(10)
        assert not thread.is_alive() and cancel_finished.is_set()
        assert completed["status"] == outcomes[0]["status"] == "succeeded"
        assert original(job["id"])["status"] == "succeeded"
        assert len(ExperimentStore(scheduler.root / "isolated-research/runs").search()) == 1
