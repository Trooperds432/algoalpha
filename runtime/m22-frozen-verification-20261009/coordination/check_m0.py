"""Structural M0 gate checks; these are not financial engine tests."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
names = ["product_requirements", "system_architecture", "domain_model", "backtesting_conventions",
         "testing_strategy", "security_model", "data_model", "roadmap"]
for name in names:
    path = ROOT / "docs" / f"{name}.md"
    assert path.is_file() and len(path.read_text(encoding="utf8")) > 700, name
architecture = (ROOT / "docs/system_architecture.md").read_text(encoding="utf8")
assert architecture.count("```mermaid") == 7
assignments = json.loads((ROOT / "coordination/agent-assignments.json").read_text(encoding="utf8"))
issues = json.loads((ROOT / "coordination/paperclip-issues.json").read_text(encoding="utf8"))
assert len(assignments) == len(issues) == 370
assert len({a["agentId"] for a in assignments}) == 370
assert {a["agentId"] for a in assignments} == {i["agentId"] for i in issues}
assert len({a["exclusiveArtifact"] for a in assignments}) == 370
assert all(a["roleMandate"] and a["buildTask"] and a["interactionTask"] for a in assignments)
state = json.loads((ROOT / "coordination/milestone-state.json").read_text(encoding="utf8"))
assert state["automaticContinuation"]
if state["activeMilestone"] == 0:
    financial = ("portfolio", "execution", "engine", "analytics", "optimization",
                 "walkforward", "montecarlo")
    assert not any((ROOT / "src/quant_terminal" / name).exists() for name in financial), (
        "M0 must precede financial implementation; foundation/scaffolding is permitted"
    )
else:
    baseline = json.loads((ROOT / "docs/validation/m0-baseline.json").read_text(encoding="utf8"))
    assert baseline["pre_implementation_check"] == "Passed before src existed; recorded in m0-completion.md"
    assert len(baseline["documents"]) == 8
    print("M0 pre-implementation constraint: historical; checking preserved baseline after advancement.")
print("PASS: 8 M0 documents, 7 flow diagrams, 370 unique role assignments/issues and exclusive artifacts.")
