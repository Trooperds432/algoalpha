"""Loopback research API over the same service used by the command line."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from quant_terminal import __version__
from quant_terminal.advanced_assets.service import capabilities as asset_capabilities
from quant_terminal.advanced_assets.service import research as asset_research
from quant_terminal.application.jobs import JobManager, JobNotFound, QueueFull
from quant_terminal.application.service import ResearchService
from quant_terminal.brokers.status import BROKER_STATUS
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import IntegrityError, RunNotFoundError, canonical_json
from quant_terminal.paper.service import PaperService
from quant_terminal.portfolio_research.service import capabilities as portfolio_capabilities
from quant_terminal.portfolio_research.service import research as portfolio_research
from quant_terminal.release.observability import (
    ObservabilityMiddleware,
    RuntimeMonitor,
    storage_health,
)
from quant_terminal.sandbox.admission import capabilities, reject_isolated_python
from quant_terminal.sandbox.gateway import IsolationGateway


def json_response(value: object, status: int = 200) -> Response:
    return Response(canonical_json(value), status_code=status, media_type="application/json")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int = 10_000_000) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                await json_response({"error": "Request body exceeds 10 MB"}, 413)(
                    scope, receive, send
                )
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


def create_app(
    root: Path,
    project_root: Path,
    *,
    workers: int = 2,
    timeout: int = 300,
    isolated_gateway: IsolationGateway | None = None,
) -> FastAPI:
    service = ResearchService(root, project_root)
    manager = JobManager(root, project_root, workers=workers, timeout=timeout)
    paper = PaperService(root)
    monitor = RuntimeMonitor(root / "logs")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with ExitStack() as contexts:
            contexts.enter_context(manager)
            if isolated_gateway is not None:
                contexts.enter_context(isolated_gateway)
            yield

    app = FastAPI(title="AlgoAlpha Research Terminal", version=__version__, lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.state.service, app.state.jobs = service, manager.store
    app.state.paper = paper
    app.state.monitor = monitor

    @app.middleware("http")
    async def local_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        authority = request.headers.get("host", "")
        if re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", authority) is None:
            return json_response({"error": "Loopback Host required"}, 403)
        origin = request.headers.get("origin")
        if origin is not None and origin != "http://" + authority:
            return json_response({"error": "Cross-origin requests are disabled"}, 403)
        if request.method in {"POST", "PUT", "PATCH"}:
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return json_response({"error": "application/json required"}, 415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> Response:
        status = 404 if isinstance(exc, (JobNotFound, RunNotFoundError)) else 422
        if isinstance(exc, QueueFull):
            status = 429
        if isinstance(exc, IntegrityError):
            status = 409
        return json_response({"error": type(exc).__name__, "message": str(exc)}, status)

    @app.get("/health")
    def health() -> Response:
        return json_response(
            {
                "status": "ok" if manager.healthy and monitor.healthy else "unhealthy",
                "mode": "research",
                "broker_orders_enabled": False,
            },
            200 if manager.healthy and monitor.healthy else 503,
        )

    @app.get("/metrics")
    def metrics() -> Response:
        return json_response(monitor.snapshot())

    @app.get("/ready")
    def ready() -> Response:
        storage = storage_health(root)
        healthy = manager.healthy and monitor.healthy and storage["healthy"] is True
        return json_response(
            {"ready": healthy, "storage": storage, "broker_orders_enabled": False},
            200 if healthy else 503,
        )

    @app.get("/broker/status")
    def broker_status() -> Response:
        return json_response(BROKER_STATUS)

    @app.get("/portfolio-research/capabilities")
    def research_capabilities() -> Response:
        return json_response(portfolio_capabilities())

    @app.get("/advanced-assets/capabilities")
    def advanced_asset_capabilities() -> Response:
        return json_response(asset_capabilities())

    @app.post("/advanced-assets")
    async def advanced_asset_request(request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise DomainError("Invalid asset JSON") from exc
        if not isinstance(payload, dict):
            raise DomainError("Asset request must be an object")
        return json_response(asset_research(payload))

    @app.post("/portfolio-research")
    async def portfolio(request: Request) -> Response:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise DomainError("Invalid research JSON") from exc
        if not isinstance(payload, dict):
            raise DomainError("Research request must be an object")
        return json_response(portfolio_research(payload))

    @app.post("/broker/connect")
    def broker_connect() -> Response:
        return json_response({"error": "disabled", **BROKER_STATUS}, 409)

    @app.post("/broker/orders")
    def broker_order() -> Response:
        return json_response({"error": "disabled", **BROKER_STATUS}, 409)

    def submission(kind: str) -> Callable[[dict[str, object]], Response]:
        def submit(payload: dict[str, object]) -> Response:
            return json_response(manager.submit(kind, payload), 202)

        return submit

    for path, kind in (
        ("backtests", "backtest"),
        ("optimisations", "optimize"),
        ("walk-forwards", "walk-forward"),
        ("monte-carlo", "monte-carlo"),
        ("robustness", "robustness"),
    ):
        app.add_api_route("/" + path, submission(kind), methods=["POST"], name=kind)

    @app.get("/jobs")
    def jobs() -> Response:
        return json_response(manager.store.list())

    @app.get("/jobs/{identity}")
    def job(identity: str) -> Response:
        return json_response(manager.store.get(identity))

    @app.get("/jobs/{identity}/events")
    def events(identity: str, after: int = 0, limit: int = 100) -> Response:
        return json_response(manager.store.events(identity, after=after, limit=limit))

    @app.post("/jobs/{identity}/cancel")
    def cancel(identity: str) -> Response:
        return json_response(manager.store.cancel(identity))

    @app.get("/runs")
    def runs() -> Response:
        return json_response(service.runs())

    @app.get("/runs/{identity}")
    def run(identity: str) -> Response:
        return json_response(service.run_section(identity))

    @app.get("/runs/{identity}/artifacts/{name}")
    def artifact(identity: str, name: str) -> Response:
        return Response(service.artifact(identity, name), media_type="application/octet-stream")

    @app.get("/runs/{identity}/report.html")
    def report(identity: str) -> Response:
        return Response(
            service.html_report(identity),
            media_type="text/html",
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
                )
            },
        )

    @app.get("/runs/{identity}/{section}")
    def section(identity: str, section: str) -> Response:
        return json_response(service.run_section(identity, section))

    @app.get("/strategies")
    def strategies() -> Response:
        return json_response(service.runtime.strategies())

    @app.get("/paper/sessions")
    def paper_sessions() -> Response:
        return json_response(paper.list())

    @app.post("/paper/sessions")
    def paper_create(payload: dict[str, object]) -> Response:
        return json_response(paper.create(payload), 201)

    @app.get("/paper/sessions/{identity}")
    def paper_session(identity: str) -> Response:
        return json_response(paper.get(identity))

    @app.post("/paper/sessions/{identity}/{action}")
    def paper_command(identity: str, action: str, payload: dict[str, object]) -> Response:
        return json_response(paper.command(identity, action, payload))

    @app.get("/sandbox/capabilities")
    def sandbox_capabilities() -> Response:
        return json_response(
            capabilities() if isolated_gateway is None else isolated_gateway.capability()
        )

    @app.post("/sandbox/strategies")
    async def submit_isolated_strategy(request: Request) -> Response:
        if isolated_gateway is None:
            reject_isolated_python()
        await run_in_threadpool(isolated_gateway.require_ready)
        raw = await request.body()
        if len(raw) > 2_000_000:
            return json_response({"error": "Isolated request exceeds 2 MB"}, 413)
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise DomainError("Invalid isolated JSON") from exc
        return json_response(await run_in_threadpool(isolated_gateway.submit, value), 202)

    def isolated_queue() -> IsolationGateway:
        if isolated_gateway is None or isolated_gateway.scheduler is None:
            reject_isolated_python()
        return isolated_gateway

    @app.get("/sandbox/jobs/{identity}")
    def isolated_job(identity: str) -> Response:
        gateway = isolated_queue()
        assert gateway.scheduler is not None
        return json_response(gateway.job_view(gateway.scheduler.store.get(identity)))

    @app.get("/sandbox/jobs/{identity}/events")
    def isolated_events(identity: str) -> Response:
        gateway = isolated_queue()
        assert gateway.scheduler is not None
        return json_response(gateway.scheduler.store.events(identity))

    @app.post("/sandbox/jobs/{identity}/cancel")
    def cancel_isolated(identity: str) -> Response:
        gateway = isolated_queue()
        assert gateway.scheduler is not None
        return json_response(gateway.job_view(gateway.scheduler.cancel(identity)))

    @app.get("/datasets")
    def datasets() -> Response:
        return json_response(service.runtime.snapshots())

    @app.post("/datasets")
    def import_data(payload: dict[str, object]) -> Response:
        return json_response(service.import_data(payload), 201)

    @app.post("/compare")
    def compare(payload: dict[str, object]) -> Response:
        ids = payload.get("run_ids")
        if (
            set(payload) != {"run_ids"}
            or not isinstance(ids, list)
            or any(not isinstance(identity, str) for identity in ids)
        ):
            raise DomainError("Comparison requires a run_ids string array")
        return json_response(service.compare(ids))

    frontend = project_root / "apps" / "web" / "dist"
    if (frontend / "index.html").is_file():
        app.mount("/ui", StaticFiles(directory=frontend, html=True), name="ui")
    else:

        @app.get("/ui/{path:path}")
        def ui_unavailable(path: str) -> Response:
            return json_response(
                {"error": "Terminal UI build unavailable", "message": "Build apps/web first"},
                503,
            )

    app.add_middleware(ObservabilityMiddleware, monitor=monitor)
    return app
