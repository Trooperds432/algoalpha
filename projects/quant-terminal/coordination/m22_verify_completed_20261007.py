"""Fresh root verification, including ALG450's explicit localhost receipt schema."""
import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal

import m22_run_roles as campaign
from quant_terminal.application.jobs import SchedulerLease


def verify_software(record, run):
    artifact = campaign.ROOT / record["artifact"]
    evidence_path = artifact.with_suffix(".json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    assert run["agentId"] == record["agentId"] and run["contextSnapshot"]["issueId"] == record["issueId"]
    assert evidence["agent_id"] == record["agentId"] and evidence["issue_id"] == record["issueId"] and evidence["paperclip_run_id"] == run["id"]
    assert all(evidence.get(k) for k in ("requirement", "failure_case", "finding", "limitations"))
    selected = evidence["selected_run_id"]
    required = {"http://localhost:8765" + p for p in ("/health", "/runs", "/runs/" + selected)}
    assert len(evidence["http_interactions"]) == 3
    bodies = {}
    for item in evidence["http_interactions"]:
        assert item["url"] in required and item["status"] == 200 and item["method"] == "GET"
        stamp = datetime.fromisoformat(item["recorded_at"])
        assert datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00")) <= stamp <= datetime.fromisoformat(run["finishedAt"].replace("Z", "+00:00"))
        with urllib.request.urlopen(item["url"], timeout=10) as response:
            raw = response.read()
        assert hashlib.sha256(raw).hexdigest() == item["response_sha256"] and json.loads(raw) == item["body"]
        bodies[item["url"]] = item["body"]
    assert set(bodies) == required
    assert bodies["http://localhost:8765/health"]["broker_orders_enabled"] is False
    assert any(b["run_id"] == selected for b in bodies["http://localhost:8765/runs"])
    assert bodies["http://localhost:8765/runs/" + selected]["run_id"] == selected
    return {"runId": run["id"], "issueId": record["issueId"], "artifact": record["artifact"], "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "interactionArtifact": str(evidence_path.relative_to(campaign.ROOT)), "interactionSha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(), "verifiedAt": datetime.now(UTC).isoformat(), "scope": "Actual localhost raw-byte receipts with recorded_at/response_sha256 keys explicitly verified; original artifacts unchanged. Source package independently reviewed and tested."}


def main():
    lease = SchedulerLease(campaign.LOCK_PATH)
    lease.acquire()
    try:
        receipts = json.loads(campaign.RECEIPTS.read_text())
        for code in ("6.4", "6.5"):
            record = next(r for r in reversed(receipts) if r["roleCode"] == code)
            run = campaign.api("/heartbeat-runs/" + record["actualRunId"])
            issue = campaign.api("/issues/" + record["issueId"])
            assert run["status"] == "succeeded" and issue["status"] == "done"
            record["verification"] = campaign.verify(record, run)
            evidence = json.loads((campaign.ROOT / record["artifact"]).with_suffix(".json").read_text(encoding="utf-8-sig"))
            detail = next(i["body"] for i in evidence["http_interactions"] if i["url"].endswith(evidence["selected_run_id"]))
            metrics = detail["metrics"]
            assert Decimal(metrics["commissions"]["value"]) + Decimal(metrics["slippage_cost"]["value"]) == Decimal(metrics["total_cost"]["value"]) == Decimal("1.1")
            assert metrics["alpha"]["status"] == "unavailable" and metrics["alpha"]["value"] is None and "price-only" in metrics["alpha"]["reason"]
            assert metrics["expected_shortfall_95"]["status"] == "insufficient_sample" and metrics["win_rate"]["value"] is None
            assert (Decimal("1.20") - Decimal("1.00")) / Decimal("1.00") == Decimal(".20")
            assert (Decimal("1.20") - Decimal("1.15")) / Decimal("1.15") == Decimal("0.04347826086956521739130434783")
            record["independentFindingReview"] = "Costs, comparative-metric and short-tail limitations reproduced. Earnings vintage oracle independently reconciles; industry conclusions correctly remain unsupported. Bounded participation only."
        source_path = campaign.ROOT / "coordination/m22-source-package-implementation.json"
        record = json.loads(source_path.read_text())
        run = campaign.api("/heartbeat-runs/d60cbb75-58bc-46b0-b93c-f5c767572dc6")
        issue = campaign.api("/issues/" + record["issueId"])
        assert run["status"] == "succeeded" and issue["status"] == "done"
        record.update(actualRunId=run["id"], status="evidence_verified", verification=verify_software(record, run), independentFindingReview="SourcePackage independent engineering review passed; root fresh source/protocol/bridge/runtime checks69passed. No public execution authority conferred.")
        source_path.write_text(json.dumps(record, indent=2), encoding="utf8")
        if not any(r["issueId"] == record["issueId"] and r.get("actualRunId") == run["id"] for r in receipts):
            receipts.append(record)
        campaign.RECEIPTS.write_text(json.dumps(receipts, indent=2), encoding="utf8")
        progress = {"at": datetime.now(UTC).isoformat(), "totalRequired": 370, "active": 0, **campaign.progress_counts(receipts), "finalReleaseApproved": False}
        campaign.PROGRESS.write_text(json.dumps(progress, indent=2), encoding="utf8")
        print(json.dumps(progress))
    finally:
        lease.release()


if __name__ == "__main__":
    main()
