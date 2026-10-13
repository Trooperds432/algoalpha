"""Record independently reviewed actual employee runs; preserve originals."""
import json
from datetime import UTC, datetime

import m22_run_roles as campaign


def main():
    lease = campaign.SchedulerLease(campaign.LOCK_PATH)
    lease.acquire()
    try:
        receipts = json.loads(campaign.RECEIPTS.read_text())
        reviewed = []
        for code, actual in (
            ("7.6", "081a205a-1ed7-4d8c-89f4-31fa0da99818"),
            ("7.7", "bdc6207b-c9a0-4c12-99f9-b512541cdd67"),
            ("7.8", "feeaad6c-8314-4ee6-99bf-a31e48823bf4"),
            ("7.9", "8316cb11-a85e-4c78-b64f-f30b585d1355"),
            ("7.10", "d387de5b-5605-433b-889d-8ca03cd1cb71"),
            ("8.2", "efed2a7a-cd09-4ea0-93a2-a2cfe080bc99"),
        ):
            receipt = next(r for r in reversed(receipts) if r.get("actualRunId") == actual)
            delayed = receipt.get("runStatus") == "scheduled_retry"
            extra_report = code == "8.2" and receipt.get("reason") == "Unexpected interaction URL/method/status"
            if receipt["status"] == "blocked" and (delayed or extra_report):
                receipt = {k: receipt[k] for k in ("agentId", "roleCode", "issueId", "identifier", "artifact", "actualRunId")}
                origin = ("Later completed observation of the same actual run; prior scheduled_retry observation retained, not a new run"
                          if delayed else "Revalidated the same actual successful run under clarified selected-report read policy; prior validator rejection retained, not a new run")
                receipt.update(status="evidence_verified", origin=origin)
                receipts.append(receipt)
            assert receipt["status"] == "evidence_verified" and receipt["roleCode"] == code
            run = campaign.api("/heartbeat-runs/" + actual)
            issue = campaign.api("/issues/" + receipt["issueId"])
            assert run["status"] == "succeeded" and issue["status"] == "done"
            fresh = campaign.verify(receipt, run)
            if "verification" in receipt:
                assert fresh["artifactSha256"] == receipt["verification"]["artifactSha256"]
                assert fresh["interactionSha256"] == receipt["verification"]["interactionSha256"]
            else:
                receipt["verification"] = fresh
            evidence = json.loads((campaign.ROOT / receipt["artifact"]).with_suffix(".json").read_text(encoding="utf-8-sig"))
            start = datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(run["finishedAt"].replace("Z", "+00:00"))
            for item in evidence["http_interactions"]:
                assert start <= datetime.fromisoformat(item["timestamp"]) <= end
            receipt["independentFindingReview"] = {
                "reviewer": "/root/m22_gateway_review",
                "status": "bounded_participation_accepted",
                "at": datetime.now(UTC).isoformat(),
                "evidence": "docs/validation/m22-role-findings-review-20261004.md",
                "scope": "Actual identity/time/HTTP receipts and synthetic role findings independently reproduced; no release or strategy approval",
            }
            reviewed.append(code)
        campaign.RECEIPTS.write_text(json.dumps(receipts, indent=2), encoding="utf8")
        progress = {"at": datetime.now(UTC).isoformat(), "totalRequired": 370,
                    "active": 0, **campaign.progress_counts(receipts), "finalReleaseApproved": False}
        campaign.PROGRESS.write_text(json.dumps(progress, indent=2), encoding="utf8")
        print(json.dumps({"reviewed": reviewed, "progress": progress}))
    finally:
        lease.release()


if __name__ == "__main__":
    main()
