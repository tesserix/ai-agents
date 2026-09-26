import hashlib
import hmac
import json

import httpx
import pytest
from pydantic import SecretStr
from tesserix_adk.testing import ScriptedProvider

from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient
from roamie_agents.evidence import EvidenceBatch
from roamie_agents.manager import PersonalTripManager
from roamie_agents.manager_api import create_manager_app


def snapshot(**changes):
    return json.dumps(
        {
            "profile": {"subject": "user", "trip_id": "trip", "revision": "1"},
            "request": {"prompt": "Dinner"},
            "specialist": "food",
            "delegated_identity_digest": hashlib.sha256(b"fixture").hexdigest(),
            "issued_at": 990,
            "expires_at": 1100,
            **changes,
        }
    ).encode()


@pytest.mark.parametrize(
    "raw,authorization,delegated,signed,status",
    [
        (snapshot(), None, "fixture", False, 401),
        (snapshot(), "Bearer " + "a" * 32, None, False, 401),
        (snapshot(), "Bearer " + "a" * 32, "fixture", False, 401),
        (snapshot(issued_at=1, expires_at=100), "Bearer " + "a" * 32, "fixture", True, 401),
        (snapshot(), "Bearer " + "a" * 32, "other", True, 401),
        (b"invalid-json", "Bearer " + "a" * 32, "fixture", True, 422),
        (b"x" * 32769, "Bearer " + "a" * 32, "fixture", True, 413),
        (snapshot(), "Bearer " + "a" * 32, "fixture", True, 503),
    ],
)
async def test_signed_profile_boundary_fails_closed(raw, authorization, delegated, signed, status):
    class UnavailableSource:
        async def search(self, specialist, request):
            return EvidenceBatch(status="unavailable")

    async def sources(token):
        yield UnavailableSource()

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_signing_key=SecretStr("b" * 32),
            clock=lambda: 1000,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {}
            if authorization:
                headers["Authorization"] = authorization
            if delegated:
                headers["X-Roamie-Gateway-Token"] = delegated
            if signed:
                headers["X-Roamie-Profile-Signature"] = hmac.new(
                    b"b" * 32, raw, hashlib.sha256
                ).hexdigest()
            result = await client.post("/v1/trip-manager", content=raw, headers=headers)
            assert result.status_code == status


async def test_provider_failure_returns_unavailable_without_exposing_error():
    from roamie_agents.runtime import TravelFailure

    async def sources(token):
        raise TravelFailure("sensitive upstream detail")
        yield

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_signing_key=SecretStr("b" * 32),
            clock=lambda: 1000,
        )
        raw = snapshot()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            result = await client.post(
                "/v1/trip-manager",
                content=raw,
                headers={
                    "Authorization": "Bearer " + "a" * 32,
                    "X-Roamie-Gateway-Token": "fixture",
                    "X-Roamie-Profile-Signature": hmac.new(
                        b"b" * 32, raw, hashlib.sha256
                    ).hexdigest(),
                },
            )
        assert result.status_code == 503
        assert "sensitive" not in result.text
