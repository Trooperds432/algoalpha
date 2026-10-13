"""Offline, bounded backup and schema-version primitives for local runtime state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Literal

from quant_terminal.core.exceptions import DomainError

DatabaseKind = Literal["jobs", "experiments", "paper", "risk"]
_SCHEMA_VERSION = 1
_MAX_FILES = 20_000
_MAX_FILE_BYTES = 128 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MANIFEST = "manifest.json"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TABLE_COLUMNS: dict[str, dict[str, frozenset[str]]] = {
    "jobs": {
        "jobs": frozenset(
            {"id", "kind", "status", "request", "result", "error", "created_at", "updated_at"}
        ),
        "events": frozenset({"seq", "job_id", "payload"}),
    },
    "experiments": {
        "runs": frozenset(
            {
                "run_id",
                "created_at",
                "strategy_id",
                "input_fingerprint",
                "result_fingerprint",
                "manifest",
                "manifest_sha256",
            }
        ),
        "artifacts": frozenset({"run_id", "name", "sha256", "size_bytes"}),
        "tag_events": frozenset({"event_id", "run_id", "tag", "action", "created_at"}),
    },
    "paper": {
        "settings": frozenset({"payload", "digest"}),
        "head": frozenset({"count", "digest"}),
        "events": frozenset({"id", "event_id", "request_hash", "payload", "digest", "projection"}),
    },
    "risk": {
        "risk_reservations": frozenset(
            {
                "order_id",
                "state",
                "instrument_id",
                "currency",
                "side",
                "quantity",
                "reference_price",
                "reserved_cash",
                "created_at",
                "updated_at",
                "resolution_source",
                "filled_quantity",
                "reconciled_quantity",
                "order_type",
                "limit_price",
            }
        ),
        "risk_provider_bindings": frozenset(
            {
                "local_order_id",
                "broker_order_id",
                "provider_order_id",
                "ticker",
                "instrument_id",
                "side",
                "quantity",
                "limit_price",
                "mapped_at",
            }
        ),
        "risk_fills": frozenset({"fill_id", "order_id", "quantity", "filled_at"}),
        "risk_reconciliations": frozenset(
            {
                "reconciliation_id",
                "order_id",
                "cumulative_filled_quantity",
                "reconciled_at",
                "source",
            }
        ),
        "risk_control": frozenset(
            {"singleton", "kill_switch_active", "updated_at", "operator", "reason"}
        ),
        "kill_switch_events": frozenset({"event_id", "active", "changed_at", "operator", "reason"}),
    },
}


def _safe_path(path: Path) -> Path:
    absolute = path.absolute()
    for part in (absolute, *absolute.parents):
        if part.is_symlink() or part.is_junction():
            raise DomainError("Storage paths cannot contain symlinks or junctions")
    return absolute


def _require_state_root(path: Path) -> Path:
    root = _safe_path(path)
    if not root.exists() or not root.is_dir():
        raise DomainError("State root must be an existing directory")
    resolved = root.resolve()
    if resolved == _PROJECT_ROOT or resolved.is_relative_to(_PROJECT_ROOT):
        raise DomainError("Runtime state root must be outside the project source tree")
    return resolved


def _acquire_scheduler_probe(root: Path) -> object:
    """Lock scheduler.lock for the duration; a stale unlocked file is harmless."""
    lock_path = _safe_path(root / "scheduler.lock")
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise DomainError("Cannot back up state while the research scheduler is active") from exc
    return handle


def _release_scheduler_probe(handle: object) -> None:
    # Closing releases both the Windows byte-range lock and POSIX flock.
    close = getattr(handle, "close", None)
    if callable(close):
        close()


def _check_sqlite(path: Path) -> None:
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if integrity != [("ok",)]:
                raise DomainError(f"SQLite integrity check failed: {path.name}")
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign:
                raise DomainError(f"SQLite foreign key check failed: {path.name}")
    except sqlite3.Error as exc:
        raise DomainError(f"Invalid or busy SQLite state database: {path.name}") from exc


def _relative_name(path: Path, root: Path) -> str:
    name = path.relative_to(root).as_posix()
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or "\\" in name or ":" in name:
        raise DomainError("State contains an unsafe relative path")
    return name


def _archive_parts(name: str) -> tuple[str, ...]:
    """Validate one canonical archive path under both POSIX and Windows names."""
    pure = PurePosixPath(name)
    parts = pure.parts
    if (
        not name
        or pure.as_posix() != name
        or pure.is_absolute()
        or "\\" in name
        or ":" in name
        or any(part in ("", ".", "..") for part in parts)
        or any(
            part.endswith((".", " "))
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in parts
        )
    ):
        raise DomainError("Backup contains an unsafe cross-platform path")
    return parts


def _state_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    total = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in tuple(dirnames):
            child = _safe_path(base / name)
            if child.is_symlink() or child.is_junction():
                raise DomainError("State contains a linked directory")
            if not child.is_dir():
                raise DomainError("State contains a non-directory path component")
        for name in filenames:
            path = _safe_path(base / name)
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise DomainError("State contains a non-regular file")
            if path == root / "scheduler.lock":
                continue
            if name.endswith(("-wal", "-shm", "-journal")):
                raise DomainError("Offline backup refuses SQLite sidecar files")
            size = path.stat().st_size
            if size > _MAX_FILE_BYTES:
                raise DomainError(f"State file exceeds {_MAX_FILE_BYTES} bytes")
            total += size
            if total > _MAX_TOTAL_BYTES:
                raise DomainError("State exceeds the total backup size bound")
            files.append(path)
            if len(files) > _MAX_FILES:
                raise DomainError("State exceeds the backup file-count bound")
    return tuple(sorted(files))


def _sqlite_snapshot(source: Path, target: Path) -> None:
    _check_sqlite(source)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst, pages=256, sleep=0.01)
        _check_sqlite(target)
    except sqlite3.Error as exc:
        raise DomainError(f"Unable to snapshot SQLite database: {source.name}") from exc


def backup_state(source_root: Path, archive_path: Path) -> dict[str, object]:
    """Create an offline archive with database snapshots; final archive never overwrites."""
    root = _require_state_root(Path(source_root))
    destination = _safe_path(Path(archive_path))
    if destination.exists():
        raise DomainError("Backup archive already exists")
    if not destination.parent.exists() or not destination.parent.is_dir():
        raise DomainError("Backup archive parent must already exist")
    lock = _acquire_scheduler_probe(root)
    temporary: Path | None = None
    try:
        source_files = _state_files(root)
        stage = Path(tempfile.mkdtemp(prefix=".quant-backup-", dir=destination.parent))
        temporary = stage / "archive.zip"
        manifest_files: list[dict[str, object]] = []
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for source in source_files:
                relative = _relative_name(source, root)
                staged = stage / "files" / Path(*PurePosixPath(relative).parts)
                staged.parent.mkdir(parents=True, exist_ok=True)
                is_database = source.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
                if is_database or source.read_bytes()[:16] == b"SQLite format 3\x00":
                    _sqlite_snapshot(source, staged)
                else:
                    shutil.copyfile(source, staged)
                data_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
                size = staged.stat().st_size
                manifest_files.append({"path": relative, "size": size, "sha256": data_hash})
                archive.write(staged, arcname=relative)
            manifest = {
                "format_version": 1,
                "schema_version": _SCHEMA_VERSION,
                "files": manifest_files,
            }
            archive.writestr(
                _MANIFEST,
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
        if temporary.stat().st_size > _MAX_ARCHIVE_BYTES:
            raise DomainError("Backup archive exceeds the archive size bound")
        # A same-filesystem hard link gives atomic create-if-absent semantics.
        os.link(temporary, destination)
        return {
            "archive": str(destination),
            "files": len(manifest_files),
            "size_bytes": destination.stat().st_size,
            "format_version": 1,
            "offline_precondition": "caller stops all writers; scheduler lock is checked",
        }
    except OSError as exc:
        raise DomainError("Unable to create backup archive without overwriting") from exc
    finally:
        _release_scheduler_probe(lock)
        if temporary is not None:
            shutil.rmtree(temporary.parent, ignore_errors=True)


def _archive_manifest(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, object], dict[str, zipfile.ZipInfo]]:
    infos = archive.infolist()
    if not infos or len(infos) > _MAX_FILES + 1:
        raise DomainError("Backup archive entry count is outside bounds")
    entries: dict[str, zipfile.ZipInfo] = {}
    folded_names: set[str] = set()
    file_path_keys: set[tuple[str, ...]] = set()
    parent_path_keys: set[tuple[str, ...]] = set()
    total = 0
    for info in infos:
        name = info.filename
        parts = _archive_parts(name)
        mode = info.external_attr >> 16
        name_key = name.casefold()
        path_key = tuple(part.casefold() for part in parts)
        mode_type = stat.S_IFMT(mode)
        if (
            stat.S_ISLNK(mode)
            or mode_type not in (0, stat.S_IFREG)
            or info.is_dir()
            or name_key in folded_names
            or path_key in parent_path_keys
        ):
            raise DomainError("Backup contains an unsafe or duplicate archive path")
        if any(path_key[:depth] in file_path_keys for depth in range(1, len(path_key))):
            raise DomainError("Backup file path collides with another entry")
        if info.file_size > _MAX_FILE_BYTES:
            raise DomainError("Backup entry exceeds the per-file size bound")
        if info.file_size and info.compress_size == 0:
            raise DomainError("Backup entry has an invalid compressed size")
        if info.compress_size and info.file_size / info.compress_size > 1000:
            raise DomainError("Backup entry compression ratio exceeds the safety bound")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise DomainError("Backup expanded size exceeds the total size bound")
        entries[name] = info
        folded_names.add(name_key)
        file_path_keys.add(path_key)
        parent_path_keys.update(path_key[:depth] for depth in range(1, len(path_key)))
    if _MANIFEST not in entries:
        raise DomainError("Backup manifest is missing")
    info = entries[_MANIFEST]
    if info.file_size > 2_000_000:
        raise DomainError("Backup manifest is too large")
    try:
        manifest = json.loads(archive.read(info))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise DomainError("Backup manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"format_version", "schema_version", "files"}
        or manifest["format_version"] != 1
        or manifest["schema_version"] != _SCHEMA_VERSION
        or not isinstance(manifest["files"], list)
    ):
        raise DomainError("Unsupported backup manifest schema")
    records: dict[str, tuple[int, str]] = {}
    folded_records: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise DomainError("Invalid backup file manifest record")
        path, size, digest = item["path"], item["size"], item["sha256"]
        if (
            not isinstance(path, str)
            or path == _MANIFEST
            or type(size) is not int
            or not 0 <= size <= _MAX_FILE_BYTES
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or path.casefold() in folded_records
        ):
            raise DomainError("Invalid backup file manifest values")
        _archive_parts(path)
        records[path] = (size, digest)
        folded_records.add(path.casefold())
    if set(entries) != set(records) | {_MANIFEST}:
        raise DomainError("Backup entries do not match the manifest")
    return manifest, entries


def restore_state(archive_path: Path, destination_root: Path) -> dict[str, object]:
    """Validate into sibling staging then atomically promote into an absent destination."""
    source = _safe_path(Path(archive_path))
    destination = _safe_path(Path(destination_root))
    if not source.exists() or not source.is_file() or source.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise DomainError("Backup archive is missing, non-regular, or too large")
    if destination.exists():
        raise DomainError("Restore destination must not already exist")
    if not destination.parent.exists() or not destination.parent.is_dir():
        raise DomainError("Restore destination parent must already exist")
    if destination.resolve().is_relative_to(_PROJECT_ROOT):
        raise DomainError("Runtime state restore destination must be outside project source")
    staging = Path(tempfile.mkdtemp(prefix=".quant-restore-", dir=destination.parent))
    try:
        try:
            with zipfile.ZipFile(source, "r") as zipped:
                _, entries = _archive_manifest(zipped)
                records = {
                    item["path"]: item for item in json.loads(zipped.read(_MANIFEST))["files"]
                }
                for name, item in records.items():
                    relative = PurePosixPath(name)
                    target = _safe_path(staging.joinpath(*relative.parts))
                    if not target.is_relative_to(staging):
                        raise DomainError("Backup path escapes restore staging")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = zipped.read(entries[name])
                    if (
                        len(data) != item["size"]
                        or hashlib.sha256(data).hexdigest() != item["sha256"]
                    ):
                        raise DomainError("Backup file hash or size does not match manifest")
                    target.write_bytes(data)
                for path in (p for p in staging.rglob("*") if p.is_file()):
                    if path.read_bytes()[:16] == b"SQLite format 3\x00":
                        _check_sqlite(path)
        except (zipfile.BadZipFile, OSError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, DomainError):
                raise
            raise DomainError("Backup archive failed validation") from exc
        os.rename(staging, destination)
        return {
            "destination": str(destination),
            "files": len(records),
            "format_version": 1,
            "offline_precondition": "restore to a new state root before starting services",
        }
    except OSError as exc:
        raise DomainError("Unable to atomically promote restored state") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def inspect_schema(database_path: Path, database_kind: DatabaseKind) -> dict[str, object]:
    """Validate current table shapes and report version without modifying the database."""
    if database_kind not in _TABLE_COLUMNS:
        raise DomainError("Unsupported database kind")
    path = _safe_path(Path(database_path))
    if not path.exists() or not path.is_file():
        raise DomainError("Database path must identify an existing regular file")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 0 or version > _SCHEMA_VERSION:
                raise DomainError("Database schema is newer than this application supports")
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise DomainError("Database integrity check failed")
            for table, required in _TABLE_COLUMNS[database_kind].items():
                columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")').fetchall()}
                if not required <= columns:
                    raise DomainError(
                        f"Database is missing required {database_kind}.{table} columns"
                    )
    except sqlite3.Error as exc:
        raise DomainError("Database is corrupt or unreadable") from exc
    return {
        "database_kind": database_kind,
        "schema_version": version,
        "supported_version": _SCHEMA_VERSION,
        "needs_upgrade": version < _SCHEMA_VERSION,
    }


def upgrade_schema(database_path: Path, database_kind: DatabaseKind) -> dict[str, object]:
    """Mark a validated legacy v0 database as current in one SQLite transaction."""
    path = _safe_path(Path(database_path))
    if not path.exists() or not path.is_file():
        raise DomainError("Database path must identify an existing regular file")
    before = inspect_schema(path, database_kind)
    if before["schema_version"] == _SCHEMA_VERSION:
        return {**before, "upgraded": False}
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None)
        ) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] != 0:
                raise DomainError("Database schema version changed during upgrade")
            # Migrations are application-owned and versioned; no caller SQL is accepted.
            db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            db.commit()
    except sqlite3.Error as exc:
        raise DomainError("Database schema upgrade failed and was rolled back") from exc
    after = inspect_schema(path, database_kind)
    return {**after, "upgraded": True}
