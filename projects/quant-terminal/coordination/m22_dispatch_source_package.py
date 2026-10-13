"""Bounded actual Software Engineer implementation assignment."""
import json
from pathlib import Path

import m22_run_roles as campaign

receipt_path = Path(__file__).with_name("m22-source-package-implementation.json")
if receipt_path.exists():
    raise SystemExit("Existing assignment retained; inspect instead of duplicating")
campaign.require_terminal()
role = next(r for r in json.loads((campaign.ROOT / "coordination/agent-assignments.json").read_text(encoding="utf-8-sig")) if r["roleCode"] == "12.2")
description = """M22 prerequisite implementation, under coordinator ALG-443. Exclusive writer only for
src/quant_terminal/sandbox/source_package.py and tests/unit/test_source_package.py,
plus docs/agent-contributions/12-2.md and .json. Do not modify any other files.
Read AGENTS.md, docs/roadmap.md, coordination/milestone-state.json and
docs/m22-isolation-integration-plan.md. Public isolated admission stays disabled.
Implement ONLY a pure frozen dataclass SourcePackage(source: str, source_sha256: str),
classmethod from_payload(value: object) -> SourcePackage and to_payload()->dict[str,object].
Exact accepted JSON-object fields source, source_sha256, dependencies; string keys;
source must be nonempty UTF-8 text, encoded bytes <= protocol.MAX_SOURCE_BYTES;
hash exact lowercase 64-hex matching SHA256 of UTF-8 source; dependencies must be
an empty JSON array (list ONLY). No caller paths, verified flags, or other fields.
Use DomainError for invalid input including invalid surrogate Unicode. Constructing
the dataclass directly must enforce source/hash invariants too. to_payload emits
source/source_sha256/dependencies:[] with fresh container. Do not compile, parse,
import, execute, stage, extract or write supplied source; source remains opaque data.
No broker, network except three terminal GETs, secrets, external routing/delegation,
new tasks, public enablement, dependencies or archive/scheduler modifications.
Write focused requirements tests: valid opaque source round-trip/frozen, source SHA
mismatch/case/malformed, non-string/Unicode/empty, byte boundary incl multibyte,
nonempty or wrong-typed dependencies, unknown fields including host paths/verified.
Run affected tests, Ruff, mypy using existing venv and record actual results/errors.
Actually GET localhost8765/health, /runs and /runs/<selected run_id>, preserve exact
response-byte SHA256, parsed bodies, UTC timestamps/method/status in your JSON.
JSON uses agent_id, issue_id, paperclip_run_id actual context IDs, selected_run_id,
recorded_at, http_interactions, requirement, failure_case, finding, limitations.
Markdown records implementation checks and limitations. No milestone pass claim.
Comment on this issue and mark done only after both role artifacts and checks exist.
Keep within one bounded implementation task; coordinator independently integrates.
"""
issue = campaign.api("/companies/" + campaign.COMPANY + "/issues", {"title": "M22 -- 12.2 Software Engineer: bounded source package validator", "description": description, "status": "todo", "priority": "high", "assigneeAgentId": role["agentId"], "projectId": campaign.PROJECT})
record = {"agentId": role["agentId"], "roleCode": "12.2", "issueId": issue["id"], "identifier": issue["identifier"], "artifact": "docs/agent-contributions/12-2.md", "status": "dispatched", "scope": "source_package.py, test_source_package.py and own role artifacts only"}
receipt_path.write_text(json.dumps(record, indent=2), encoding="utf8")
print(json.dumps(record))
