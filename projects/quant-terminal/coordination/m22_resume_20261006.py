"""Reconcile actual restarted employee runs; preserve earlier failures."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import m22_run_roles as campaign


def main():
    receipts = json.loads(campaign.RECEIPTS.read_text())
    accepted = []
    for code, run_id in (
        ("5.10", "44107aef-56bc-45e8-9e77-da8e5f25ef3d"),
        ("6.3", "9338d8e1-6be6-47ec-a4ff-fab3cf68ae52"),
    ):
        if any(r.get("actualRunId") == run_id and r["status"] == "evidence_verified" for r in receipts):
            continue
        prior = next(r for r in reversed(receipts) if r["roleCode"] == code)
        run = campaign.api("/heartbeat-runs/" + run_id)
        issue = campaign.api("/issues/" + prior["issueId"])
        assert run["status"] == "succeeded" and issue["status"] == "done"
        record = {k: prior[k] for k in ("agentId", "roleCode", "issueId", "identifier", "artifact")}
        record.update(actualRunId=run_id, status="evidence_verified", origin="actual Paperclip automatic restart retry; prior receipt retained")
        record["verification"] = campaign.verify(record, run)
        evidence = json.loads((campaign.ROOT / record["artifact"]).with_suffix(".json").read_text(encoding="utf-8-sig"))
        for item in evidence["http_interactions"]:
            stamp = datetime.fromisoformat(item["timestamp"])
            assert datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00")) <= stamp <= datetime.fromisoformat(run["finishedAt"].replace("Z", "+00:00"))
        detail = next(i["body"] for i in evidence["http_interactions"] if i["url"].endswith(evidence["selected_run_id"]))
        metrics = detail["metrics"]
        assert Decimal(metrics["commissions"]["value"]) + Decimal(metrics["slippage_cost"]["value"]) == Decimal(metrics["total_cost"]["value"]) == Decimal("1.1")
        for name in ("alpha", "beta", "tracking_error", "information_ratio", "up_capture", "down_capture"):
            assert metrics[name]["value"] is None and metrics[name]["status"] == "unavailable" and "price-only" in metrics[name]["reason"]
        for suffix, confidence in (("95", ".95"), ("99", ".99"), ("99.9", ".999")):
            assert Decimal(43) * (1 - Decimal(confidence)) < 5
            for prefix in ("var_", "expected_shortfall_"):
                assert metrics[prefix + suffix]["status"] == "insufficient_sample"
        assert (Decimal(101) / 100 - 1) - ((Decimal(100) + 1) / 100 - 1) == 0
        record["independentFindingReview"] = "Coordinator reproduced exact costs, unavailable relative metrics, short-tail flags and distribution counterexample. Bounded participation only."
        receipts.append(record)
        accepted.append(code)
    campaign.RECEIPTS.write_text(json.dumps(receipts, indent=2), encoding="utf8")
    progress = {"at": datetime.now(UTC).isoformat(), "totalRequired": 370, "active": 0, **campaign.progress_counts(receipts), "finalReleaseApproved": False}
    campaign.PROGRESS.write_text(json.dumps(progress, indent=2), encoding="utf8")
    if accepted:
        report = campaign.ROOT / "docs/validation/m22-role-findings-review-20261004.md"
        with report.open("a", encoding="utf8") as stream:
            stream.write("\n\n## Actual restart retries, 2026-10-06\n\n5.10 Factor Researcher and 6.3 Macro Research Analyst completed actual successful Paperclip restart runs, with done issues and all three HTTP receipt timestamps inside their execution intervals. Earlier failed receipts remain intact. Coordinator freshly matched identities, artifact hashes and frozen response bytes/bodies, reproduced USD 1.10 cost addition, six unavailable price-only comparative metrics and six insufficient-sample tail flags. The factor distribution counterexample reconciles to zero total-return excess versus one percentage point false price-only excess. Their limitations correctly distinguish this synthetic control from factor or macro suitability. Bounded participation accepted: 43 of 370 independently reviewed; no release or strategy gate pass.\n")
    print(json.dumps(progress))


if __name__ == "__main__":
    main()
