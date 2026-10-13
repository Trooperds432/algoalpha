import io
import json
from decimal import Decimal

import pytest

from quant_terminal.config import AppConfig
from quant_terminal.core.exceptions import ConfigurationError
from quant_terminal.logging import write_event


def test_valid_config(tmp_path):
    config = AppConfig(tmp_path, Decimal("1000"), 7)
    assert config.max_workers == 2
    assert not config.live_enabled


@pytest.mark.parametrize(
    "change",
    [
        {"initial_capital": Decimal("NaN")},
        {"initial_capital": Decimal("0")},
        {"initial_capital": 1000.0},
        {"seed": True},
        {"seed": -1},
        {"max_workers": 370},
        {"timeout_seconds": 0},
        {"live_enabled": True},
        {"live_enabled": 0},
        {"timezone": "Europe/London"},
    ],
)
def test_invalid_config_fails_closed(tmp_path, change):
    args = {"workspace": tmp_path, "initial_capital": Decimal("1000"), "seed": 7}
    args.update(change)
    with pytest.raises(ConfigurationError):
        AppConfig(**args)


def test_recursive_redaction_and_json_lines():
    out = io.StringIO()
    write_event(
        out,
        "job.started",
        {
            "run_id": "run-1",
            "headers": {
                "Authorization": "private-value",
                "nested": [{"api_key": "key-value"}],
            },
            "refreshToken": "refresh-value",
        },
    )
    rendered = out.getvalue()
    assert "private-value" not in rendered
    assert "key-value" not in rendered
    assert "refresh-value" not in rendered
    assert json.loads(rendered)["fields"]["run_id"] == "run-1"
    assert len(rendered.splitlines()) == 1


def test_unknown_objects_never_render_repr():
    class SecretObject:
        def __repr__(self):
            return "hidden-secret"

    out = io.StringIO()
    write_event(out, "job.test", {"object": SecretObject()})
    assert "hidden-secret" not in out.getvalue()
