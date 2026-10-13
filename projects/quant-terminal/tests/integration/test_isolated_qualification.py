"""Preserve orphaned immutable artifacts without presenting completed research."""

import shutil
import sqlite3

import pytest
from test_isolated_jobs import PROJECT, setup

from quant_terminal.application.isolated_jobs import IsolatedJobScheduler
from quant_terminal.application.jobs import JobStore
from quant_terminal.application.service import ResearchService
from quant_terminal.experiments import IntegrityError


def fixture(tmp_path):
    scheduler, owners, package, request = setup(tmp_path)
    shutil.copytree(scheduler.research_root / "data", scheduler.root / "data")
    scheduler.research_root = scheduler.root
    return scheduler, owners, package, request, ResearchService(scheduler.root, PROJECT)


def test_publication_crash_preserves_artifact_but_blocks_public_reads(tmp_path, monkeypatch):
    scheduler, owners, package, request, service = fixture(tmp_path)
    original = scheduler.store.publish_after_cleanup

    class Crash(BaseException):
        pass

    def crash_after_registered(identity, publish):
        def partial_commit():
            publish()
            raise Crash

        return original(identity, partial_commit)

    monkeypatch.setattr(scheduler.store, "publish_after_cleanup", crash_after_registered)
    with scheduler:
        job = scheduler.submit(package, request)
        with pytest.raises(Crash):
            scheduler.run_next()
        assert owners[0].closed
        assert scheduler.store.get(job["id"])["status"] == "running"
        manifest = service.runtime.registry.search()[0]
        run_id = manifest.run_id.value
        preserved = service.runtime.registry.read_artifact(run_id, "report-bundle.zip")
        assert service.runs() == []
        for read in (
            lambda: service.run_section(run_id),
            lambda: service.run_section(run_id, "metrics"),
            lambda: service.artifact(run_id, "report-bundle.zip"),
            lambda: service.html_report(run_id),
            lambda: service.compare([run_id, run_id]),
        ):
            with pytest.raises(IntegrityError, match="completion is unconfirmed"):
                read()
    assert scheduler.store.get(job["id"])["status"] == "interrupted"
    replacement = IsolatedJobScheduler(
        scheduler.root, PROJECT, scheduler.owner_factory, research_root=scheduler.root
    )
    with replacement:
        assert replacement.run_next() is None
        assert service.runs() == []
        assert service.runtime.registry.read_artifact(run_id, "report-bundle.zip") == preserved
        assert owners[-1].backend.calls == 0


def test_confirmed_isolated_completion_remains_publicly_accessible(tmp_path):
    scheduler, _, package, request, service = fixture(tmp_path)
    with scheduler:
        job = scheduler.submit(package, request)
        completed = scheduler.run_next()
        run_id = completed["result"]["run_id"]
        assert completed["id"] == job["id"] and completed["status"] == "succeeded"
        assert service.runs()[0]["run_id"] == run_id
        assert service.run_section(run_id)["run_id"] == run_id
        assert service.html_report(run_id)
        scheduler.submit(package, request)
        repeated = scheduler.run_next()["result"]["run_id"]
        assert service.compare([run_id, repeated])["run_ids"] == [run_id, repeated]


def test_success_record_must_match_immutable_run_identity(tmp_path):
    scheduler, _, package, request, service = fixture(tmp_path)
    with scheduler:
        scheduler.submit(package, request)
        completed = scheduler.run_next()
        run_id = completed["result"]["run_id"]
        with JobStore(scheduler.store.root)._connection() as db:
            db.execute(
                "UPDATE jobs SET result=? WHERE id=?", ('{"run_id":"other"}', completed["id"])
            )
        assert service.runs() == []
        with pytest.raises(IntegrityError, match="completion is unconfirmed"):
            service.run_section(run_id)


def test_malformed_job_result_fails_closed(tmp_path):
    scheduler, _, package, request, service = fixture(tmp_path)
    with scheduler:
        scheduler.submit(package, request)
        completed = scheduler.run_next()
        with JobStore(scheduler.store.root)._connection() as db:
            db.execute("UPDATE jobs SET result='{' WHERE id=?", (completed["id"],))
        assert service.runs() == []
        with pytest.raises(IntegrityError, match="completion is unconfirmed"):
            service.run_section(completed["result"]["run_id"])


def test_public_read_does_not_initialize_missing_completion_schema(tmp_path):
    scheduler, _, package, request, service = fixture(tmp_path)
    with scheduler:
        scheduler.submit(package, request)
        completed = scheduler.run_next()
    path = scheduler.store.path
    path.rename(path.with_suffix(".preserved.sqlite"))
    sqlite3.connect(path).close()
    assert service.runs() == []
    with pytest.raises(IntegrityError, match="completion is unconfirmed"):
        service.html_report(completed["result"]["run_id"])
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_future_completion_schema_is_not_qualified(tmp_path):
    scheduler, _, package, request, service = fixture(tmp_path)
    with scheduler:
        scheduler.submit(package, request)
        completed = scheduler.run_next()
    with sqlite3.connect(scheduler.store.path) as db:
        db.execute("PRAGMA user_version=2")
    assert service.runs() == []
    with pytest.raises(IntegrityError, match="completion is unconfirmed"):
        service.run_section(completed["result"]["run_id"])
