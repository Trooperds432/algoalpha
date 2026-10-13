"""Bounded JSON wire protocol for an isolated strategy process.

Parsing this protocol does not grant authority to execute a strategy. Admission and
OS isolation are separate, mandatory controls on the trusted side of the boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

VERSION = 1
MAX_REQUEST_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 128_000
MAX_SOURCE_BYTES = 512_000
MAX_DATA_ROWS = 10_000
MAX_ROW_FIELDS = 32
MAX_INTENTS = 256
MAX_DIAGNOSTIC_BYTES = 2_000
MAX_DEPENDENCIES = 32

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_DEPENDENCY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}==[A-Za-z0-9][A-Za-z0-9_.+!-]{0,63}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?\Z")

type DataRow = tuple[tuple[str, str], ...]


class ProtocolError(ValueError):
    """Malformed, oversized, or unsupported protocol message."""


@dataclass(frozen=True, slots=True)
class SandboxBudget:
    wall_seconds: int
    cpu_seconds: int
    memory_bytes: int
    process_count: int
    open_files: int
    scratch_bytes: int
    scratch_files: int
    output_bytes: int


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    job_id: str
    source: str
    source_sha256: str
    data_rows: tuple[DataRow, ...]
    data_sha256: str
    dependencies: tuple[tuple[str, str], ...]
    seed: int
    budget: SandboxBudget


@dataclass(frozen=True, slots=True)
class WireIntent:
    instrument_id: str
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit", "stop", "stop_limit"]
    quantity: str
    limit_price: str | None = None
    stop_price: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxResponse:
    job_id: str
    status: Literal["ok", "error"]
    intents: tuple[WireIntent, ...]
    diagnostic: str = ""
    error_code: str | None = None


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON field")
        result[key] = value
    return result


def _decode(blob: bytes, limit: int) -> dict[str, object]:
    if not isinstance(blob, bytes) or not blob or len(blob) > limit:
        raise ProtocolError("invalid message size")
    try:
        result = json.loads(
            blob.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError("nonfinite JSON number")),
        )
    except ProtocolError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("invalid JSON") from exc
    if not isinstance(result, dict):
        raise ProtocolError("top-level JSON object required")
    return result


def _fields(value: object, expected: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProtocolError("unexpected or missing fields")
    return value


def _text(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ProtocolError(f"invalid {label}")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError(f"invalid {label}")
    return value


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utf8(value: str, label: str, maximum: int) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProtocolError(f"invalid {label}") from exc
    if len(encoded) > maximum:
        raise ProtocolError(f"invalid {label} size")
    return encoded


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )


def _budget(value: object) -> SandboxBudget:
    fields = _fields(value, set(SandboxBudget.__dataclass_fields__))
    limits = {
        "wall_seconds": (1, 3_600),
        "cpu_seconds": (1, 3_600),
        "memory_bytes": (16_777_216, 8_589_934_592),
        "process_count": (1, 64),
        "open_files": (8, 4_096),
        "scratch_bytes": (0, 1_073_741_824),
        "scratch_files": (0, 10_000),
        "output_bytes": (1, MAX_RESPONSE_BYTES),
    }
    values = {key: _integer(fields[key], key, *limits[key]) for key in limits}
    return SandboxBudget(**values)


def _rows(value: object) -> tuple[DataRow, ...]:
    if not isinstance(value, list) or len(value) > MAX_DATA_ROWS:
        raise ProtocolError("invalid data rows")
    rows: list[DataRow] = []
    for raw in value:
        if not isinstance(raw, dict) or not 1 <= len(raw) <= MAX_ROW_FIELDS:
            raise ProtocolError("invalid data row")
        row: list[tuple[str, str]] = []
        for key, entry in raw.items():
            _text(key, "data field", _FIELD)
            if not isinstance(entry, str):
                raise ProtocolError("invalid data value")
            _utf8(entry, "data value", 256)
            row.append((key, entry))
        rows.append(tuple(sorted(row)))
    return tuple(rows)


def _dependencies(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or len(value) > MAX_DEPENDENCIES:
        raise ProtocolError("invalid dependencies")
    dependencies: list[tuple[str, str]] = []
    for name, digest in value.items():
        dependencies.append((_text(name, "dependency", _DEPENDENCY), _text(digest, "hash", _HASH)))
    return tuple(sorted(dependencies))


def decode_request(blob: bytes) -> SandboxRequest:
    """Validate a complete request; never import or execute its source."""
    fields = _fields(
        _decode(blob, MAX_REQUEST_BYTES),
        {
            "version",
            "job_id",
            "source",
            "source_sha256",
            "data_rows",
            "data_sha256",
            "dependencies",
            "seed",
            "budget",
        },
    )
    if _integer(fields["version"], "version", VERSION, VERSION) != VERSION:
        raise ProtocolError("unsupported version")
    source = fields["source"]
    if not isinstance(source, str) or not source:
        raise ProtocolError("invalid source size")
    source_bytes = _utf8(source, "source", MAX_SOURCE_BYTES)
    source_sha256 = _text(fields["source_sha256"], "source hash", _HASH)
    if _digest(source_bytes) != source_sha256:
        raise ProtocolError("source hash mismatch")
    rows = _rows(fields["data_rows"])
    data_sha256 = _text(fields["data_sha256"], "data hash", _HASH)
    if _digest(_canonical([dict(row) for row in rows])) != data_sha256:
        raise ProtocolError("data hash mismatch")
    return SandboxRequest(
        job_id=_text(fields["job_id"], "job ID", _JOB_ID),
        source=source,
        source_sha256=source_sha256,
        data_rows=rows,
        data_sha256=data_sha256,
        dependencies=_dependencies(fields["dependencies"]),
        seed=_integer(fields["seed"], "seed", 0, 2**63 - 1),
        budget=_budget(fields["budget"]),
    )


def encode_request(request: SandboxRequest) -> bytes:
    """Serialize and validate a request using exactly the accepted wire schema."""
    payload = {
        "version": VERSION,
        "job_id": request.job_id,
        "source": request.source,
        "source_sha256": request.source_sha256,
        "data_rows": [dict(row) for row in request.data_rows],
        "data_sha256": request.data_sha256,
        "dependencies": dict(request.dependencies),
        "seed": request.seed,
        "budget": {key: getattr(request.budget, key) for key in SandboxBudget.__dataclass_fields__},
    }
    blob = _canonical(payload)
    decode_request(blob)
    return blob


def _positive_decimal(value: object, label: str) -> str:
    text = _text(value, label, _DECIMAL)
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ProtocolError(f"invalid {label}") from exc
    if not number.is_finite() or number <= 0:
        raise ProtocolError(f"invalid {label}")
    return text


def _optional_price(value: object, label: str) -> str | None:
    return None if value is None else _positive_decimal(value, label)


def _intent(value: object) -> WireIntent:
    fields = _fields(
        value,
        {"instrument_id", "side", "order_type", "quantity", "limit_price", "stop_price"},
    )
    side = fields["side"]
    order_type = fields["order_type"]
    if side not in ("buy", "sell") or order_type not in ("market", "limit", "stop", "stop_limit"):
        raise ProtocolError("invalid order side or type")
    limit_price = _optional_price(fields["limit_price"], "limit price")
    stop_price = _optional_price(fields["stop_price"], "stop price")
    if (limit_price is not None) != (order_type in ("limit", "stop_limit")):
        raise ProtocolError("invalid limit price for order type")
    if (stop_price is not None) != (order_type in ("stop", "stop_limit")):
        raise ProtocolError("invalid stop price for order type")
    return WireIntent(
        instrument_id=_text(fields["instrument_id"], "instrument ID", _IDENTIFIER),
        side=side,
        order_type=order_type,
        quantity=_positive_decimal(fields["quantity"], "quantity"),
        limit_price=limit_price,
        stop_price=stop_price,
    )


def decode_response(blob: bytes) -> SandboxResponse:
    """Validate bounded order proposals without constructing domain order objects."""
    fields = _fields(
        _decode(blob, MAX_RESPONSE_BYTES),
        {"version", "job_id", "status", "intents", "diagnostic", "error_code"},
    )
    _integer(fields["version"], "version", VERSION, VERSION)
    status = fields["status"]
    if status not in ("ok", "error"):
        raise ProtocolError("invalid response status")
    raw_intents = fields["intents"]
    if not isinstance(raw_intents, list) or len(raw_intents) > MAX_INTENTS:
        raise ProtocolError("invalid intent count")
    intents = tuple(_intent(value) for value in raw_intents)
    diagnostic = fields["diagnostic"]
    if not isinstance(diagnostic, str):
        raise ProtocolError("invalid diagnostic")
    _utf8(diagnostic, "diagnostic", MAX_DIAGNOSTIC_BYTES)
    error_code = fields["error_code"]
    if status == "ok" and (error_code is not None or diagnostic):
        raise ProtocolError("success response cannot contain error")
    if status == "error" and (intents or not isinstance(error_code, str)):
        raise ProtocolError("error response cannot contain intents")
    validated_code = None if error_code is None else _text(error_code, "error code", _FIELD)
    return SandboxResponse(
        job_id=_text(fields["job_id"], "job ID", _JOB_ID),
        status=status,
        intents=intents,
        diagnostic=diagnostic,
        error_code=validated_code,
    )


def encode_response(response: SandboxResponse) -> bytes:
    payload = {
        "version": VERSION,
        "job_id": response.job_id,
        "status": response.status,
        "intents": [
            {key: getattr(intent, key) for key in WireIntent.__dataclass_fields__}
            for intent in response.intents
        ],
        "diagnostic": response.diagnostic,
        "error_code": response.error_code,
    }
    blob = _canonical(payload)
    decode_response(blob)
    return blob
