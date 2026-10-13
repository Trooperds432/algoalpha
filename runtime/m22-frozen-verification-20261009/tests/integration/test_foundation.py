import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quant_terminal.config import AppConfig
from quant_terminal.core.clock import SimulationClock
from quant_terminal.core.events import Event
from quant_terminal.core.identifiers import RunId
from quant_terminal.core.serialization import event_json, parse_event
from quant_terminal.logging import write_event


def test_config_event_persistence_replay_and_audit(tmp_path):
    config = AppConfig(tmp_path, Decimal("1000"), 17)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        Event(f"event-{i}", RunId("fixture"), i, start, start + timedelta(minutes=i))
        for i in range(3)
    ]
    path = config.workspace / "events.jsonl"
    path.write_text("\n".join(map(event_json, events)), encoding="utf8")
    reconstructed = [parse_event(line) for line in path.read_text().splitlines()]
    clock = SimulationClock(start)
    for event in reconstructed:
        clock.advance(event.observed_at)
    assert reconstructed == events
    assert clock.now == start + timedelta(minutes=2)
    stream = io.StringIO()
    write_event(stream, "replay.succeeded", {"run_id": "fixture", "event_count": 3})
    assert json.loads(stream.getvalue())["fields"]["event_count"] == 3
