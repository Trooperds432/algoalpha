"""Harmless worker contract fixtures; OS isolation is tested in containers only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from quant_terminal.sandbox.protocol import (
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    WireIntent,
    decode_response,
    encode_response,
)
from quant_terminal.sandbox.strategy_worker import _emit, execute


def _request(source: str, *, empty: bool = False) -> SandboxRequest:
    rows = () if empty else ((("close", "99"),), (("close", "100"),))
    data = json.dumps([dict(row) for row in rows], sort_keys=True, separators=(",", ":"))
    return SandboxRequest(
        "worker-test",
        source,
        hashlib.sha256(source.encode()).hexdigest(),
        rows,
        hashlib.sha256(data.encode()).hexdigest(),
        (),
        42,
        SandboxBudget(10, 10, 64_000_000, 1, 16, 0, 0, 4096),
    )


def test_callback_runs_once_on_final_row_with_only_supplied_history() -> None:
    request = _request(
        "calls = 0\n"
        "def on_bar(context, bar):\n"
        "    global calls\n"
        "    calls += 1\n"
        "    assert calls == 1\n"
        "    assert context['index'] == 1\n"
        "    assert context['seed'] == 42\n"
        "    assert context['history'] == ({'close': '99'}, {'close': '100'})\n"
        "    assert bar == {'close': '100'}\n"
        "    return []\n"
    )
    assert execute(request) == SandboxResponse("worker-test", "ok", ())


def test_callback_receives_copies_without_mutating_request_or_current_bar() -> None:
    request = _request(
        "def on_bar(context, bar):\n"
        "    context['history'][-1]['close'] = 'changed'\n"
        "    assert bar['close'] == '100'\n"
        "    bar['close'] = 'also changed'\n"
        "    return []\n"
    )
    original = request.data_rows
    assert execute(request).status == "ok"
    assert request.data_rows == original


def test_empty_history_does_not_invoke_callback() -> None:
    response = execute(_request("def on_bar(context, bar): raise AssertionError()", empty=True))
    assert response.status == "ok"
    assert response.intents == ()


def test_valid_order_fields_survive_worker_and_wire_validation() -> None:
    source = (
        "def on_bar(context, bar):\n"
        "    return [{'instrument_id': 'ETF-DEMO', 'side': 'buy', 'order_type': 'limit',\n"
        "             'quantity': '2.5', 'limit_price': '99.95', 'stop_price': None}]\n"
    )
    response = execute(_request(source))
    assert response.intents == (WireIntent("ETF-DEMO", "buy", "limit", "2.5", "99.95"),)
    assert decode_response(encode_response(response)) == response


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("def broken(:", "strategy_load_failed"),
        ("raise ValueError('PRIVATE_MARKER')", "strategy_load_failed"),
        ("on_bar = 12", "callback_missing"),
        (
            "def on_bar(context, bar): raise ValueError('PRIVATE_MARKER')",
            "strategy_callback_failed",
        ),
        (
            "def on_bar(context, bar): raise SystemExit('PRIVATE_MARKER')",
            "strategy_callback_failed",
        ),
        ("def on_bar(context, bar): return None", "invalid_intent"),
        ("def on_bar(context, bar): return [{}]", "invalid_intent"),
        ("def on_bar(context, bar): return [[]]", "invalid_intent"),
        ("def on_bar(context, bar): return [{}] * 257", "invalid_intent"),
    ],
)
def test_failures_have_stable_sanitized_response(source: str, code: str) -> None:
    response = execute(_request(source))
    assert response.status == "error"
    assert response.error_code == code
    assert response.intents == ()
    assert response.diagnostic == "Strategy execution failed."
    assert b"PRIVATE_MARKER" not in encode_response(response)


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "0", "-1", True])
def test_invalid_economic_intent_is_rejected(quantity: object) -> None:
    intent = {
        "instrument_id": "ETF-DEMO",
        "side": "buy",
        "order_type": "market",
        "quantity": quantity,
        "limit_price": None,
        "stop_price": None,
    }
    source = f"def on_bar(context, bar): return [{intent!r}]"
    response = execute(_request(source))
    assert response.status == "error"
    assert response.error_code == "invalid_intent"


def test_emit_never_exceeds_requested_output_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[bytes] = []

    def capture(fd: int, payload: bytes) -> int:
        assert fd == 1
        writes.append(payload)
        return len(payload)

    monkeypatch.setattr("quant_terminal.sandbox.strategy_worker.os.write", capture)
    response = SandboxResponse("worker-test", "ok", ())
    _emit(response, 1)
    assert writes == []
    _emit(response, 4096)
    assert len(writes) == 1
    assert decode_response(writes[0]) == response


def test_nonempty_dependencies_fail_before_source_execution() -> None:
    request = replace(
        _request("raise ValueError('must not load')"),
        dependencies=(("example==1.0", "a" * 64),),
    )
    response = execute(request)
    assert response.status == "error"
    assert response.error_code == "dependencies_unsupported"


def test_buffered_strategy_print_is_drained_before_protocol_restore() -> None:
    import subprocess
    import sys

    from quant_terminal.sandbox.protocol import encode_request

    request = _request(
        "def on_bar(context, bar):\n"
        "    print('BUFFERED_PRIVATE_MARKER')\n"
        "    raise ValueError('BUFFERED_PRIVATE_MARKER')\n"
    )
    # Known harmless callback in a separate process exercises real TextIO
    # buffering and interpreter shutdown without altering pytest descriptors.
    result = subprocess.run(
        (
            sys.executable,
            "-c",
            "from quant_terminal.sandbox.strategy_worker import main;raise SystemExit(main())",
        ),
        input=encode_request(request),
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert b"BUFFERED_PRIVATE_MARKER" not in result.stdout + result.stderr
    response = decode_response(result.stdout)
    assert response.status == "error"
    assert response.error_code == "strategy_callback_failed"
