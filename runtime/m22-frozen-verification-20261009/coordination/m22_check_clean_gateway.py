"""Verify the new candidate's own environment and bounded isolated replay."""
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CLEAN = (Path(sys.argv[1]) if len(sys.argv) > 1 else
         PROJECT.parents[1] / "runtime/m22-gateway-clean-install-20261008")
PYTHON = CLEAN / ".venv/Scripts/python.exe"


def main():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = PROJECT / "docs/validation" / ("m22-clean-gateway-" + stamp + ".json")
    fixture_path = (Path(sys.argv[2]) if len(sys.argv) > 2 else
                    PROJECT / "docs/validation/m22-real-gateway-20261008T121544Z.json")
    fixture = json.loads(fixture_path.read_text())
    archive = (Path(sys.argv[3]) if len(sys.argv) > 3 else
               CLEAN.parent / "quant-terminal-m22-20261008-gateway.zip")
    state = Path(fixture["state"])
    report = {"at": datetime.now(UTC).isoformat(), "clean_install": str(CLEAN),
              "archive": str(archive),
              "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "final_release": False, "result": "pending"}
    failed = False
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=3) as response:
                    assert response.status == 200
                break
            except (OSError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        record = {}
        for path in ("/health", "/ready", "/sandbox/capabilities", "/ui/"):
            with urllib.request.urlopen("http://127.0.0.1:8766" + path, timeout=15) as response:
                body = response.read()
                assert response.status == 200
                record[path] = {"status": response.status, "sha256": hashlib.sha256(body).hexdigest()}
                if path == "/sandbox/capabilities":
                    assert not json.loads(body)["isolated_python"]["available"]
        report["http_smoke"] = record
        command = [str(PYTHON), "-c", "from pathlib import Path; import json; from quant_terminal.sandbox.gateway import code_hashes; print(json.dumps(code_hashes(Path.cwd())))"]
        hashes = subprocess.run(command, cwd=CLEAN, capture_output=True, text=True, check=True, timeout=30)
        assert json.loads(hashes.stdout) == fixture["source_hashes"]
        report["installed_source_hashes_match"] = True
        subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
                       capture_output=True, check=True, timeout=60)
        command = [str(PYTHON), "-m", "quant_terminal", "--state-dir", str(state),
                   "--project-root", str(CLEAN), "--isolation-config", str(state / "config.json"),
                   "sandbox-run", "--request", str(state / "request.json")]
        replay = subprocess.run(command, cwd=CLEAN, capture_output=True, text=True, timeout=200)
        report.update(replay_exit_code=replay.returncode, replay_stderr=replay.stderr[-2000:])
        assert replay.returncode == 0, replay.stderr
        job = json.loads(replay.stdout)
        assert job["status"] == "succeeded"
        assert job["result"]["result_fingerprint"] == fixture["http_result"]["result_fingerprint"]
        report.update(replay_job_id=job["id"], replay_run_id=job["result"]["run_id"],
                      replay_result_fingerprint=job["result"]["result_fingerprint"],
                      frozen_replay_matches=True)
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        report["result"] = "failed" if failed else "passed"
        target.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({"result": report["result"], "evidence": str(target),
                          "reason": report.get("failure_reason")}), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
