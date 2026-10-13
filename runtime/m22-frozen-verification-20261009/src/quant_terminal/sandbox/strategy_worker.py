"""Container-only execution of a bounded strategy request.

This file is copied into a purpose-built image. It is never an isolation
boundary by itself; the trusted host must launch and supervise the container.
The host must send only data already visible to the strategy at this instant.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any, cast

# Python -I excludes the script directory. Add only the immutable image code.
if __name__ == "__main__":
    sys.path.insert(0, "/opt/algoalpha")

from quant_terminal.sandbox.protocol import (  # noqa: E402
    MAX_INTENTS,
    MAX_REQUEST_BYTES,
    ProtocolError,
    SandboxRequest,
    SandboxResponse,
    WireIntent,
    decode_request,
    encode_response,
)


def _error(job_id: str, code: str) -> SandboxResponse:
    # Never include source, exception messages, tracebacks or environment values.
    return SandboxResponse(job_id, "error", (), "Strategy execution failed.", code)


def _bounded_intents(raw: object) -> tuple[WireIntent, ...]:
    if type(raw) not in (list, tuple):
        raise ProtocolError("invalid intents")
    entries = cast("list[object] | tuple[object, ...]", raw)
    if len(entries) > MAX_INTENTS:
        raise ProtocolError("invalid intents")
    intents: list[WireIntent] = []
    for entry in entries:
        if type(entry) is not dict:
            raise ProtocolError("invalid intent")
        fields = entry
        if set(fields) != set(WireIntent.__dataclass_fields__):
            raise ProtocolError("invalid intent fields")
        intents.append(WireIntent(**fields))
    return tuple(intents)


def execute(request: SandboxRequest) -> SandboxResponse:
    """Run owner source with only the request's as-of rows passed as data.

    This is ordinary Python execution inside the container, not a Python-level
    attempt to restrict imports or system calls. Future rows must be withheld by
    the trusted scheduler; same-process Python cannot enforce no-lookahead.
    """
    if request.dependencies:
        return _error(request.job_id, "dependencies_unsupported")
    namespace: dict[str, Any] = {"__name__": "__strategy__", "__builtins__": __builtins__}
    try:
        compiled = compile(request.source, "<strategy>", "exec", dont_inherit=True)
        exec(compiled, namespace)
    except BaseException:  # hostile strategy code can raise SystemExit/KeyboardInterrupt
        return _error(request.job_id, "strategy_load_failed")
    callback = namespace.get("on_bar")
    if not callable(callback):
        return _error(request.job_id, "callback_missing")
    try:
        # One process per as-of observation. Every byte in this request is
        # already visible; never stage a future row in this Python interpreter.
        context: Mapping[str, object] = {
            "index": len(request.data_rows) - 1,
            "seed": request.seed,
            "history": tuple(dict(row) for row in request.data_rows),
        }
        intents = (
            _bounded_intents(callback(context, dict(request.data_rows[-1])))
            if request.data_rows
            else ()
        )
        response = SandboxResponse(request.job_id, "ok", intents)
        encode_response(response)
        return response
    except ProtocolError:
        return _error(request.job_id, "invalid_intent")
    except BaseException:
        return _error(request.job_id, "strategy_callback_failed")


def _emit(response: SandboxResponse, output_limit: int) -> None:
    try:
        payload = encode_response(response)
    except ProtocolError:
        payload = encode_response(_error(response.job_id, "invalid_intent"))
    if len(payload) > output_limit:
        payload = encode_response(_error(response.job_id, "output_limit"))
    if len(payload) <= output_limit:
        os.write(1, payload)


def main() -> int:
    try:
        blob = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        request = decode_request(blob)
    except (OSError, ProtocolError):
        _emit(_error("invalid", "invalid_request"), 128_000)
        return 2
    # Strategy writes must not become trusted protocol output. The host still
    # caps stdout/stderr and validates the single response independently.
    stdout_flush = sys.stdout.flush
    stderr_flush = sys.stderr.flush
    stdout_flush()
    stderr_flush()
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_fd, 1)
            os.dup2(null_fd, 2)
        finally:
            os.close(null_fd)
        response = execute(request)
    finally:
        # TextIO buffers retain prints while descriptor 1/2 points to devnull.
        # Drain them before restoring the protocol descriptors; otherwise a
        # later interpreter flush could append strategy text to the response.
        for flush in (stdout_flush, stderr_flush):
            try:
                flush()
            except (OSError, ValueError):
                pass
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
    _emit(response, request.budget.output_bytes)
    return 0 if response.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
