"""Trusted operator configuration for a distinct isolated research gateway.

HTTP requests cannot supply configuration, receipt paths or approval flags.
The default terminal has no gateway. Acceptance-testing receipts are identified
explicitly and do not constitute final release approval.
"""

from __future__ import annotations

import hashlib
import json
import platform
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Self, cast

from quant_terminal import __version__
from quant_terminal.application.isolated_jobs import IsolatedJobScheduler, RuntimeOwner
from quant_terminal.application.runtime import _object, _text
from quant_terminal.core.exceptions import DomainError
from quant_terminal.sandbox.admission import capabilities, reject_isolated_python
from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig, SandboxUnavailable
from quant_terminal.sandbox.protocol import SandboxBudget
from quant_terminal.sandbox.runtime_owner import IsolationRuntimeOwner
from quant_terminal.sandbox.wsl_keeper import WslKeeper

PUBLIC_BUDGET_CAPS = SandboxBudget(5, 3, 64_000_000, 4, 32, 0, 0, 4096)


def host_identity() -> dict[str, str]:
    return {
        "system": platform.system(),
        "version": platform.version(),
        "machine": platform.machine(),
    }


def code_hashes(project: Path) -> dict[str, str]:
    return {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((project / "src").rglob("*.py"))
    }


def _read_object(path: Path, limit: int = 64_000) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise DomainError("Operator metadata exceeds size limit")
        value = json.loads(raw)
    except (OSError, ValueError, RecursionError) as exc:
        raise DomainError("Operator metadata unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise DomainError("Operator metadata object required")
    return value


@dataclass(frozen=True, slots=True)
class OperatorIsolationConfig:
    image: str
    seccomp_profile: str
    audit_path: Path
    acceptance_path: Path

    @classmethod
    def load(cls, path: Path) -> OperatorIsolationConfig:
        fields = {"image", "seccomp_profile", "audit_path", "acceptance_path"}
        data = _object(_read_object(path), fields, fields)
        return cls(
            _text(data["image"]),
            _text(data["seccomp_profile"]),
            Path(_text(data["audit_path"])),
            Path(_text(data["acceptance_path"])),
        )


class IsolationGateway:
    def __init__(self, root: Path, project_root: Path, config: OperatorIsolationConfig) -> None:
        self.root, self.project = Path(root).resolve(), Path(project_root).resolve(strict=True)
        self.config = config
        self.scheduler: IsolatedJobScheduler | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.failure: str | None = None
        self.scope: str | None = None
        self.expected_backend: object = None

    def _receipt(self) -> dict[str, object]:
        fields = {
            "schema_version",
            "approved",
            "scope",
            "engine_version",
            "host",
            "source_hashes",
            "dependency_lock_sha256",
            "backend_identity",
            "evidence",
        }
        receipt = _object(_read_object(self.config.acceptance_path), fields, fields)
        if (
            type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1
            or receipt["approved"] is not True
        ):
            reject_isolated_python()
        if receipt["scope"] not in ("isolated_research", "acceptance_testing"):
            reject_isolated_python()
        if receipt["engine_version"] != __version__ or receipt["host"] != host_identity():
            reject_isolated_python()
        if receipt["source_hashes"] != code_hashes(self.project):
            reject_isolated_python()
        if (
            receipt["dependency_lock_sha256"]
            != hashlib.sha256((self.project / "requirements-dev.lock").read_bytes()).hexdigest()
        ):
            reject_isolated_python()
        evidence = receipt["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != {"hostile", "lifecycle", "review"}:
            reject_isolated_python()
        for item in evidence.values():
            entry = _object(item, {"path", "sha256"}, {"path", "sha256"})
            name = _text(entry["path"])
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or "\\" in name
                or ":" in name
                or relative.as_posix() != name
                or len(relative.parts) != 1
            ):
                reject_isolated_python()
            # Proofs travel with the trusted operator receipt, independently of
            # the installed source tree. Only sibling regular files are allowed.
            path = self.config.acceptance_path.parent / name
            if (
                path.is_symlink()
                or path.is_junction()
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]
            ):
                reject_isolated_python()
        return receipt

    def _owner(self) -> RuntimeOwner:
        return IsolationRuntimeOwner(
            PodmanBackend(
                PodmanConfig(
                    self.config.image,
                    self.config.seccomp_profile,
                    audit_path=self.config.audit_path,
                )
            ),
            WslKeeper(lifetime_seconds=900),
        )

    def __enter__(self) -> Self:
        # No source parsing, staging or claimed job precedes this operator gate.
        receipt = self._receipt()
        self.scope = cast(str, receipt["scope"])
        self.expected_backend = receipt["backend_identity"]
        self.scheduler = IsolatedJobScheduler(
            self.root, self.project, self._owner, research_root=self.root
        )
        self.scheduler.__enter__()
        try:
            self._require_ready()
            self.thread = threading.Thread(target=self._loop, daemon=True, name="isolated-gateway")
            self.thread.start()
        except BaseException:
            self.scheduler.close()
            raise
        return self

    def _require_ready(self) -> None:
        if self.scheduler is None or self.stop.is_set() or self.failure is not None:
            reject_isolated_python()
        receipt = self._receipt()
        if receipt["backend_identity"] != self.expected_backend:
            reject_isolated_python()
        with self.scheduler._lock:
            self.scheduler._ready_owner()
            if json.loads(cast(str, self.scheduler._identity)) != self.expected_backend:
                reject_isolated_python()

    def require_ready(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            reject_isolated_python()
        try:
            self._require_ready()
        except (SandboxUnavailable, OSError) as exc:
            raise DomainError("Isolated gateway unavailable") from exc

    def capability(self) -> dict[str, object]:
        result = capabilities()
        try:
            self.require_ready()
        except Exception:
            return result
        result["isolated_python"] = {
            "available": True,
            "scope": self.scope,
            "budget_caps": {
                key: getattr(PUBLIC_BUDGET_CAPS, key) for key in SandboxBudget.__dataclass_fields__
            },
        }
        return result

    def submit(self, value: object) -> dict[str, object]:
        self.require_ready()
        packet = _object(
            value,
            {"execution_mode", "source_package", "request"},
            {"execution_mode", "source_package", "request"},
        )
        if packet["execution_mode"] != "isolated_python":
            raise DomainError("Distinct isolated_python execution mode required")
        request = packet["request"]
        if not isinstance(request, Mapping):
            raise DomainError("Isolated research request object required")
        fields = set(SandboxBudget.__dataclass_fields__)
        budget = _object(request.get("budget"), fields, fields)
        for key, value in budget.items():
            if type(value) is not int or value < 0 or value > getattr(PUBLIC_BUDGET_CAPS, key):
                raise DomainError("Isolated request exceeds accepted budget caps")
        assert self.scheduler is not None
        try:
            job = self.scheduler.submit(
                packet["source_package"], cast(Mapping[str, object], request)
            )
        except (SandboxUnavailable, OSError) as exc:
            raise DomainError("Isolated gateway unavailable") from exc
        return self.job_view(job)

    @staticmethod
    def job_view(job: Mapping[str, object]) -> dict[str, object]:
        """Default API/CLI output never echoes submitted strategy text."""
        result = dict(job)
        packet = cast(Mapping[str, object], job["request"])
        package = cast(Mapping[str, object], packet["source_package"])
        result["request"] = {
            "execution_mode": "isolated_python",
            "source_sha256": package["source_sha256"],
            "config": packet["config"],
        }
        return result

    def _loop(self) -> None:
        assert self.scheduler is not None
        try:
            while not self.stop.wait(0.05):
                if self.scheduler.store.has_queued():
                    self._require_ready()
                    self.scheduler.run_next()
        except Exception:
            self.failure = "isolated_gateway_unavailable"
            self.stop.set()

    def close(self) -> None:
        self.stop.set()
        if self.scheduler is not None:
            self.scheduler.close()
        if self.thread is not None:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise DomainError("Isolated gateway shutdown incomplete")

    def __exit__(self, *args: object) -> None:
        self.close()
