"""Fresh opt-in operator containment suite under bounded WSL transport only."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from quant_terminal.sandbox.podman import _client_environment
from quant_terminal.sandbox.wsl_keeper import WslKeeper

ROOT = Path(__file__).resolve().parents[1]
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
prefix = ROOT / "docs/validation" / ("m22-hostile-current-" + stamp)
image = "sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29"
profile = "/home/user/algoalpha-seccomp.json"
report = {"at": datetime.now(UTC).isoformat(), "scope": "operator backend hostile suite, not public queued API acceptance", "public_admission_enabled": False, "image": image, "profile": profile, "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*sorted((ROOT / "src/quant_terminal/sandbox").glob("*.py")), ROOT / "tests/security/test_m16_hostile.py"]}, "result": "pending"}
keeper = WslKeeper(lifetime_seconds=900)
failed = True


def inventory():
    return subprocess.run(["podman", "ps", "-aq", "--filter=label=algoalpha.owner=quant-terminal-m16"], capture_output=True, timeout=15, check=True, env=_client_environment()).stdout.decode().split()


try:
    subprocess.run(["wsl.exe", "-d", "podman-algoalpha-m16", "--", "python3", "--version"], capture_output=True, timeout=60, check=True)
    keeper.start()
    report["pre_suite_container_inventory"] = inventory()
    if report["pre_suite_container_inventory"]:
        raise RuntimeError("Existing owner container; suite must not reconcile an active owner")
    environment = dict(os.environ)
    environment.update(QT_M16_HOSTILE="RUN_IN_VERIFIED_PODMAN", QT_M16_IMAGE=image, QT_M16_SECCOMP_PROFILE=profile)
    command = [sys.executable, "-m", "pytest", "tests/security/test_m16_hostile.py", "-q", "--tb=short", "--junitxml=" + str(prefix.with_suffix(".xml"))]
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=780)
    prefix.with_suffix(".txt").write_text(result.stdout + result.stderr, encoding="utf8")
    report.update(command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    report["post_suite_container_inventory"] = inventory()
    failed = result.returncode != 0 or bool(report["post_suite_container_inventory"]) or "skipped" in result.stdout
    keeper.ensure_available()
except Exception as exc:
    report.update(failure_type=type(exc).__name__, failure_reason=str(exc)[:500])
finally:
    try:
        clean = inventory() == []
        if not clean:
            failed = True
        keeper.close(cleanup_confirmed=clean)
        report["keeper_cleanup"] = "confirmed_empty_inventory_before_close" if clean else "not_confirmed"
    except Exception as exc:
        failed = True
        report.update(close_failure_type=type(exc).__name__, close_failure_reason=str(exc)[:500])
    report["result"] = "failed" if failed else "passed"
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({"result": report["result"], "evidence": str(prefix.with_suffix('.json')), "reason": report.get("failure_reason")}), flush=True)
raise SystemExit(int(failed))
