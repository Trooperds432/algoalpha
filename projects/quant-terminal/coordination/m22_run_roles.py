"""Bounded actual Paperclip role dispatch; receipts are not release approvals."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from quant_terminal.application.jobs import SchedulerLease

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:3100/api"
COMPANY = "3b1d401d-7010-4eed-acd1-be7d52372c07"
PROJECT = "d0eefc92-3d3a-4862-acf7-31e4fee95a56"
RECEIPTS = ROOT / "coordination/m22-dispatches.json"
PROGRESS = ROOT / "docs/validation/m22-role-progress.json"
LOCK_PATH = ROOT.parents[1] / "runtime/m22-coordinator/campaign.lock"


def api(path: str, body: dict[str, object] | None = None) -> object:
    request = urllib.request.Request(
        API + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def prompt(role: dict[str, object]) -> str:
    return f"""M22 final release acceptance: {role["name"]} ({role["roleCode"]}).
Owner approved a NEW bounded role-specific software review and terminal interaction,
not replay of any failed task. Your authoritative role mandate is below.
Read project AGENTS.md, docs/m22-acceptance-contract.md and M21 release report.
Review one concrete existing-software requirement and one failure case relevant
to this exact role, with observed evidence, candid limits and recommendation.
This is software/research evidence, never professional approval or synthetic alpha.

Actually GET http://127.0.0.1:8765/health, /runs and /runs/<returned run_id>.
Use Python urllib.request or PowerShell; record exact response-byte SHA256,
UTC timestamp, method, URL, status and parsed body. Inspect the returned manifest
and metrics according to your role. HTTP success alone is not acceptance.
Only write C:/Users/Jonat/algoalpha/projects/quant-terminal/{role["exclusiveArtifact"]}
and its same-stem .json. Preserve existing artifacts; no source/other file edits.
The Markdown must state role requirement, failure case, evidence, finding and limits.
JSON must have agent_id, issue_id, paperclip_run_id (actual task context IDs),
selected_run_id, recorded_at, http_interactions (timestamp,method,url,status,sha256,
body), finding, requirement, failure_case, limitations. Never invent IDs/results.
No delegation, new issues, external research/messages, paid services, secret reads,
broker endpoints/connections/orders, strategy submission or runtime/source changes.
Do not poll other tasks. Comment on this assigned issue and mark done only after
real interaction and both artifacts exist; report failures on this issue.
Keep the task bounded: inspect existing evidence rather than rerunning campaigns.
Do not claim M16, strategy eligibility or final release passed.

Role build scope: {role["buildTask"]}
Role interaction scope: {role["interactionTask"]}
Owner role mandate (source data):
{role["roleMandate"]}
"""


def require_terminal() -> None:
    """Do not dispatch employees against an unavailable or incompatible service."""
    with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=5) as response:
        health = json.load(response)
    if (
        health.get("status") != "ok"
        or health.get("mode") != "research"
        or health.get("broker_orders_enabled") is not False
    ):
        raise ValueError("Expected healthy research terminal with broker orders disabled")


def recover_run_id(receipt: dict[str, object]) -> str | None:
    """Recover an actual task run after Paperclip clears executionRunId."""
    runs = api(f"/companies/{COMPANY}/heartbeat-runs?agentId={receipt['agentId']}&limit=100")
    if not isinstance(runs, list):
        raise ValueError("Unexpected Paperclip run-list response")
    matches = [
        run
        for run in runs
        if run.get("agentId") == receipt["agentId"]
        and run.get("contextSnapshot", {}).get("issueId") == receipt["issueId"]
    ]
    if not matches:
        return None
    latest = max(matches, key=lambda run: str(run.get("createdAt") or run.get("startedAt") or ""))
    return str(latest["id"])


def dispatch_sets(receipts: list[dict[str, object]]) -> tuple[set[str], set[str]]:
    """A retry authorization belongs to the latest actual attempt only."""
    latest = {str(r["agentId"]): r for r in receipts if not r.get("superseded")}
    verified = {
        str(r["agentId"])
        for r in receipts
        if r["status"] == "evidence_verified" and not r.get("superseded")
    }
    retries = {
        identity
        for identity, r in latest.items()
        if r["status"] == "blocked" and r.get("retryAuthorized")
    } - verified
    return (set(latest) - retries) | verified, retries


def progress_counts(receipts: list[dict[str, object]]) -> dict[str, int]:
    latest = {str(r["agentId"]): r for r in receipts if not r.get("superseded")}
    verified = {
        str(r["agentId"])
        for r in receipts
        if r["status"] == "evidence_verified" and not r.get("superseded")
    }
    return {
        "dispatched": len(latest),
        "attemptRecords": len(receipts),
        "evidenceVerified": len(verified),
        "blocked": len({key for key, r in latest.items() if r["status"] == "blocked"} - verified),
    }


def verify(receipt: dict[str, object], run: dict[str, object]) -> dict[str, object]:
    if (
        run.get("agentId") != receipt["agentId"]
        or run.get("contextSnapshot", {}).get("issueId") != receipt["issueId"]
    ):
        raise ValueError("Live Paperclip run is not associated with this employee/task")
    artifact = ROOT / str(receipt["artifact"])
    evidence_path = artifact.with_suffix(".json")
    if not artifact.is_file() or not evidence_path.is_file():
        raise ValueError("Missing role review or HTTP evidence")
    if artifact.stat().st_size < 250 or evidence_path.stat().st_size > 1_000_000:
        raise ValueError("Artifact is incomplete or exceeds evidence budget")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    expected = {
        "agent_id": receipt["agentId"],
        "issue_id": receipt["issueId"],
        "paperclip_run_id": run["id"],
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError("Artifact task/run/agent identity mismatch")
    report = artifact.read_text(encoding="utf-8-sig")
    requirement = evidence.get("requirement") or "## Requirement" in report
    failure_case = evidence.get("failure_case") or any(
        heading in report for heading in ("## Required failure case", "## Failure case")
    )
    if (
        not evidence.get("finding")
        or not evidence.get("limitations")
        or not requirement
        or not failure_case
    ):
        raise ValueError("Missing role requirement, failure case, finding or limitations")
    interactions = evidence.get("http_interactions")
    if not isinstance(interactions, list) or not 3 <= len(interactions) <= 12:
        raise ValueError("Missing bounded actual HTTP receipts")
    selected = evidence.get("selected_run_id")
    required = {
        "http://127.0.0.1:8765" + path for path in ("/health", "/runs", f"/runs/{selected}")
    }
    binary_report = f"http://127.0.0.1:8765/runs/{selected}/artifacts/report-bundle.zip"
    allowed = (
        required
        | {binary_report}
        | {
            f"http://127.0.0.1:8765/runs/{selected}/{section}"
            for section in ("report", "orders", "fills", "nav", "positions", "trades", "equity")
        }
    )
    observed = set()
    bodies = {}
    for item in interactions:
        url = item.get("url")
        if url not in allowed or item.get("method") != "GET" or item.get("status") != 200:
            raise ValueError("Unexpected interaction URL/method/status")
        timestamp = datetime.fromisoformat(item["timestamp"])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Interaction timestamp must include a timezone")
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read(100_000_001)
        if len(raw) > 100_000_000:
            raise ValueError("HTTP receipt exceeds size budget")
        if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
            raise ValueError("HTTP receipt does not match frozen terminal bytes")
        if url == binary_report:
            body = item.get("body")
            if (
                not isinstance(body, dict)
                or set(body) != {"format", "member_names", "parsed_csv_tables"}
                or body["format"] != "zip"
                or not isinstance(body["parsed_csv_tables"], dict)
                or not 1 <= len(body["parsed_csv_tables"]) <= 32
            ):
                raise ValueError("HTTP receipt parsed body mismatch")
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    members = archive.infolist()
                    if (
                        len(members) > 64
                        or len(set(archive.namelist())) != len(members)
                        or archive.namelist() != body["member_names"]
                        or sum(m.file_size for m in members) > 100_000_000
                    ):
                        raise ValueError("HTTP receipt parsed body mismatch")
                    for name, rows in body["parsed_csv_tables"].items():
                        if not isinstance(name, str) or not name.replace("_", "").isalnum():
                            raise ValueError("HTTP receipt parsed body mismatch")
                        member = "tables/" + name + ".csv"
                        if archive.getinfo(member).file_size > 2_000_000:
                            raise ValueError("HTTP receipt parsed body mismatch")
                        parsed = list(
                            csv.DictReader(io.StringIO(archive.read(member).decode("utf-8")))
                        )
                        if parsed != rows:
                            raise ValueError("HTTP receipt parsed body mismatch")
            except (zipfile.BadZipFile, csv.Error, KeyError, UnicodeError, RuntimeError) as exc:
                raise ValueError("HTTP receipt parsed body mismatch") from exc
        elif json.loads(raw) != item.get("body"):
            raise ValueError("HTTP receipt parsed body mismatch")
        observed.add(url)
        bodies[url] = item["body"]
    if not required <= observed:
        raise ValueError("Missing required terminal interaction")
    listing = bodies["http://127.0.0.1:8765/runs"]
    detail = bodies[f"http://127.0.0.1:8765/runs/{selected}"]
    if not isinstance(listing, list) or not any(
        isinstance(item, dict) and item.get("run_id") == selected for item in listing
    ):
        raise ValueError("Selected run is absent from terminal listing")
    if not isinstance(detail, dict) or detail.get("run_id") != selected:
        raise ValueError("Selected run detail identity mismatch")
    return {
        "runId": run["id"],
        "issueId": receipt["issueId"],
        "artifact": str(artifact.relative_to(ROOT)),
        "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "interactionArtifact": str(evidence_path.relative_to(ROOT)),
        "interactionSha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "verifiedAt": datetime.now(UTC).isoformat(),
        "scope": (
            "identity/files/actual HTTP evidence verified; role finding needs independent review"
        ),
    }


def run_campaign() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-new", type=int, default=3)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--max-active", type=int, default=3)
    args = parser.parse_args()
    if (
        not 0 <= args.max_new <= 370
        or not 1 <= args.minutes <= 480
        or not 1 <= args.max_active <= 3
    ):
        raise ValueError("Invalid bounded campaign budget")
    roles = json.loads(
        (ROOT / "coordination/agent-assignments.json").read_text(encoding="utf-8-sig")
    )
    receipts = json.loads(RECEIPTS.read_text())
    launched = 0
    deadline = time.monotonic() + args.minutes * 60
    while time.monotonic() < deadline:
        active = 0
        for receipt in receipts:
            if receipt.get("superseded"):
                continue
            if receipt.get("status") in {"evidence_verified", "blocked"}:
                continue
            issue = api("/issues/" + str(receipt["issueId"]))
            run_id = issue.get("executionRunId") or recover_run_id(receipt)
            run_id = run_id or receipt.get("actualRunId")
            if not run_id and issue.get("status") == "done":
                evidence_path = (ROOT / str(receipt["artifact"])).with_suffix(".json")
                if evidence_path.is_file():
                    # A reported ID is only a lookup hint; verify live association below.
                    run_id = json.loads(evidence_path.read_text(encoding="utf-8-sig")).get(
                        "paperclip_run_id"
                    )
            if not run_id:
                active += 1
                continue
            receipt["actualRunId"] = run_id
            run = api("/heartbeat-runs/" + str(run_id))
            if run["status"] in {"queued", "running", "scheduled_retry"}:
                active += 1
                continue
            if run["status"] != "succeeded" or issue["status"] != "done":
                receipt.update(
                    status="blocked",
                    reason="Actual run/issue did not complete",
                    runStatus=run["status"],
                )
                continue
            try:
                receipt["verification"] = verify(receipt, run)
                receipt["status"] = "evidence_verified"
            except (ValueError, OSError, KeyError) as exc:
                receipt.update(status="blocked", reason=str(exc))
        known, queued_retries = dispatch_sets(receipts)
        candidates = [r for r in roles if r["agentId"] in queued_retries] + roles
        scheduled: set[str] = set()
        for role in candidates:
            if role["agentId"] in scheduled:
                continue
            if active >= args.max_active or launched >= args.max_new:
                break
            if role["agentId"] in known:
                continue
            require_terminal()
            artifact = ROOT / role["exclusiveArtifact"]
            if artifact.exists() or role["agentId"] in queued_retries:
                # Preserve all prior evidence and use a unique retry artifact.
                role = {
                    **role,
                    "exclusiveArtifact": "docs/agent-contributions/m22/retry-"
                    + str(sum(r["agentId"] == role["agentId"] for r in receipts) + 1)
                    + "/"
                    + artifact.name,
                }
            issue = api(
                "/companies/" + COMPANY + "/issues",
                {
                    "title": f"M22 acceptance -- {role['roleCode']} {role['name']}"
                    + (
                        f" retry {sum(r['agentId'] == role['agentId'] for r in receipts) + 1}"
                        if role["agentId"] in queued_retries
                        else ""
                    ),
                    "description": prompt(role),
                    "status": "todo",
                    "priority": "medium",
                    "assigneeAgentId": role["agentId"],
                    "projectId": PROJECT,
                },
            )
            if any(r["issueId"] == issue["id"] for r in receipts):
                raise ValueError("Paperclip returned an existing issue; no new attempt recorded")
            scheduled.add(role["agentId"])
            receipts.append(
                {
                    "agentId": role["agentId"],
                    "roleCode": role["roleCode"],
                    "issueId": issue["id"],
                    "identifier": issue["identifier"],
                    "artifact": role["exclusiveArtifact"],
                    "status": "dispatched",
                }
            )
            RECEIPTS.write_text(json.dumps(receipts, indent=2), encoding="utf8")
            active += 1
            launched += 1
        RECEIPTS.write_text(json.dumps(receipts, indent=2), encoding="utf8")
        progress = {
            "at": datetime.now(UTC).isoformat(),
            "totalRequired": 370,
            "active": active,
            **progress_counts(receipts),
            "finalReleaseApproved": False,
        }
        PROGRESS.write_text(json.dumps(progress, indent=2), encoding="utf8")
        print(json.dumps(progress), flush=True)
        known, _ = dispatch_sets(receipts)
        exhausted = all(role["agentId"] in known for role in roles)
        if active == 0 and (launched >= args.max_new or exhausted):
            return
        time.sleep(20)
    print("Campaign time budget ended; receipts retained for explicit continuation", flush=True)


def main() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lease = SchedulerLease(LOCK_PATH)
    lease.acquire()
    try:
        run_campaign()
    finally:
        lease.release()


if __name__ == "__main__":
    main()
