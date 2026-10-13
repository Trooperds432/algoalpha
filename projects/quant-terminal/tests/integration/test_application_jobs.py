"""Real process lifecycle and durable job state checks."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from quant_terminal.application.jobs import (
    TERMINAL,
    JobManager,
    JobStore,
    QueueFull,
    SchedulerLease,
)
from quant_terminal.core.exceptions import DomainError

PROJECT = Path(__file__).parents[2]


def wait_for(store, identity, expected=TERMINAL):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = store.get(identity)
        if job["status"] in expected:
            return job
        time.sleep(0.05)
    raise AssertionError("Job did not reach the expected state")


def test_durable_queue_capacity_cancel_recovery_events(tmp_path):
    store = JobStore(tmp_path, capacity=1)
    job = store.submit("backtest", {"fixture": "exact"})
    with pytest.raises(QueueFull):
        store.submit("backtest", {})
    assert store.cancel(job["id"])["status"] == "cancelled"
    next_job = store.submit("optimize", {})
    assert store.claim()["id"] == next_job["id"]
    store.record(next_job["id"], {"trial": 0, "status": "failed"})
    restarted = JobStore(tmp_path)
    restarted.recover()
    assert restarted.get(next_job["id"])["status"] == "interrupted"
    assert restarted.events(next_job["id"])[0]["event"]["status"] == "failed"
    restarted.finish(next_job["id"], "succeeded", result={"wrong": True})
    assert restarted.get(next_job["id"])["status"] == "interrupted"


def test_real_worker_failure_and_exclusive_scheduler(tmp_path):
    with JobManager(tmp_path, PROJECT, workers=1) as manager:
        with pytest.raises(DomainError, match="already owns"):
            with JobManager(tmp_path, PROJECT):
                pass
        job = manager.store.submit("backtest", {})
        outcome = wait_for(manager.store, job["id"])
        assert outcome["status"] == "failed"
        assert outcome["error"]["type"] == "DomainError"
    with JobManager(tmp_path, PROJECT):
        pass


@pytest.mark.parametrize("cancel", [False, True])
def test_stalled_worker_is_terminated_and_state_retained(tmp_path, monkeypatch, cancel):
    processes = []

    def launch(self, job):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        self.active[job["id"]] = process, time.monotonic()

    monkeypatch.setattr(JobManager, "_launch", launch)
    with JobManager(tmp_path, PROJECT, workers=1, timeout=1) as manager:
        job = manager.store.submit("backtest", {})
        wait_for(manager.store, job["id"], {"running"})
        if cancel:
            manager.store.cancel(job["id"])
        outcome = wait_for(manager.store, job["id"])
        assert outcome["status"] == ("cancelled" if cancel else "failed")
        assert outcome["error"]["type"] == ("Cancelled" if cancel else "Timeout")
    assert processes and all(process.poll() is not None for process in processes)


def test_cancellation_cannot_be_overwritten_by_worker_success(tmp_path):
    store = JobStore(tmp_path)
    job = store.submit("backtest", {})
    store.claim()
    store.cancel(job["id"])
    store.finish(job["id"], "succeeded", result={"late": True})
    assert store.get(job["id"])["status"] == "cancelling"


def test_scheduler_failure_is_visible_and_rejects_submission(tmp_path, monkeypatch):
    def broken_claim(self):
        raise OSError("fixture database unavailable")

    monkeypatch.setattr(JobStore, "claim", broken_claim)
    with JobManager(tmp_path, PROJECT) as manager:
        deadline = time.monotonic() + 5
        while manager.healthy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not manager.healthy and manager.failure == "OSError"
        with pytest.raises(DomainError, match="unavailable"):
            manager.submit("backtest", {})


def test_worker_parent_death_watchdog_terminates_orphan(tmp_path):
    # Child inherits the pipe: parent.communicate cannot see EOF until both exit.
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess,sys,os,time\n"
        "child = 'from quant_terminal.application.worker import _watch_parent; '"
        " + '_watch_parent(' + str(os.getpid()) + '); '"
        " + 'print(\"watching\",flush=True); import time; time.sleep(60)'\n"
        "subprocess.Popen([sys.executable,'-c',child])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert parent.stdout.readline().strip() == "watching"
        parent.kill()
        parent.communicate(timeout=5)
    finally:
        if parent.poll() is None:
            parent.kill()


def test_cleanup_keeps_lease_until_worker_exit_confirmed(tmp_path, monkeypatch):
    import threading

    allow_exit = threading.Event()
    cleanup_seen = threading.Event()

    class DelayedDeath:
        def poll(self):
            return 0 if allow_exit.is_set() else None

        def kill(self):
            cleanup_seen.set()
            if not allow_exit.is_set():
                raise OSError("temporary kill failure")

        def wait(self, timeout):
            if not allow_exit.is_set():
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

    def launch(self, job):
        self.active[job["id"]] = DelayedDeath(), time.monotonic()

    monkeypatch.setattr(JobManager, "_launch", launch)
    manager = JobManager(tmp_path, PROJECT).__enter__()
    try:
        job = manager.submit("backtest", {})
        wait_for(manager.store, job["id"], {"running"})
        manager.stop.set()
        assert cleanup_seen.wait(timeout=5)
        with pytest.raises(DomainError, match="already owns"):
            SchedulerLease(tmp_path / "scheduler.lock").acquire()
        assert manager.thread.is_alive()
    finally:
        allow_exit.set()
        manager.__exit__()
    assert manager.active == {}
