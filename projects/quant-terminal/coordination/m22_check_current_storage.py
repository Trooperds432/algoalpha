"""ALG485: installed candidate offline recovery on an owned synthetic state copy."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT.parents[1] / "runtime"
CLEAN = RUNTIME / "m22-audited-fixed-clean-install-20261008"
PYTHON = CLEAN / ".venv/Scripts/python.exe"
ARCHIVE = RUNTIME / "quant-terminal-m22-20261008-audited-fixed.zip"
FIXTURE = PROJECT / "docs/validation/m22-real-gateway-20261008T195348Z.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Linked fixture path")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = digest(path)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-dir", type=Path, default=CLEAN)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    clean, fixture, archive = args.clean_dir, args.fixture, args.archive
    python = clean / ".venv/Scripts/python.exe"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence = PROJECT / "docs/validation" / f"m22-current-storage-20261009-{stamp}.json"
    owned = RUNTIME / f"m22-current-storage-{stamp}-{uuid4().hex[:8]}"
    original = Path(json.loads(fixture.read_text())["state"])
    report = {"at": datetime.now(UTC).isoformat(), "issue": "ALG485",
              "scope": "Offline installed candidate storage on copied synthetic state; no containment execution",
              "original": str(original), "owned_root": str(owned), "candidate": str(archive),
              "clean_install": str(clean), "fixture": str(fixture),
              "candidate_sha256": digest(archive), "final_release_approved": False,
              "commands": [], "result": "pending"}
    before = inventory(original)

    def run(args, expected=0):
        command = [str(python), "-m", "quant_terminal", *map(str, args)]
        result = subprocess.run(command, cwd=clean, capture_output=True, text=True, timeout=90)
        report["commands"].append({"args": list(map(str, args)), "exit_code": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == expected, result.stderr
        return json.loads(result.stdout) if result.stdout.strip() else None

    # No source import/execution: ResearchService only reads registry, qualification and reports.
    read_code = """
import hashlib,json,sys
from pathlib import Path
from quant_terminal.application.service import ResearchService
from quant_terminal.experiments import IntegrityError
service=ResearchService(Path(sys.argv[1]),Path.cwd())
visible=service.runs()
records=[]
for manifest in service.runtime.registry.search():
    identity=manifest.run_id.value
    record={'run_id':identity,'fingerprint':manifest.result_fingerprint,
            'report_sha256':hashlib.sha256(service.runtime.registry.read_artifact(identity,'report-bundle.zip')).hexdigest()}
    try:
        detail=service.run_section(identity)
        record['qualified']=True
        record['detail']=detail
    except IntegrityError:
        record['qualified']=False
    records.append(record)
print(json.dumps({'visible':visible,'records':records},sort_keys=True,default=str))
"""

    def read(root):
        result = subprocess.run([str(python), "-c", read_code, str(root)], cwd=clean,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    try:
        if archive == ARCHIVE:
            assert report["candidate_sha256"] == "20bf8fe18ee998cd17576cffb09d2ad935bca6a4b49c32a0eb0340c5cfc926bd"
        assert owned.resolve().parent == RUNTIME.resolve() and not owned.exists()
        owned.mkdir()
        copied = owned / "copied-state"
        shutil.copytree(original, copied)
        assert inventory(copied) == before
        original_view = read(copied)
        assert any(row["qualified"] for row in original_view["records"])
        backup = owned / "backup.zip"
        run(["--state-dir", copied, "backup", "--output", backup])
        restored = owned / "restored-state"
        run(["restore", "--archive", backup, "--destination", restored])
        report["backup_sha256"] = digest(backup)
        databases = [("jobs.sqlite", "jobs"), ("isolated-jobs/jobs.sqlite", "jobs"),
                     ("runs/experiments.sqlite3", "experiments")]
        logical = {}
        for relative, kind in databases:
            database = restored / relative
            if not database.exists():
                continue
            run(["database-inspect", "--database", database, "--kind", kind])
            with sqlite3.connect(database) as db:
                tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                logical[relative] = {table: db.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall() for table in tables}
                db.execute("PRAGMA user_version=0")
            migrated = run(["database-upgrade", "--database", database, "--kind", kind])
            assert migrated["schema_version"] == 1 and migrated["upgraded"]
            run(["database-inspect", "--database", database, "--kind", kind])
            with sqlite3.connect(database) as db:
                after = {table: db.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall() for table in logical[relative]}
            assert after == logical[relative]
        report["migrated_databases"] = list(logical)
        restored_view = read(restored)
        assert restored_view == original_view
        report["qualified_run_ids"] = [row["run_id"] for row in restored_view["records"] if row["qualified"]]
        report["unconfirmed_run_ids"] = [row["run_id"] for row in restored_view["records"] if not row["qualified"]]
        report["registry_reports_fingerprints_and_public_qualification_preserved"] = True
        report["read_view_sha256"] = hashlib.sha256(json.dumps(restored_view, sort_keys=True).encode()).hexdigest()
        immutable = {key: value for key, value in before.items() if key.startswith("runs/runs/") or key.startswith("data/")}
        assert all(digest(restored / key) == value for key, value in immutable.items())
        report["immutable_files_preserved"] = len(immutable)
        future = owned / "future-jobs.sqlite"
        shutil.copyfile(restored / "isolated-jobs/jobs.sqlite", future)
        with sqlite3.connect(future) as db:
            db.execute("PRAGMA user_version=2")
        future_before = digest(future)
        run(["database-upgrade", "--database", future, "--kind", "jobs"], expected=1)
        assert json.loads(report["commands"][-1]["stderr"])["error"] == "DomainError"
        assert digest(future) == future_before
        report["future_schema_rejected_without_mutation"] = True
        report["result"] = "passed"
    except Exception as exc:
        report.update(result="failed", failure_type=type(exc).__name__, failure_reason=str(exc)[:1500])
    finally:
        report["original_hashes_unchanged"] = inventory(original) == before
        report["original_file_count"] = len(before)
        report["helper_sha256"] = digest(Path(__file__))
        if not report["original_hashes_unchanged"]:
            report["result"] = "failed"
        evidence.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({"result": report["result"], "evidence": str(evidence), "failure": report.get("failure_reason")}))
    return int(report["result"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
