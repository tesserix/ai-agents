"""The HTTP edge: orchestration and supervision endpoints, and the A2A methods.

The surface is deliberately narrow. A caller states a task or an answer to judge; which
workers exist, which tenant runs, and which model supervises are all owned by the
process, so no request can widen the blast radius the operator configured.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Protocol

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from orchestrator_agent.config import Settings
from orchestrator_agent.definitions import ORCHESTRATOR, SUPERVISOR, Verdict
from orchestrator_agent.orchestration import OrchestrationReport, UnsupportedTaskError
from orchestrator_agent.supervision import SupervisionFailedError, SupervisionRun

ORCHESTRATOR_NAME = ORCHESTRATOR.agent.name
SUPERVISOR_NAME = SUPERVISOR.agent.name


class Orchestrator(Protocol):
    """The edge needs the two runtimes, and a test can stand in for either."""

    async def run(self, raw_task: str, *, tenant: str) -> OrchestrationReport: ...

    def cards(self) -> tuple[dict[str, object], ...]: ...


class Supervisor(Protocol):
    async def supervise(
        self, *, task: str, answer: str, context: str = "", tenant: str = "tesserix"
    ) -> SupervisionRun: ...


class OrchestrationRequest(BaseModel):
    """One task object, as a string the orchestration contract parses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str = Field(min_length=1, max_length=50_000)


class SupervisionRequest(BaseModel):
    """One answer to judge against the task that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str = Field(min_length=1, max_length=12_000)
    answer: str = Field(min_length=1, max_length=12_000)
    context: str = Field(default="", max_length=12_000)


class SupervisionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    agent_name: str
    verdict: Verdict


class A2ATextPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=50_000)


class A2AMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user"]
    parts: Annotated[list[A2ATextPart], Field(min_length=1, max_length=8)]


class A2AParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: A2AMessage


class A2ARequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    jsonrpc: Literal["2.0"]
    id: str | int
    method: Literal["message/send"]
    params: A2AParams


def _authenticate(settings: Settings) -> Callable[..., None]:
    expected = settings.api_key.get_secret_value()

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    return authenticate


def create_app(
    *,
    settings: Settings,
    orchestrator: Orchestrator,
    supervisor: Supervisor,
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Build the process edge around the injected runtimes."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if shutdown is not None:
            await shutdown()

    app = FastAPI(
        title="Tesserix Orchestrator Agent",
        version=ORCHESTRATOR.agent.version,
        lifespan=lifespan,
    )
    authenticate = _authenticate(settings)
    logger = structlog.get_logger("orchestrator_agent.http")

    @app.middleware("http")
    async def observe(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = secrets.token_hex(16)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException:
            logger.error(
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 404: "not_found", 422: "unprocessable"}
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": codes.get(error.status_code, "error"),
                "message": str(error.detail),
            },
        )

    @app.exception_handler(UnsupportedTaskError)
    async def unsupported_task(request: Request, error: UnsupportedTaskError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"code": "unsupported_task", "message": str(error)},
        )

    @app.exception_handler(SupervisionFailedError)
    async def supervision_failed(request: Request, error: SupervisionFailedError) -> JSONResponse:
        logger.warning("supervision_failed", path=request.url.path, state=error.state)
        return JSONResponse(
            status_code=502,
            content={
                "code": "supervision_failed",
                "message": "the supervision did not produce a verdict",
            },
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/v1/agents", dependencies=[Depends(authenticate)])
    async def agents() -> dict[str, object]:
        return {"agents": list(orchestrator.cards())}

    @app.get("/a2a/v1/{agent_name}/card", dependencies=[Depends(authenticate)])
    async def agent_card(agent_name: str) -> dict[str, object]:
        for card in orchestrator.cards():
            if card["name"] == agent_name:
                return card
        raise HTTPException(status_code=404, detail="agent not found")

    @app.post("/v1/orchestrations", dependencies=[Depends(authenticate)])
    async def orchestrate(body: OrchestrationRequest) -> OrchestrationReport:
        return await orchestrator.run(body.task, tenant=settings.tenant_id)

    @app.post(
        "/v1/supervisions",
        response_model=SupervisionResponse,
        dependencies=[Depends(authenticate)],
    )
    async def supervise(body: SupervisionRequest) -> SupervisionResponse:
        run = await supervisor.supervise(
            task=body.task,
            answer=body.answer,
            context=body.context,
            tenant=settings.tenant_id,
        )
        return SupervisionResponse(
            run_id=run.run_id, agent_name=SUPERVISOR_NAME, verdict=run.verdict
        )

    @app.post("/a2a/v1/{agent_name}", dependencies=[Depends(authenticate)])
    async def a2a(agent_name: str, body: A2ARequest) -> dict[str, object]:
        prompt = "\n".join(part.text for part in body.params.message.parts)
        if len(prompt) > settings.max_prompt_chars:
            raise HTTPException(status_code=422, detail="prompt exceeds configured limit")
        if agent_name == ORCHESTRATOR_NAME:
            report = await orchestrator.run(prompt, tenant=settings.tenant_id)
            run_id, output = report.run_id, report.model_dump_json()
        elif agent_name == SUPERVISOR_NAME:
            # A structured request names the task; bare text is judged as the answer alone.
            try:
                ask = SupervisionRequest.model_validate_json(prompt)
            except ValidationError:
                ask = SupervisionRequest(
                    task="Judge whether this answer is safe and coherent to pass on.",
                    answer=prompt,
                )
            run = await supervisor.supervise(
                task=ask.task,
                answer=ask.answer,
                context=ask.context,
                tenant=settings.tenant_id,
            )
            run_id, output = run.run_id, run.verdict.model_dump_json()
        else:
            raise HTTPException(status_code=404, detail="agent not found")
        return {
            "jsonrpc": "2.0",
            "id": body.id,
            "result": {
                "id": run_id,
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": output}]}],
            },
        }

    return app
