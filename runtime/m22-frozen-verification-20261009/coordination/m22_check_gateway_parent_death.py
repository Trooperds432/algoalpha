"""Kill only a disposable acceptance-testing API child, then verify recovery."""
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from quant_terminal.sandbox.gateway import code_hashes
from quant_terminal.sandbox.podman import _client_environment

PROJECT = Path(__file__).resolve().parents[1]


def main():
    fixture = json.loads(Path(sys.argv[1]).read_text())
    assert fixture["result"] == "passed" and fixture["scope"] == "acceptance_testing"
    assert fixture["source_hashes"] == code_hashes(PROJECT)
    root = Path(fixture["state"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = PROJECT / "docs/validation" / ("m22-gateway-parent-death-" + stamp + ".json")
    report = {"at": datetime.now(UTC).isoformat(), "state": str(root),
              "source_hashes": code_hashes(PROJECT), "scope": "acceptance_testing",
              "final_release_approved": False}
    with socket.socket() as allocator:
        allocator.bind(("127.0.0.1", 0))
        port = allocator.getsockname()[1]
    base = "http://127.0.0.1:" + str(port)
    command = [sys.executable, "-m", "quant_terminal", "--state-dir", str(root),
               "--project-root", str(PROJECT), "--isolation-config", str(root / "config.json"),
               "serve", "--port", str(port)]
    process = None
    failed = False
    log = (root / ("parent-death-" + stamp + ".log")).open("ab")

    def request(path, value=None):
        data = None if value is None else json.dumps(value).encode()
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.load(response)

    def start():
        child = subprocess.Popen(command, cwd=PROJECT, stdout=log, stderr=log)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Acceptance-testing API exited during startup")
            try:
                if request("/sandbox/capabilities")["isolated_python"]["available"]:
                    return child
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.2)
        child.terminate()
        child.wait(20)
        raise RuntimeError("Acceptance-testing API startup timed out")

    def inventory():
        result = subprocess.run(["podman", "ps", "-aq",
                 "--filter=label=algoalpha.owner=quant-terminal-m16"],
                 capture_output=True, check=True, timeout=10, env=_client_environment())
        return result.stdout.decode().split()

    try:
        subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"],
                       capture_output=True, check=True, timeout=60)
        assert not inventory(), "Pre-existing containers block exclusive test"
        process = start()
        packet = json.loads((root / "request.json").read_text())
        source = "def on_bar(context, bar):\n    while True:\n        pass\n# " + uuid4().hex
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        packet["source_package"] = {"source": source, "source_sha256": source_hash,
                                    "dependencies": []}
        job = request("/sandbox/strategies", packet)
        identity = job["id"]
        deadline = time.monotonic() + 90
        observed = []
        while time.monotonic() < deadline:
            events = [json.loads(line) for line in (root / "audit.jsonl").read_text().splitlines()]
            if any(e.get("source_sha256") == source_hash and e["event"] == "launch" for e in events):
                observed = inventory()
                if observed:
                    break
            if request("/sandbox/jobs/" + identity)["status"] in {"failed", "cancelled"}:
                break
            time.sleep(.05)
        assert observed, "Running strategy container not observed before parent death"
        report.update(job_id=identity, source_sha256=source_hash,
                      killed_api_pid=process.pid, observed_containers=observed)
        process.kill()
        process.wait(20)
        process = None
        deadline = time.monotonic() + 60
        remaining = inventory()
        while remaining and time.monotonic() < deadline:
            time.sleep(.2)
            remaining = inventory()
        assert not remaining, "Container remained after API death"
        report["inventory_before_restart"] = remaining
        process = start()
        recovered = request("/sandbox/jobs/" + identity)
        assert recovered["status"] == "interrupted", recovered
        assert recovered["result"] is None
        events = [json.loads(line) for line in (root / "audit.jsonl").read_text().splitlines()]
        assert sum(e.get("source_sha256") == source_hash and e["event"] == "launch" for e in events) == 1
        report.update(recovered_status=recovered["status"], automatically_rerun=False,
                      cancelled_experiment_published=False)
    except Exception as exc:
        failed = True
        report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:1000])
    finally:
        if process is not None:
            process.terminate()
            process.wait(30)
        log.close()
        try:
            remaining = inventory()
            report["final_container_inventory"] = remaining
            failed = failed or bool(remaining)
        except Exception as exc:
            failed = True
            report["inventory_failure"] = type(exc).__name__
        report["result"] = "failed" if failed else "passed"
        target.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(json.dumps({"result": report["result"], "evidence": str(target),
                          "reason": report.get("failure_reason")}), flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
