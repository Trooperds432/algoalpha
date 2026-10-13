"""Operational CLI preserves state while migrating and restoring offline copies."""

import json
import sqlite3
from pathlib import Path

import pytest

from quant_terminal.application.jobs import JobStore
from quant_terminal.cli import main
from quant_terminal.core.exceptions import DomainError


def test_offline_backup_restore_upgrade_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state"
    store = JobStore(state)
    job = store.submit("backtest", {"strategy": "buy_and_hold"})
    backup = tmp_path / "backup.zip"
    assert main(["--state-dir", str(state), "backup", "--output", str(backup)]) == 0
    assert json.loads(capsys.readouterr().out)
    restored = tmp_path / "restored"
    assert main(["restore", "--archive", str(backup), "--destination", str(restored)]) == 0
    assert json.loads(capsys.readouterr().out)
    assert JobStore(restored).get(str(job["id"]))["status"] == "queued"
    database = restored / "jobs.sqlite"
    assert main(["database-upgrade", "--database", str(database), "--kind", "jobs"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1
    assert JobStore(restored).get(str(job["id"]))["status"] == "queued"


def test_future_jobs_schema_refuses_before_mutation(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(DomainError, match="schema"):
        JobStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_cli_backup_passes_original_path_for_link_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import quant_terminal.cli as cli

    original = tmp_path / "missing" / ".." / "state"
    seen: list[Path] = []

    def capture(source: Path, output: Path) -> dict[str, object]:
        seen.append(source)
        return {"checked": True}

    monkeypatch.setattr(cli, "backup_state", capture)
    assert main(["--state-dir", str(original), "backup", "--output", str(tmp_path / "b.zip")]) == 0
    assert seen == [original]
