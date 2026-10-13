"""Headless research CLI; the GUI and HTTP adapter share its application service."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import cast

from quant_terminal import __version__
from quant_terminal.advanced_assets.service import research as asset_research
from quant_terminal.application.jobs import KINDS, TERMINAL, JobManager, JobStore
from quant_terminal.application.service import ResearchService
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import canonical_json
from quant_terminal.paper.service import PaperService
from quant_terminal.portfolio_research.service import research as portfolio_research
from quant_terminal.release.distribution import build_candidate, verify_candidate
from quant_terminal.release.storage import (
    backup_state,
    inspect_schema,
    restore_state,
    upgrade_schema,
)
from quant_terminal.sandbox.admission import capabilities, reject_isolated_python
from quant_terminal.sandbox.podman import SandboxUnavailable


def _request(path: str) -> dict[str, object]:
    raw = Path(path).read_bytes()
    if len(raw) > 10_000_000:
        raise DomainError("Request file exceeds 10 MB")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise DomainError("Expected a JSON request object")
    canonical_json(payload)
    return cast(dict[str, object], payload)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="quant", description="AlgoAlpha local research terminal")
    result.add_argument("--version", action="version", version=__version__)
    default_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AlgoAlpha" / "research"
    result.add_argument("--state-dir", type=Path, default=default_root)
    result.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    result.add_argument(
        "--isolation-config",
        type=Path,
        help="Trusted operator isolation configuration; never a strategy request field",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for kind in sorted(KINDS):
        command = commands.add_parser(kind)
        command.add_argument("--request", required=True)
        command.add_argument("--timeout", type=int, default=300)
    importer = commands.add_parser("data-import")
    importer.add_argument("--request", required=True)
    report = commands.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("run_ids", nargs="+")
    commands.add_parser("runs")
    commands.add_parser("strategies")
    commands.add_parser("sandbox-capabilities")
    isolated = commands.add_parser("sandbox-run")
    isolated.add_argument("--request")
    probe = commands.add_parser("sandbox-probe")
    probe.add_argument("--image", required=True)
    probe.add_argument("--seccomp-profile", required=True)
    commands.add_parser("datasets")
    portfolio = commands.add_parser("portfolio-research")
    portfolio.add_argument("--request", required=True)
    advanced = commands.add_parser("advanced-assets")
    advanced.add_argument("--request", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    for name in ("database-inspect", "database-upgrade"):
        database = commands.add_parser(name)
        database.add_argument("--database", type=Path, required=True)
        database.add_argument(
            "--kind", choices=("jobs", "experiments", "paper", "risk"), required=True
        )
    candidate = commands.add_parser("release-build")
    candidate.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("release-verify")
    verify.add_argument("--archive", type=Path, required=True)
    commands.add_parser("paper-sessions")
    paper_create = commands.add_parser("paper-create")
    paper_create.add_argument("--request", required=True)
    paper_status = commands.add_parser("paper-status")
    paper_status.add_argument("session_id")
    paper_command = commands.add_parser("paper-command")
    paper_command.add_argument("session_id")
    paper_command.add_argument("action", choices=("reconnect", "disconnect", "events", "fills"))
    paper_command.add_argument("--request", required=True)
    paper_command.add_argument(
        "--reconciliation", help="JSON reconnect request for this invocation"
    )
    job = commands.add_parser("job")
    job.add_argument("job_id")
    job.add_argument("--cancel", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--workers", type=int, default=2)
    serve.add_argument("--timeout", type=int, default=300)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root, project = args.state_dir.resolve(), args.project_root.resolve()
        if args.command in {
            "backup",
            "restore",
            "database-inspect",
            "database-upgrade",
            "release-build",
            "release-verify",
        }:
            if args.command == "backup":
                operation_value = backup_state(args.state_dir, args.output)
            elif args.command == "restore":
                operation_value = restore_state(args.archive, args.destination)
            elif args.command == "database-inspect":
                operation_value = inspect_schema(args.database, args.kind)
            elif args.command == "database-upgrade":
                operation_value = upgrade_schema(args.database, args.kind)
            elif args.command == "release-build":
                operation_value = build_candidate(args.project_root, args.output)
            else:
                operation_value = verify_candidate(args.archive)
            print(canonical_json(operation_value).decode())
            return 0
        if args.command == "advanced-assets":
            print(canonical_json(asset_research(_request(args.request))).decode())
            return 0
        if args.command == "portfolio-research":
            print(canonical_json(portfolio_research(_request(args.request))).decode())
            return 0
        if args.command == "sandbox-capabilities":
            if args.isolation_config is None:
                print(canonical_json(capabilities()).decode())
            else:
                from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig

                with IsolationGateway(
                    root, project, OperatorIsolationConfig.load(args.isolation_config)
                ) as gateway:
                    print(canonical_json(gateway.capability()).decode())
            return 0
        if args.command.startswith("paper-"):
            paper = PaperService(root)
            if args.command == "paper-create":
                paper_value: object = paper.create(_request(args.request))
            elif args.command == "paper-sessions":
                paper_value = paper.list()
            elif args.command == "paper-status":
                paper_value = paper.get(args.session_id)
            else:
                if args.reconciliation:
                    paper.command(args.session_id, "reconnect", _request(args.reconciliation))
                paper_value = paper.command(args.session_id, args.action, _request(args.request))
            print(canonical_json(paper_value).decode())
            return 0
        if args.command == "sandbox-run":
            if args.isolation_config is None:
                reject_isolated_python()
            from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig

            with IsolationGateway(
                root, project, OperatorIsolationConfig.load(args.isolation_config)
            ) as gateway:
                gateway.require_ready()
                if args.request is None:
                    raise DomainError("Isolated request file required")
                with Path(args.request).open("rb") as stream:
                    raw = stream.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise DomainError("Isolated request exceeds 2 MB")
                job = gateway.submit(json.loads(raw))
                assert gateway.scheduler is not None
                while job["status"] not in TERMINAL:
                    if gateway.failure is not None:
                        raise DomainError("Isolated gateway unavailable")
                    time.sleep(0.05)
                    job = gateway.job_view(gateway.scheduler.store.get(str(job["id"])))
                print(canonical_json(job).decode())
                return 0 if job["status"] == "succeeded" else 1
        if args.command == "sandbox-probe":
            from dataclasses import asdict

            from quant_terminal.sandbox.podman import PodmanBackend, PodmanConfig

            backend = PodmanBackend(PodmanConfig(args.image, args.seccomp_profile))
            capability = backend.probe()
            probe_value = asdict(capability)
            if capability.available:
                probe_value["identity"] = backend.identity()
            print(canonical_json(probe_value).decode())
            return 0 if capability.available else 1
        if args.command == "serve":
            import uvicorn

            from quant_terminal.api import create_app

            serve_gateway: IsolationGateway | None = None
            if args.isolation_config is not None:
                from quant_terminal.sandbox.gateway import IsolationGateway, OperatorIsolationConfig

                serve_gateway = IsolationGateway(
                    root, project, OperatorIsolationConfig.load(args.isolation_config)
                )

            uvicorn.run(
                create_app(
                    root,
                    project,
                    workers=args.workers,
                    timeout=args.timeout,
                    isolated_gateway=serve_gateway,
                ),
                host="127.0.0.1",
                port=args.port,
            )
            return 0
        if args.command in KINDS:
            payload = _request(args.request)
            with JobManager(root, project, workers=1, timeout=args.timeout) as manager:
                job = manager.submit(args.command, payload)
                identity = str(job["id"])
                print(
                    canonical_json({"job_id": identity, "status": "queued"}).decode(),
                    file=sys.stderr,
                    flush=True,
                )
                while job["status"] not in TERMINAL:
                    if not manager.healthy:
                        raise DomainError("Research scheduler stopped unexpectedly")
                    time.sleep(0.1)
                    job = manager.store.get(identity)
            print(canonical_json(job).decode())
            return 0 if job["status"] == "succeeded" else 1
        value: object
        if args.command == "job":
            store = JobStore(root)
            value = store.cancel(args.job_id) if args.cancel else store.get(args.job_id)
        else:
            service = ResearchService(root, project)
            if args.command == "data-import":
                value = service.import_data(_request(args.request))
            elif args.command == "runs":
                value = service.runs()
            elif args.command == "strategies":
                value = service.runtime.strategies()
            elif args.command == "datasets":
                value = service.runtime.snapshots()
            elif args.command == "compare":
                value = service.compare(args.run_ids)
            else:
                # Exclusive creation prevents an accidental overwrite of a local report.
                with args.output.open("xb") as handle:
                    handle.write(service.html_report(args.run_id))
                value = {"run_id": args.run_id, "report": str(args.output.resolve())}
        print(canonical_json(value).decode())
        return 0
    except (DomainError, OSError, ValueError, RecursionError, SandboxUnavailable) as exc:
        print(
            canonical_json({"error": type(exc).__name__, "message": str(exc)}).decode(),
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print('{"error":"Interrupted"}', file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
