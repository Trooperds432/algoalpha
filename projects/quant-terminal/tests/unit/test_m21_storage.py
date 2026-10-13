from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.release.storage import (
    _acquire_scheduler_probe,
    backup_state,
    inspect_schema,
    restore_state,
    upgrade_schema,
)


def _jobs_db(path: Path, *, version: int = 0, valid: bool = True) -> None:
    with sqlite3.connect(path) as db:
        if valid:
            db.executescript(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, "
                "request TEXT NOT NULL, result TEXT, error TEXT, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL);"
                "CREATE TABLE events (seq INTEGER PRIMARY KEY, job_id TEXT NOT NULL, "
                "payload TEXT NOT NULL);"
            )
            db.execute(
                "INSERT INTO jobs VALUES ('j1','backtest','succeeded','{}',NULL,NULL,'t','t')"
            )
            db.execute("INSERT INTO events VALUES (1,'j1','{}')")
        db.execute(f"PRAGMA user_version={version}")


def test_migration_upgrades_v0_transactionally_and_preserves_rows(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite"
    _jobs_db(path)
    assert inspect_schema(path, "jobs")["needs_upgrade"] is True
    result = upgrade_schema(path, "jobs")
    assert result["schema_version"] == 1
    assert result["upgraded"] is True
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,status FROM jobs").fetchall() == [("j1", "succeeded")]
        assert db.execute("SELECT payload FROM events").fetchall() == [("{}",)]
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
    assert upgrade_schema(path, "jobs")["upgraded"] is False


def test_migration_rejects_newer_version_and_corrupt_schema_without_mutation(
    tmp_path: Path,
) -> None:
    newer = tmp_path / "newer.sqlite"
    _jobs_db(newer, version=2)
    with pytest.raises(DomainError):
        upgrade_schema(newer, "jobs")
    with sqlite3.connect(newer) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)

    corrupt = tmp_path / "corrupt.sqlite"
    _jobs_db(corrupt, version=0, valid=False)
    with pytest.raises(DomainError):
        upgrade_schema(corrupt, "jobs")
    with sqlite3.connect(corrupt) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)


def test_inspection_rejects_corrupt_database_and_unknown_kind(tmp_path: Path) -> None:
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not sqlite")
    with pytest.raises(DomainError):
        inspect_schema(broken, "jobs")
    valid = tmp_path / "jobs.sqlite"
    _jobs_db(valid)
    with pytest.raises(DomainError):
        inspect_schema(valid, "unknown")  # type: ignore[arg-type]


def test_backup_restore_snapshots_database_and_excludes_scheduler_lock(tmp_path: Path) -> None:
    state = tmp_path / "runtime-state"
    state.mkdir()
    (state / "scheduler.lock").write_bytes(b"stale lock marker")
    (state / "settings.json").write_text('{"mode":"research"}', encoding="utf-8")
    _jobs_db(state / "jobs.sqlite")
    archive = tmp_path / "state.zip"
    result = backup_state(state, archive)
    assert "caller stops all writers" in result["offline_precondition"]
    with zipfile.ZipFile(archive) as zipped:
        assert "scheduler.lock" not in zipped.namelist()
        assert "jobs.sqlite" in zipped.namelist()
        assert "settings.json" in zipped.namelist()
    restored = tmp_path / "restored-state"
    assert restore_state(archive, restored)["files"] == 2
    assert (restored / "settings.json").read_text(encoding="utf-8") == '{"mode":"research"}'
    with sqlite3.connect(restored / "jobs.sqlite") as db:
        assert db.execute("SELECT id FROM jobs").fetchall() == [("j1",)]
    assert not (restored / "scheduler.lock").exists()


def test_backup_holds_scheduler_lock_and_rejects_active_scheduler(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    lock = _acquire_scheduler_probe(state)
    try:
        with pytest.raises(DomainError, match="scheduler is active"):
            backup_state(state, tmp_path / "busy.zip")
    finally:
        lock.close()


def test_backup_never_overwrites_archive_and_rejects_sqlite_sidecars(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    archive = tmp_path / "state.zip"
    archive.write_bytes(b"keep")
    with pytest.raises(DomainError):
        backup_state(state, archive)
    assert archive.read_bytes() == b"keep"
    archive.unlink()
    (state / "jobs.sqlite-wal").write_bytes(b"pending")
    with pytest.raises(DomainError, match="sidecar"):
        backup_state(state, archive)


def test_backup_rejects_corrupt_database_instead_of_archiving_as_opaque_file(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "jobs.sqlite").write_bytes(b"not a database")
    with pytest.raises(DomainError, match="SQLite"):
        backup_state(state, tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()


def test_restore_refuses_existing_destination_and_preserves_it(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    archive = tmp_path / "state.zip"
    backup_state(state, archive)
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep.txt").write_text("unchanged", encoding="utf-8")
    with pytest.raises(DomainError):
        restore_state(archive, destination)
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "unchanged"


def test_restore_rejects_zip_slip_and_does_not_create_destination(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    payload = b"owned"
    manifest = {
        "format_version": 1,
        "schema_version": 1,
        "files": [
            {
                "path": "../escaped.txt",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escaped.txt", payload)
        zipped.writestr("manifest.json", json.dumps(manifest))
    destination = tmp_path / "restored"
    with pytest.raises(DomainError):
        restore_state(archive, destination)
    assert not destination.exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_rejects_manifest_hash_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "bad-hash.zip"
    manifest = {
        "format_version": 1,
        "schema_version": 1,
        "files": [{"path": "a.txt", "size": 3, "sha256": "0" * 64}],
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("a.txt", b"abc")
        zipped.writestr("manifest.json", json.dumps(manifest))
    destination = tmp_path / "restored"
    with pytest.raises(DomainError):
        restore_state(archive, destination)
    assert not destination.exists()


def test_restore_rejects_duplicate_casefolded_and_symlink_entries(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    payload = b"x"
    records = [
        {"path": name, "size": 1, "sha256": hashlib.sha256(payload).hexdigest()}
        for name in ("A.txt", "a.txt")
    ]
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("A.txt", payload)
        zipped.writestr("a.txt", payload)
        zipped.writestr(
            "manifest.json",
            json.dumps({"format_version": 1, "schema_version": 1, "files": records}),
        )
    with pytest.raises(DomainError):
        restore_state(archive, tmp_path / "duplicate-restore")

    symlink_archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (0o120777 << 16) | 0xA000
    with zipfile.ZipFile(symlink_archive, "w") as zipped:
        zipped.writestr(info, "target")
        zipped.writestr(
            "manifest.json",
            json.dumps({"format_version": 1, "schema_version": 1, "files": []}),
        )
    with pytest.raises(DomainError):
        restore_state(symlink_archive, tmp_path / "symlink-restore")


def test_restore_rejects_windows_filename_aliases_before_any_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "windows-alias.zip"
    payload = b"first"
    names = ("state", "state.")
    manifest = {
        "format_version": 1,
        "schema_version": 1,
        "files": [
            {"path": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for name in names
        ],
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        for name in names:
            zipped.writestr(name, payload)
        zipped.writestr("manifest.json", json.dumps(manifest))
    destination = tmp_path / "alias-restore"
    with pytest.raises(DomainError):
        restore_state(archive, destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "name",
    ["state.", "state ", "CON", "con.txt", "folder/LPT1.data", "bad\x01name", "bad\x7fname"],
)
def test_restore_rejects_windows_unsafe_path_segments(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe-name.zip"
    data = b"x"
    manifest = {
        "format_version": 1,
        "schema_version": 1,
        "files": [{"path": name, "size": 1, "sha256": hashlib.sha256(data).hexdigest()}],
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(name, data)
        zipped.writestr("manifest.json", json.dumps(manifest))
    destination = tmp_path / "unsafe-restore"
    with pytest.raises(DomainError):
        restore_state(archive, destination)
    assert not destination.exists()
