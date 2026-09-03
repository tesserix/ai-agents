"""The HTTP edge: one investigation endpoint, and the A2A method the registry publishes.

The surface is deliberately narrow. A caller may ask for an investigation and read what
came back; nothing here lets a caller choose the tenant, the cluster or the model, because
those decide the blast radius and the process owns them.
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
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from agent_telemetry import bound_session
from sre_agent.config import Settings
from sre_agent.definitions import INVESTIGATOR, Investigation
from sre_agent.runtime import InvestigationFailedError, InvestigationRun

AGENT_NAME = INVESTIGATOR.agent.name


class Investigator(Protocol):
    """What the edge needs from the runtime, so a test can stand in for it."""

    async def investigate(self, prompt: str, *, tenant: str) -> InvestigationRun: ...


class InvestigationRequest(BaseModel):
    """One thing to investigate. Tenant and cluster are server-owned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1, max_length=12_000)


class UsageResponse(BaseModel):
    """What the run cost, without any of what it read."""

    input_tokens: int
    output_tokens: int


class InvestigationResponse(BaseModel):
    """A completed investigation, in the shape SRE Studio and Slack both read."""

    run_id: str
    agent_name: str
    state: str
    findings: Investigation
    tools_called: tuple[str, ...]
    usage: UsageResponse


class A2ATextPart(BaseModel):
    """One bounded A2A text part."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=12_000)


class A2AMessage(BaseModel):
    """A caller-authored A2A message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user"]
    parts: Annotated[list[A2ATextPart], Field(min_length=1, max_length=8)]


class A2AParams(BaseModel):
    """Parameters accepted by message/send."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: A2AMessage


class A2ARequest(BaseModel):
    """The one A2A method this agent serves."""

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


def _card() -> dict[str, object]:
    """What this service publishes about the agent it serves."""
    agent = INVESTIGATOR.agent
    return {
        "name": agent.name,
        "version": agent.version,
        "revision": INVESTIGATOR.revision,
        "description": agent.instructions.split(".", maxsplit=1)[0] + ".",
        "tools": list(agent.tools),
        "read_only": True,
    }


def _response(run: InvestigationRun) -> InvestigationResponse:
    return InvestigationResponse(
        run_id=run.run_id,
        agent_name=AGENT_NAME,
        state="completed",
        findings=run.findings,
        tools_called=run.tools_called,
        usage=UsageResponse(input_tokens=run.input_tokens, output_tokens=run.output_tokens),
    )


def create_app(
    *,
    settings: Settings,
    service: Investigator,
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Build the process edge around an injected investigator."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if shutdown is not None:
            await shutdown()

    app = FastAPI(
        title="Tesserix SRE Investigator",
        version=INVESTIGATOR.agent.version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    authenticate = _authenticate(settings)
    logger = structlog.get_logger("sre_agent.http")

    @app.middleware("http")
    async def request_telemetry(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = secrets.token_hex(16)
        request.state.request_id = request_id
        started = time.perf_counter()
        session_id = request.headers.get("X-Session-ID", "")[:128] or request_id
        try:
            with bound_session(session_id):
                response = await call_next(request)
        except BaseException as error:
            logger.error(
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                error_type=type(error).__name__,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 404: "agent_not_found"}
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": codes.get(error.status_code, "request_failed"),
                "message": str(error.detail),
            },
        )

    @app.exception_handler(InvestigationFailedError)
    async def failed_run(request: Request, error: InvestigationFailedError) -> JSONResponse:
        # The caller gets a stable code and no internals; the operator gets the state and
        # the detail, without which an intermittent 502 cannot be diagnosed.
        logger.warning(
            "investigation_failed",
            path=request.url.path,
            state=error.state,
            detail=error.detail or "unknown",
        )
        return JSONResponse(
            status_code=502,
            content={
                "code": "investigation_failed",
                "message": "the investigation did not produce findings",
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
        return {"agents": [_card()]}

    @app.get("/a2a/v1/{agent_name}/card", dependencies=[Depends(authenticate)])
    async def agent_card(agent_name: str) -> dict[str, object]:
        if agent_name != AGENT_NAME:
            raise HTTPException(status_code=404, detail="agent not found")
        return _card()

    def prompt_within_limit(prompt: str) -> str:
        if len(prompt) > settings.max_prompt_chars:
            raise HTTPException(status_code=422, detail="prompt exceeds configured limit")
        return prompt

    @app.post(
        "/v1/investigations",
        response_model=InvestigationResponse,
        dependencies=[Depends(authenticate)],
    )
    async def investigate(body: InvestigationRequest) -> InvestigationResponse:
        run = await service.investigate(prompt_within_limit(body.prompt), tenant=settings.tenant_id)
        return _response(run)

    @app.post("/a2a/v1/{agent_name}", dependencies=[Depends(authenticate)])
    async def a2a(agent_name: str, body: A2ARequest) -> dict[str, object]:
        if agent_name != AGENT_NAME:
            raise HTTPException(status_code=404, detail="agent not found")
        prompt = "\n".join(part.text for part in body.params.message.parts)
        run = await service.investigate(prompt_within_limit(prompt), tenant=settings.tenant_id)
        return {
            "jsonrpc": "2.0",
            "id": body.id,
            "result": {
                "id": run.run_id,
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "parts": [
                            {
                                "kind": "text",
                                "text": run.findings.model_dump_json(),
                            }
                        ]
                    }
                ],
                "metadata": {
                    "tools_called": list(run.tools_called),
                    "usage": {
                        "input_tokens": run.input_tokens,
                        "output_tokens": run.output_tokens,
                    },
                },
            },
        }

    return app
