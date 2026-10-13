"""Adversarial wire-message validation at the isolated strategy boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from quant_terminal.sandbox.protocol import (
    MAX_RESPONSE_BYTES,
    ProtocolError,
    SandboxBudget,
    SandboxRequest,
    SandboxResponse,
    WireIntent,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)


def _request() -> SandboxRequest:
    source = "def on_bar(context, bar): return []"
    rows = ((("close", "100.25"), ("timestamp", "2026-01-01T00:00:00Z")),)
    data = [dict(row) for row in rows]
    data_blob = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return SandboxRequest(
        job_id="job-123",
        source=source,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        data_rows=rows,
        data_sha256=hashlib.sha256(data_blob.encode("ascii")).hexdigest(),
        dependencies=(),
        seed=42,
        budget=SandboxBudget(10, 10, 64_000_000, 1, 16, 0, 0, 4096),
    )


def _response() -> SandboxResponse:
    return SandboxResponse(
        job_id="job-123",
        status="ok",
        intents=(WireIntent("ETF-DEMO", "buy", "limit", "2.5", "99.95"),),
    )


def test_request_round_trip_and_hash_verification() -> None:
    request = _request()
    assert decode_request(encode_request(request)) == request
    with pytest.raises(ProtocolError, match="source hash mismatch"):
        encode_request(replace(request, source="print(1)"))
    with pytest.raises(ProtocolError, match="data hash mismatch"):
        encode_request(replace(request, data_rows=((("close", "101"),),)))


def test_request_rejects_unknown_duplicate_and_oversized_fields() -> None:
    payload = json.loads(encode_request(_request()))
    payload["unknown"] = "value"
    with pytest.raises(ProtocolError, match="fields"):
        decode_request(json.dumps(payload).encode())
    with pytest.raises(ProtocolError, match="duplicate"):
        decode_request(b'{"version":1,"version":1}')
    with pytest.raises(ProtocolError, match="size"):
        decode_request(b" " * 2_000_001)


def test_request_rejects_path_shaped_job_id_and_bool_budget() -> None:
    with pytest.raises(ProtocolError, match="job ID"):
        encode_request(replace(_request(), job_id="../private"))
    request = _request()
    with pytest.raises(ProtocolError, match="process_count"):
        encode_request(replace(request, budget=replace(request.budget, process_count=True)))
    with pytest.raises(ProtocolError, match="source"):
        encode_request(replace(request, source="\ud800"))


def test_response_round_trip_and_invalid_intents() -> None:
    response = _response()
    assert decode_response(encode_response(response)) == response
    for quantity in ("NaN", "Infinity", "-1", "0", "1e100", "1" * 100):
        bad = replace(response, intents=(replace(response.intents[0], quantity=quantity),))
        with pytest.raises(ProtocolError):
            encode_response(bad)
    bad_price = replace(response, intents=(replace(response.intents[0], limit_price=None),))
    with pytest.raises(ProtocolError, match="limit price"):
        encode_response(bad_price)


def test_response_rejects_oversized_or_unknown_messages() -> None:
    with pytest.raises(ProtocolError, match="size"):
        decode_response(b"x" * (MAX_RESPONSE_BYTES + 1))
    payload = json.loads(encode_response(_response()))
    payload["intents"][0]["ledger_mutation"] = "approve"
    with pytest.raises(ProtocolError, match="fields"):
        decode_response(json.dumps(payload).encode())
    payload = json.loads(encode_response(_response()))
    payload["version"] = 2
    with pytest.raises(ProtocolError, match="version"):
        decode_response(json.dumps(payload).encode())
    with pytest.raises(ProtocolError, match="diagnostic"):
        encode_response(replace(_response(), diagnostic="\ud800"))


def test_error_response_cannot_carry_orders() -> None:
    response = replace(_response(), status="error", error_code="StrategyError")
    with pytest.raises(ProtocolError, match="error response"):
        encode_response(response)
    valid = replace(response, intents=(), diagnostic="bounded failure")
    assert decode_response(encode_response(valid)) == valid


def test_excessive_json_integer_has_structured_protocol_failure() -> None:
    with pytest.raises(ProtocolError, match="invalid JSON"):
        decode_response(b'{"x":' + b"1" * 5000 + b"}")
