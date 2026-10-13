"""Required bounded public decision records; decisions precede durable job commits."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from quant_terminal import __version__
from quant_terminal.core.exceptions import DomainError
from quant_terminal.sandbox.protocol import SandboxBudget


class PublicSecurityAudit:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        reason: str,
        *,
        backend: object,
        image: str,
        caps: SandboxBudget,
        value: object = None,
        job_id: str | None = None,
        status: str | None = None,
    ) -> None:
        if event not in {
            "public_admission_rejected",
            "public_admission_decision",
            "public_cancel_rejected",
            "public_cancel_decision",
        } or reason not in {
            "gateway_unavailable",
            "invalid_packet",
            "invalid_execution_mode",
            "invalid_request",
            "budget_rejected",
            "source_or_request_invalid",
            "queue_full",
            "admit",
            "cancel",
            "unchanged",
            "job_unavailable",
            "invalid_json",
            "request_too_large",
            "request_file_required",
            "request_file_unavailable",
            "invalid_host",
            "invalid_origin",
            "invalid_content_type",
        }:
            raise DomainError("Invalid trusted public audit decision")
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "reason": reason,
            "phase": "decision_before_commit",
            "backend": "podman",
            "engine_version": __version__,
        }
        if isinstance(backend, dict):
            encoded = json.dumps(backend, sort_keys=True).encode()
            payload["backend_identity_sha256"] = hashlib.sha256(encoded).hexdigest()
            version = backend.get("engine_version")
            if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", version):
                payload["backend_version"] = version
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            payload["image"] = image
        if job_id is not None and re.fullmatch(r"[0-9a-f]{32}", job_id):
            payload["job_id"] = job_id
        if status in {
            "queued",
            "running",
            "cancelling",
            "cancelled",
            "succeeded",
            "failed",
            "interrupted",
        }:
            payload["target_status"] = status
        if isinstance(value, Mapping):
            package = value.get("source_package")
            if isinstance(package, Mapping):
                source = package.get("source")
                if isinstance(source, str) and len(source) <= 512_000:
                    try:
                        raw = source.encode("utf-8")
                    except UnicodeError:
                        raw = b""
                    if 0 < len(raw) <= 512_000:
                        payload["source_sha256"] = hashlib.sha256(raw).hexdigest()
            request = value.get("request")
            if isinstance(request, Mapping):
                budget = request.get("budget")
                if isinstance(budget, Mapping):
                    payload["budget"] = {
                        key: budget[key]
                        for key in SandboxBudget.__dataclass_fields__
                        if key in budget
                        and type(budget[key]) is int
                        and 0 <= budget[key] <= getattr(caps, key)
                    }
        try:
            with self._lock, self.path.open("ab") as stream:
                raw_line = json.dumps(payload, sort_keys=True).encode() + b"\n"
                if len(raw_line) > 4096:
                    raise OSError("Audit record exceeds bound")
                if stream.write(raw_line) != len(raw_line):
                    raise OSError("Incomplete audit write")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise DomainError("Required public security audit unavailable") from exc
