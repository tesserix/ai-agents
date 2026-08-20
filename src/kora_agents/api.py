"""Bounded HTTP edge for Kora agent execution and discovery."""

import hmac
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from kora_agents.config import Settings
from kora_agents.execution import AgentService, ExecutionResult
from kora_agents.runtime import AgentNotFoundError, ExecutionFailedError


class RunRequest(BaseModel):
    """One bounded user prompt; tenant and identity are server-owned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1, max_length=12_000)


class UsageResponse(BaseModel):
    """Payload-free usage returned to callers."""

    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated: bool


class RunResponse(BaseModel):
    """Stable response for a completed agent run."""

    run_id: str
    agent_name: str
    state: str
    output: str | dict[str, object]
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
    """Minimal A2A JSON-RPC request supported by Kora agents."""

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


def _response(result: ExecutionResult) -> RunResponse:
    return RunResponse(
        run_id=result.run_id,
        agent_name=result.agent_name,
        state=result.state,
        output=result.output,
        usage=UsageResponse(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            estimated=result.estimated,
        ),
    )


def create_app(
    *,
    settings: Settings,
    service: AgentService,
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Build the process edge around an injected ADK-backed service."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if shutdown is not None:
            await shutdown()

    app = FastAPI(
        title="Kora AI Agents",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    authenticate = _authenticate(settings)
    logger = structlog.get_logger("kora_agents.http")

    @app.middleware("http")
    async def request_telemetry(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = secrets.token_hex(16)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
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
        code = "unauthorized" if error.status_code == 401 else "request_failed"
        return JSONResponse(
            status_code=error.status_code, content={"code": code, "message": str(error.detail)}
        )

    @app.exception_handler(AgentNotFoundError)
    async def missing_agent(_request: Request, _error: AgentNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"code": "agent_not_found", "message": "agent not found"},
        )

    @app.exception_handler(ExecutionFailedError)
    async def failed_run(_request: Request, _error: ExecutionFailedError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"code": "agent_execution_failed", "message": "agent execution failed"},
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/v1/agents", dependencies=[Depends(authenticate)])
    async def agents() -> dict[str, object]:
        return {"agents": service.cards()}

    @app.post(
        "/v1/agents/{agent_name}/runs",
        response_model=RunResponse,
        dependencies=[Depends(authenticate)],
    )
    async def run_agent(agent_name: str, body: RunRequest) -> RunResponse:
        if len(body.prompt) > settings.max_prompt_chars:
            raise HTTPException(status_code=422, detail="prompt exceeds configured limit")
        result = await service.run(agent_name, body.prompt, tenant=settings.tenant_id)
        return _response(result)

    @app.post("/a2a/v1/{agent_name}", dependencies=[Depends(authenticate)])
    async def a2a(agent_name: str, body: A2ARequest) -> dict[str, object]:
        prompt = "\n".join(part.text for part in body.params.message.parts)
        if len(prompt) > settings.max_prompt_chars:
            raise HTTPException(status_code=422, detail="prompt exceeds configured limit")
        result = await service.run(agent_name, prompt, tenant=settings.tenant_id)
        output = result.output if isinstance(result.output, str) else json.dumps(result.output)
        return {
            "jsonrpc": "2.0",
            "id": body.id,
            "result": {
                "id": result.run_id,
                "status": {"state": result.state},
                "artifacts": [{"parts": [{"kind": "text", "text": output}]}],
                "metadata": {
                    "usage": {
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "cached_tokens": result.cached_tokens,
                        "estimated": result.estimated,
                    }
                },
            },
        }

    return app
