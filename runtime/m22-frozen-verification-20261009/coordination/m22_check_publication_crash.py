"""Actual API exit after registration, before the isolated job decision commits."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from quant_terminal.sandbox.gateway import code_hashes

PROJECT = Path(__file__).resolve().parents[1]


def child(root, port):
    import uvicorn
    from quant_terminal.api import create_app
    from quant_terminal.application.isolated_runtime import IsolatedResearchRuntime
    from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig

    register = IsolatedResearchRuntime._register

    def exit_after_registration(runtime, manifest, result, artifacts):
        register(runtime, manifest, result, artifacts)
        os._exit(75)  # Kill only this disposable trusted API at the exact crash seam.

    IsolatedResearchRuntime._register = exit_after_registration
    gateway = IsolationGateway(root, PROJECT, OperatorIsolationConfig.load(root / "config.json"))
    uvicorn.run(create_app(root, PROJECT, isolated_gateway=gateway), host="127.0.0.1", port=port)


def main():
    from quant_terminal.application.jobs import JobStore
    from quant_terminal.experiments import ExperimentStore
    from quant_terminal.sandbox.podman import _client_environment

    fixture = json.loads(Path(sys.argv[1]).read_text())
    assert fixture["result"] == "passed" and fixture["scope"] == "acceptance_testing"
    assert fixture["source_hashes"] == code_hashes(PROJECT)
    root = Path(fixture["state"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = PROJECT / "docs/validation" / ("m22-publication-crash-" + stamp + ".json")
    report = {"at": datetime.now(UTC).isoformat(), "state": str(root),
              "source_hashes": code_hashes(PROJECT), "scope": "acceptance_testing",
              "final_release_approved": False}
    with socket.socket() as allocator:
        allocator.bind(("127.0.0.1", 0))
        port = allocator.getsockname()[1]
    base = "http://127.0.0.1:" + str(port)
    log = (root / ("publication-crash-" + stamp + ".log")).open("ab")
    process = None
    failed = False

    def request(path, value=None):
        data = None if value is None else json.dumps(value).encode()
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def start(command):
        child_process = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=log)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if child_process.poll() is not None:
                raise RuntimeError("Disposable API failed during startup")
            try:
                if request("/sandbox/capabilities")["isolated_python"]["available"]:
                    return child_process
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.2)
        child_process.terminate()
        child_process.wait(20)
        raise RuntimeError("Disposable API startup timed out")

    try:
        subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
                       capture_output=True, check=True, timeout=60)
        process = start([sys.executable, __file__, "--child", str(root), str(port)])
        previous_runs = request("/runs")
        packet = json.loads((root / "request.json").read_text())
        submitted = request("/sandbox/strategies", packet)
        identity = submitted["id"]
        process.wait(150)
        assert process.returncode == 75
        process = None
        store = JobStore(root / "isolated-jobs")
        assert store.get(identity)["status"] == "running"
        registry = ExperimentStore(root / "runs")
        run_id = "run-" + identity
        manifest = registry.get(run_id)
        preserved = hashlib.sha256(registry.read_artifact(run_id, "report-bundle.zip")).hexdigest()
        report.update(job_id=identity, run_id=run_id, exit_code=75,
                      pre_restart_job_status="running", preserved_report_sha256=preserved,
                      registered_fingerprint=manifest.result_fingerprint)
        command = [sys.executable, "-m", "quant_terminal", "--state-dir", str(root),
                   "--project-root", str(PROJECT), "--isolation-config", str(root / "config.json"),
                   "serve", "--port", str(port)]
        process = start(command)
        job = request("/sandbox/jobs/" + identity)
        assert job["status"] == "interrupted" and job["result"] is None
        assert request("/runs") == previous_runs
        rejected = {}
        for path, payload in (
            ("/runs/" + run_id, None),
            ("/runs/" + run_id + "/metrics", None),
            ("/runs/" + run_id + "/report", None),
            ("/runs/" + run_id + "/report.html", None),
            ("/runs/" + run_id + "/artifacts/report-bundle.zip", None),
            ("/compare", {"run_ids": [run_id, previous_runs[0]["run_id"]]}),
        ):
            try:
                request(path, payload)
                raise AssertionError("Unconfirmed isolated result was exposed")
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
                body = json.load(exc)
                assert body["message"] == "Isolated experiment completion is unconfirmed"
                rejected[path] = exc.code
        assert hashlib.sha256(registry.read_artifact(run_id, "report-bundle.zip")).hexdigest() == preserved
        report.update(recovered_status="interrupted", unconfirmed_run_hidden=True,
                      rejected_public_reads=rejected, immutable_report_preserved=True)
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        if process is not None:
            process.terminate()
            process.wait(30)
        log.close()
        try:
            inventory = subprocess.run(["podman", "ps", "-aq",
                       "--filter=label=algoalpha.owner=quant-terminal-m16"],
                       capture_output=True, timeout=15, check=True,
                       env=_client_environment()).stdout.decode().split()
            report["container_inventory"] = inventory
            failed = failed or bool(inventory)
        except Exception as exc:
            failed = True
            report["inventory_failure"] = type(exc).__name__
        report["result"] = "failed" if failed else "passed"
        target.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({"result": report["result"], "evidence": str(target),
                          "reason": report.get("failure_reason")}), flush=True)
    return int(failed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        raise SystemExit(main())
