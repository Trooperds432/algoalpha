"""Accept only the actual completed Feature Store Manager retry and fixture."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import m22_run_roles as campaign
from quant_terminal.application.jobs import SchedulerLease
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import Bar


def bar(raw):
    return Bar(InstrumentId(raw["instrument_id"]), raw["frequency"], *(datetime.fromisoformat(raw[k]) for k in ("start", "end", "available_at")), *(Decimal(raw[k]) for k in ("open", "high", "low", "close", "volume")), raw["source"])


lease = SchedulerLease(campaign.LOCK_PATH)
lease.acquire()
try:
    receipts = json.loads(campaign.RECEIPTS.read_text())
    run_id = "88317274-6c38-4d38-894a-a0cf03f03138"
    if any(r.get("actualRunId") == run_id and r["status"] == "evidence_verified" for r in receipts):
        raise SystemExit("Actual retry already verified; no duplicate")
    prior = next(r for r in reversed(receipts) if r["roleCode"] == "7.5")
    previous_run = campaign.api("/heartbeat-runs/" + prior["actualRunId"])
    assert previous_run["status"] == "cancelled"
    prior.update(status="blocked", runStatus="cancelled", reason="Original actual run cancelled; fresh successful retry recorded separately")
    record = {k: prior[k] for k in ("agentId", "roleCode", "issueId", "identifier", "artifact")}
    run = campaign.api("/heartbeat-runs/" + run_id)
    issue = campaign.api("/issues/" + record["issueId"])
    assert run["status"] == "succeeded" and issue["status"] == "done"
    evidence = json.loads((campaign.ROOT / record["artifact"]).with_suffix(".json").read_text(encoding="utf-8-sig"))
    assert bar(evidence["accepted_input_fixture"]["input"]).close == Decimal("100.5")
    try:
        bar(evidence["failure_case"]["input"])
    except DomainError as exc:
        assert str(exc) == "Bar available_at cannot precede end"
    else:
        raise AssertionError("Premature record unexpectedly accepted")
    for item in evidence["http_interactions"]:
        assert datetime.fromisoformat(run["startedAt"].replace("Z", "+00:00")) <= datetime.fromisoformat(item["timestamp"]) <= datetime.fromisoformat(run["finishedAt"].replace("Z", "+00:00"))
    record.update(actualRunId=run_id, status="evidence_verified", origin="Actual successful Paperclip retry; original cancellation retained", verification=campaign.verify(record, run), independentFindingReview="Coordinator reproduced exact accepted Bar and premature-publication rejection. Actual three receipt hashes/identities matched. Feature-level registry/parity/entitlements remain unproven; bounded participation only.")
    receipts.append(record)
    campaign.RECEIPTS.write_text(json.dumps(receipts, indent=2), encoding="utf8")
    progress = {"at": datetime.now(UTC).isoformat(), "totalRequired": 370, "active": 0, **campaign.progress_counts(receipts), "finalReleaseApproved": False}
    campaign.PROGRESS.write_text(json.dumps(progress, indent=2), encoding="utf8")
    print(json.dumps(progress))
finally:
    lease.release()
