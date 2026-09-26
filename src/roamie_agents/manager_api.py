import asyncio
import hashlib
import hmac
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import Field, SecretStr
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from roamie_agents.api import SourceFactory
from roamie_agents.base import Contract
from roamie_agents.contracts import RecommendationRequest, Specialist
from roamie_agents.manager import ManagedResponse, PersonalTripManager, Profile
from roamie_agents.runtime import TravelFailure


class ProfileSnapshot(Contract):
    delegated_identity_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: Profile
    request: RecommendationRequest
    specialist: Specialist
    issued_at: int = Field(ge=0, strict=True)
    expires_at: int = Field(ge=0, strict=True)


def create_manager_app(
    *,
    manager: PersonalTripManager,
    sources: SourceFactory,
    api_key: SecretStr,
    profile_signing_key: SecretStr,
    clock: Callable[[], float] = time.time,
    max_in_flight: int = 16,
) -> FastAPI:
    if min(len(api_key.get_secret_value()), len(profile_signing_key.get_secret_value())) < 32:
        raise ValueError("manager credentials require at least 32 characters")
    app = FastAPI(title="Roamie personal trip manager", docs_url=None, redoc_url=None)

    slots = asyncio.Semaphore(max_in_flight)

    @app.middleware("http")
    async def bounded_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
        from fastapi.responses import JSONResponse

        if request.url.path != "/v1/trip-manager":
            return await call_next(request)
        if slots.locked():
            return JSONResponse({"detail": "capacity exceeded"}, status_code=429)
        try:
            async with slots, asyncio.timeout(110):
                return await call_next(request)
        except TimeoutError:
            return JSONResponse({"detail": "trip manager deadline exceeded"}, status_code=503)

    @app.get("/healthz")
    @app.get("/readyz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/trip-manager", response_model=ManagedResponse)
    async def manage(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        signature: Annotated[
            str | None, Header(alias="X-Roamie-Profile-Signature", max_length=64)
        ] = None,
        gateway_token: Annotated[
            str | None, Header(alias="X-Roamie-Gateway-Token", max_length=8192)
        ] = None,
    ) -> ManagedResponse:
        if not hmac.compare_digest(authorization or "", "Bearer " + api_key.get_secret_value()):
            raise HTTPException(401, "unauthorized")
        if not gateway_token:
            raise HTTPException(401, "delegated identity required")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32768:
                raise HTTPException(413, "profile snapshot too large")
        expected = hmac.new(
            profile_signing_key.get_secret_value().encode(), raw, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature or "", expected):
            raise HTTPException(401, "unverified profile")
        try:
            snapshot = ProfileSnapshot.model_validate_json(raw)
        except ValueError as error:
            raise HTTPException(422, "invalid profile snapshot") from error
        if not hmac.compare_digest(
            snapshot.delegated_identity_digest, hashlib.sha256(gateway_token.encode()).hexdigest()
        ):
            raise HTTPException(401, "profile identity mismatch")
        now = clock()
        if not snapshot.issued_at <= now < snapshot.expires_at <= snapshot.issued_at + 120:
            raise HTTPException(401, "expired profile snapshot")
        try:
            async with asynccontextmanager(sources)(gateway_token) as source:
                batch = await source.search(snapshot.specialist, snapshot.request)
            if batch.status != "ok":
                raise HTTPException(503, "provider evidence unavailable")
            result = await manager.manage(
                profile=snapshot.profile,
                current_revision=lambda: snapshot.profile.revision,
                specialist=snapshot.specialist,
                request=snapshot.request,
                facts=batch.facts,
                reference_rate=batch.reference_rate,
                exchange_quotes=batch.exchange_quotes,
            )
            if clock() >= snapshot.expires_at:
                raise HTTPException(401, "profile snapshot expired during review")
            return result
        except (TravelFailure, TimeoutError) as error:
            raise HTTPException(503, "trip manager could not validate this response") from error

    return app
