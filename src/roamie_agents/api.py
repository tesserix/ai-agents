import asyncio
import hmac
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Protocol

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from tesserix_adk.core import principal_scope

from roamie_agents.base import Contract
from roamie_agents.config import Settings
from roamie_agents.contracts import (
    RecommendationRequest,
    Specialist,
)
from roamie_agents.definitions import DEFINITIONS
from roamie_agents.delegation import DelegationError, Delegations
from roamie_agents.evidence import EvidenceBatch
from roamie_agents.runtime import TravelFailure, TravelService


class TextPart(Contract):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=60000)


class Message(Contract):
    role: Literal["user"]
    parts: Annotated[list[TextPart], Field(min_length=1, max_length=1)]


class Params(Contract):
    message: Message


class A2ARequest(Contract):
    jsonrpc: Literal["2.0"]
    id: str = Field(min_length=1, max_length=120)
    method: Literal["message/send"]
    params: Params


class Source(Protocol):
    async def search(
        self, specialist: Specialist, request: RecommendationRequest
    ) -> EvidenceBatch: ...


type SourceFactory = Callable[[str], AsyncIterator[Source]]


def create_app(
    *,
    settings: Settings,
    service: TravelService,
    on_close: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if on_close is not None:
                await on_close()

    app = FastAPI(
        title="Internal Roamie specialist workers", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    slots = asyncio.Semaphore(settings.max_in_flight)
    delegations = Delegations(key=settings.delegation_key)

    @app.get("/healthz")
    @app.get("/readyz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(TravelFailure)
    async def failure(request: Request, error: TravelFailure) -> JSONResponse:
        del request, error
        return JSONResponse(
            status_code=502,
            content={
                "code": "travel_unavailable",
                "message": "Travel suggestions are unavailable.",
                "request_id": secrets.token_hex(16),
            },
        )

    def authorize(value: str | None) -> None:
        scheme, _, token = (value or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token, settings.api_key.get_secret_value()
        ):
            raise HTTPException(401, "unauthorized")

    @app.get("/v1/agents")
    async def agents(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
        authorize(authorization)
        return {
            "agents": [
                {
                    "name": definition.agent.name,
                    "revision": definition.revision,
                    "version": definition.agent.version,
                }
                for definition in DEFINITIONS.values()
            ]
        }

    @app.post("/a2a/v1/roamie-{specialist}")
    async def a2a_worker(
        specialist: Specialist,
        body: A2ARequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        authorize(authorization)
        try:
            delegated, identity = delegations.verify_request(
                body.params.message.parts[0].text, specialist
            )
            payload = delegated.payload
        except DelegationError as error:
            raise HTTPException(401, "invalid manager delegation") from error
        if slots.locked():
            raise HTTPException(429, "capacity exceeded")
        async with slots:
            async with asyncio.timeout(60):
                with principal_scope(identity.principal):
                    result = await service.recommend(
                        specialist,
                        payload.request,
                        facts=payload.facts,
                        reference_rate=payload.reference_rate,
                        exchange_quotes=payload.exchange_quotes,
                    )
        try:
            signed = delegations.response(delegated, result)
        except DelegationError as error:
            raise HTTPException(401, "expired manager delegation") from error
        return {
            "jsonrpc": "2.0",
            "id": body.id,
            "result": {
                "id": result.run_id or secrets.token_hex(16),
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": signed.model_dump_json()}]}],
            },
        }

    return app
