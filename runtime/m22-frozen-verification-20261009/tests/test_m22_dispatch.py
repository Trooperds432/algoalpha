"""Campaign recovery must not fabricate participation or repeat failed tasks."""

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "coordination/m22_run_roles.py"
_SPEC = importlib.util.spec_from_file_location("m22_dispatch", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
campaign = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(campaign)


def test_retry_permission_is_consumed_by_a_new_attempt() -> None:
    old = {"agentId": "employee", "status": "blocked", "retryAuthorized": True}
    assert campaign.dispatch_sets([old]) == (set(), {"employee"})
    for status in ("dispatched", "blocked", "evidence_verified"):
        latest = {"agentId": "employee", "status": status}
        assert campaign.dispatch_sets([old, latest]) == ({"employee"}, set())


def test_verified_employee_is_reserved_and_superseded_attempt_is_ignored() -> None:
    accepted = {"agentId": "employee", "status": "evidence_verified"}
    obsolete = {
        "agentId": "employee",
        "status": "blocked",
        "retryAuthorized": True,
        "superseded": True,
    }
    assert campaign.dispatch_sets([accepted, obsolete]) == ({"employee"}, set())


def test_cleared_run_id_recovers_only_exact_employee_and_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def record(identity: str, agent: str, issue: str, timestamp: str) -> dict[str, Any]:
        return {
            "id": identity,
            "agentId": agent,
            "contextSnapshot": {"issueId": issue},
            "createdAt": timestamp,
        }

    runs = [
        record("wrong-issue", "employee", "other", "2026-10-05"),
        record("wrong-agent", "other", "task", "2026-10-05"),
        record("old", "employee", "task", "2026-10-01"),
        record("latest", "employee", "task", "2026-10-04"),
    ]
    monkeypatch.setattr(campaign, "api", lambda path: runs)
    assert campaign.recover_run_id({"agentId": "employee", "issueId": "task"}) == "latest"
    assert campaign.recover_run_id({"agentId": "missing", "issueId": "task"}) is None


def test_progress_separates_retry_history_from_current_employee_status() -> None:
    receipts = [
        {"agentId": "a", "status": "blocked"},
        {"agentId": "a", "status": "dispatched"},
        {"agentId": "b", "status": "evidence_verified", "superseded": True},
        {"agentId": "c", "status": "evidence_verified"},
    ]
    assert campaign.progress_counts(receipts) == {
        "dispatched": 2,
        "attemptRecords": 4,
        "evidenceVerified": 1,
        "blocked": 0,
    }


def test_main_uses_later_actual_run_before_cached_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "coordination").mkdir()
    (tmp_path / "coordination/agent-assignments.json").write_text("[]")
    receipts = tmp_path / "receipts.json"
    receipts.write_text(
        json.dumps(
            [
                {
                    "agentId": "employee",
                    "issueId": "task",
                    "status": "dispatched",
                    "actualRunId": "failed-old",
                    "artifact": "unused.md",
                }
            ]
        )
    )
    progress = tmp_path / "progress.json"
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    monkeypatch.setattr(campaign, "RECEIPTS", receipts)
    monkeypatch.setattr(campaign, "PROGRESS", progress)
    monkeypatch.setattr(campaign, "LOCK_PATH", tmp_path / "campaign.lock")
    monkeypatch.setattr(sys, "argv", ["campaign", "--max-new", "0", "--minutes", "1"])

    def api(path: str, body: object = None) -> object:
        assert body is None, "A reconciliation-only run must not create issues"
        if path == "/issues/task":
            return {"status": "done", "executionRunId": None}
        if "heartbeat-runs?" in path:
            return [
                {
                    "id": "new-success",
                    "agentId": "employee",
                    "contextSnapshot": {"issueId": "task"},
                    "createdAt": "2026-10-04",
                }
            ]
        assert path == "/heartbeat-runs/new-success"
        return {"id": "new-success", "status": "succeeded"}

    monkeypatch.setattr(campaign, "api", api)
    monkeypatch.setattr(campaign, "verify", lambda receipt, run: {"runId": run["id"]})
    campaign.main()
    result = json.loads(receipts.read_text())[0]
    assert result["status"] == "evidence_verified"
    assert result["actualRunId"] == "new-success"
    assert json.loads(progress.read_text())["evidenceVerified"] == 1


def prepare_campaign(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    roles: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
) -> Path:
    (root / "coordination").mkdir()
    (root / "coordination/agent-assignments.json").write_text(json.dumps(roles))
    path = root / "receipts.json"
    path.write_text(json.dumps(receipts))
    monkeypatch.setattr(campaign, "ROOT", root)
    monkeypatch.setattr(campaign, "RECEIPTS", path)
    monkeypatch.setattr(campaign, "PROGRESS", root / "progress.json")
    monkeypatch.setattr(campaign, "LOCK_PATH", root / "campaign.lock")
    monkeypatch.setattr(campaign, "prompt", lambda role: "bounded review")
    monkeypatch.setattr(sys, "argv", ["campaign", "--max-new", "4", "--minutes", "1"])
    return path


def test_main_does_not_create_issue_when_terminal_is_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = prepare_campaign(monkeypatch, tmp_path, [{"agentId": "a"}], [])

    def unavailable() -> None:
        raise ConnectionRefusedError("offline")

    def no_api(*args: object) -> None:
        pytest.fail("Offline terminal must prevent issue creation")

    monkeypatch.setattr(campaign, "require_terminal", unavailable)
    monkeypatch.setattr(campaign, "api", no_api)
    with pytest.raises(ConnectionRefusedError):
        campaign.main()
    assert json.loads(path.read_text()) == []


def test_scheduled_retry_retains_active_slot_without_new_dispatch(monkeypatch, tmp_path):
    roles = [
        {
            "agentId": "waiting",
            "roleCode": "1",
            "name": "Waiting",
            "exclusiveArtifact": "waiting.md",
        },
        {"agentId": "next", "roleCode": "2", "name": "Next", "exclusiveArtifact": "next.md"},
    ]
    receipts = [{"agentId": "waiting", "issueId": "task", "status": "dispatched"}]
    path = prepare_campaign(monkeypatch, tmp_path, roles, receipts)
    monkeypatch.setattr(
        sys, "argv", ["campaign", "--max-new", "1", "--max-active", "1", "--minutes", "1"]
    )
    monkeypatch.setattr(campaign, "require_terminal", lambda: None)

    def api(url, body=None):
        assert body is None, "Scheduled retry still occupies the only active slot"
        if url == "/issues/task":
            return {"status": "in_progress", "executionRunId": "actual"}
        assert url == "/heartbeat-runs/actual"
        return {"status": "scheduled_retry"}

    class EndObservation(Exception):
        pass

    def observed(seconds):
        raise EndObservation

    monkeypatch.setattr(campaign, "api", api)
    monkeypatch.setattr(campaign.time, "sleep", observed)
    with pytest.raises(EndObservation):
        campaign.main()
    assert json.loads(path.read_text())[0]["status"] == "dispatched"
    assert json.loads((tmp_path / "progress.json").read_text())["active"] == 1


def test_main_rejects_duplicate_issue_without_new_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    role = {"agentId": "a", "roleCode": "1", "name": "A", "exclusiveArtifact": "a.md"}
    old = {"agentId": "a", "issueId": "old", "status": "blocked", "retryAuthorized": True}
    path = prepare_campaign(monkeypatch, tmp_path, [role], [old])
    monkeypatch.setattr(campaign, "require_terminal", lambda: None)
    monkeypatch.setattr(campaign, "api", lambda path, body: {"id": "old"})
    with pytest.raises(ValueError, match="existing issue"):
        campaign.main()
    assert json.loads(path.read_text()) == [old]


def test_main_bounds_concurrency_and_health_checks_every_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    roles = [
        {
            "agentId": str(n),
            "roleCode": str(n),
            "name": str(n),
            "exclusiveArtifact": f"{n}.md",
        }
        for n in range(4)
    ]
    path = prepare_campaign(monkeypatch, tmp_path, roles, [])
    events: list[str] = []
    monkeypatch.setattr(campaign, "require_terminal", lambda: events.append("health"))

    def create(url: str, body: object) -> dict[str, str]:
        events.append("create")
        return {"id": str(len(events)), "identifier": str(len(events))}

    class EndObservation(Exception):
        pass

    def end_sleep(seconds: float) -> None:
        raise EndObservation

    monkeypatch.setattr(campaign, "api", create)
    monkeypatch.setattr(campaign.time, "sleep", end_sleep)
    with pytest.raises(EndObservation):
        campaign.main()
    assert events == ["health", "create"] * 3
    assert len(json.loads(path.read_text())) == 3
    assert json.loads((tmp_path / "progress.json").read_text())["active"] == 3


def test_operator_can_run_one_writer_during_runtime_integration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    roles = [
        {
            "agentId": str(n),
            "roleCode": str(n),
            "name": str(n),
            "exclusiveArtifact": f"{n}.md",
        }
        for n in range(3)
    ]
    path = prepare_campaign(monkeypatch, tmp_path, roles, [])
    monkeypatch.setattr(
        sys, "argv", ["campaign", "--max-new", "3", "--minutes", "1", "--max-active", "1"]
    )
    events: list[str] = []
    monkeypatch.setattr(campaign, "require_terminal", lambda: events.append("health"))
    monkeypatch.setattr(
        campaign,
        "api",
        lambda url, body: (
            events.append("create")
            or {
                "id": "only",
                "identifier": "ALG-1",
            }
        ),
    )

    class EndObservation(Exception):
        pass

    monkeypatch.setattr(
        campaign.time, "sleep", lambda seconds: (_ for _ in ()).throw(EndObservation)
    )
    with pytest.raises(EndObservation):
        campaign.main()
    assert events == ["health", "create"]
    assert len(json.loads(path.read_text())) == 1
    assert json.loads((tmp_path / "progress.json").read_text())["active"] == 1


def test_second_coordinator_cannot_touch_the_campaign(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "campaign.lock"
    monkeypatch.setattr(campaign, "LOCK_PATH", lock_path)
    owner = campaign.SchedulerLease(lock_path)
    owner.acquire()

    def forbidden() -> None:
        pytest.fail("Another coordinator already owns the campaign")

    monkeypatch.setattr(campaign, "run_campaign", forbidden)
    try:
        with pytest.raises(Exception, match="scheduler already owns"):
            campaign.main()
    finally:
        owner.release()


def prepare_receipt(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    monkeypatch.setattr(campaign, "ROOT", root)
    report = root / "role.md"
    report.write_text(
        "## Requirement\n"
        + "Bounded requirement evidence. " * 12
        + "\n## Required failure case\nExplicit limitation observed."
    )
    selected = "run-example"
    base = "http://127.0.0.1:8765"
    bodies = {
        base + "/health": {"status": "ok", "mode": "research", "broker_orders_enabled": False},
        base + "/runs": [{"run_id": selected}],
        base + "/runs/" + selected: {"run_id": selected},
    }
    raw = {url: json.dumps(body).encode() for url, body in bodies.items()}
    evidence = {
        "agent_id": "employee",
        "issue_id": "task",
        "paperclip_run_id": "actual",
        "selected_run_id": selected,
        "finding": "Bounded finding",
        "limitations": "Synthetic",
        "http_interactions": [
            {
                "url": url,
                "method": "GET",
                "status": 200,
                "timestamp": "2026-10-04T11:00:00+00:00",
                "sha256": hashlib.sha256(raw[url]).hexdigest(),
                "body": body,
            }
            for url, body in bodies.items()
        ],
    }
    path = report.with_suffix(".json")
    path.write_text(json.dumps(evidence))
    monkeypatch.setattr(
        campaign.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(raw[url])
    )
    return (
        {"agentId": "employee", "issueId": "task", "artifact": "role.md"},
        {"id": "actual", "agentId": "employee", "contextSnapshot": {"issueId": "task"}},
        path,
    )


def test_legacy_pilot_markdown_requirements_and_actual_receipts_remain_valid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt, run, _ = prepare_receipt(monkeypatch, tmp_path)
    assert campaign.verify(receipt, run)["runId"] == "actual"


def test_naive_interaction_timestamp_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt, run, path = prepare_receipt(monkeypatch, tmp_path)
    evidence = json.loads(path.read_text())
    evidence["http_interactions"][0]["timestamp"] = "2026-10-04T11:00:00"
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="timezone"):
        campaign.verify(receipt, run)


def test_missing_requirement_and_failure_case_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt, run, _ = prepare_receipt(monkeypatch, tmp_path)
    (tmp_path / "role.md").write_text("HTTP connectivity only. " * 20)
    with pytest.raises(ValueError, match="Missing role requirement"):
        campaign.verify(receipt, run)


def report_receipt(monkeypatch, tmp_path):
    receipt, run, path = prepare_receipt(monkeypatch, tmp_path)
    evidence = json.loads(path.read_text())
    report = {"tables": [], "fixture": "selected report"}
    blob = json.dumps(report).encode()
    evidence["http_interactions"].append(
        {
            "url": "http://127.0.0.1:8765/runs/run-example/report",
            "method": "GET",
            "status": 200,
            "timestamp": "2026-10-04T11:00:00+00:00",
            "sha256": hashlib.sha256(blob).hexdigest(),
            "body": report,
        }
    )
    path.write_text(json.dumps(evidence))
    raw = {i["url"]: json.dumps(i["body"]).encode() for i in evidence["http_interactions"]}
    monkeypatch.setattr(
        campaign.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(raw[url])
    )
    return receipt, run, path


def test_selected_report_read_supplements_required_interactions(monkeypatch, tmp_path):
    receipt, run, _ = report_receipt(monkeypatch, tmp_path)
    assert campaign.verify(receipt, run)["runId"] == "actual"


@pytest.mark.parametrize("section", ["orders", "fills", "nav", "positions"])
def test_selected_accounting_reads_supplement_required_interactions(monkeypatch, tmp_path, section):
    receipt, run, path = report_receipt(monkeypatch, tmp_path)
    evidence = json.loads(path.read_text())
    evidence["http_interactions"][-1]["url"] = "http://127.0.0.1:8765/runs/run-example/" + section
    path.write_text(json.dumps(evidence))
    raw = {i["url"]: json.dumps(i["body"]).encode() for i in evidence["http_interactions"]}
    monkeypatch.setattr(
        campaign.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(raw[url])
    )
    assert campaign.verify(receipt, run)["runId"] == "actual"


def test_report_read_cannot_replace_required_health_interaction(monkeypatch, tmp_path):
    receipt, run, path = report_receipt(monkeypatch, tmp_path)
    evidence = json.loads(path.read_text())
    evidence["http_interactions"] = [
        i for i in evidence["http_interactions"] if not i["url"].endswith("/health")
    ]
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="Missing required terminal interaction"):
        campaign.verify(receipt, run)


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "POST"},
        {"status": 500},
        {"url": "http://127.0.0.1:8765/runs/another/report"},
        {"url": "http://example.com/runs/run-example/report"},
    ],
)
def test_optional_report_read_cannot_change_authority(monkeypatch, tmp_path, changes):
    receipt, run, path = report_receipt(monkeypatch, tmp_path)
    evidence = json.loads(path.read_text())
    evidence["http_interactions"][-1].update(changes)
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="Unexpected interaction"):
        campaign.verify(receipt, run)


def test_selected_binary_report_receipt_verifies_parsed_csv(monkeypatch, tmp_path):
    import zipfile

    receipt, run, path = report_receipt(monkeypatch, tmp_path)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("tables/fills.csv", "quantity,price\n5,100\n")
    raw = stream.getvalue()
    evidence = json.loads(path.read_text())
    item = evidence["http_interactions"][-1]
    item.update(
        url="http://127.0.0.1:8765/runs/run-example/artifacts/report-bundle.zip",
        sha256=hashlib.sha256(raw).hexdigest(),
        body={
            "format": "zip",
            "member_names": ["tables/fills.csv"],
            "parsed_csv_tables": {"fills": [{"quantity": "5", "price": "100"}]},
        },
    )
    path.write_text(json.dumps(evidence))
    blobs = {i["url"]: json.dumps(i["body"]).encode() for i in evidence["http_interactions"]}
    blobs[item["url"]] = raw
    monkeypatch.setattr(
        campaign.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(blobs[url])
    )
    assert campaign.verify(receipt, run)["runId"] == "actual"
    item["body"]["parsed_csv_tables"]["fills"][0]["quantity"] = "5000"
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="parsed body mismatch"):
        campaign.verify(receipt, run)
