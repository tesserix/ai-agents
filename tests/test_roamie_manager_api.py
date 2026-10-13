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


async def authority(profile):
    return profile.revision


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
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_authority=authority,
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


@pytest.mark.parametrize(
    "reason,expected,status",
    [
        ("model_failed", "model_failed", 503),
        ("sensitive upstream detail", "unclassified", 503),
        ("response_not_approved", "response_not_approved", 422),
        ("request_requires_clarification", "request_requires_clarification", 422),
        ("profile_changed", "profile_changed", 409),
    ],
)
async def test_failure_distinguishes_terminal_review_from_unavailability(reason, expected, status):
    from structlog.testing import capture_logs

    from roamie_agents.runtime import TravelFailure

    async def sources(token):
        raise TravelFailure(reason)
        yield

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_authority=authority,
            profile_signing_key=SecretStr("b" * 32),
            clock=lambda: 1000,
        )
        raw = snapshot()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            with capture_logs() as logs:
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
        assert result.status_code == status
        assert "sensitive" not in result.text

        failure = next(event for event in logs if event["event"] == "roamie_request_failed")
        assert failure["reason"] == expected
        assert failure["component"] == "manager"
        assert len(failure["request_id"]) == 32
        assert "sensitive" not in json.dumps(logs)
        assert "fixture" not in json.dumps(logs)


async def test_incomplete_trip_plan_returns_questions_without_searching_providers():
    searched = []

    class RecordingSource:
        async def search(self, specialist, request):
            searched.append(specialist)
            return EvidenceBatch(status="unavailable")

    async def sources(token):
        yield RecordingSource()

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_authority=authority,
            profile_signing_key=SecretStr("b" * 32),
            clock=lambda: 1000,
        )
        raw = snapshot(
            specialist="trip-planner",
            request={
                "prompt": "Plan a week in Japan",
                "plan_options": True,
                "destination": "Japan",
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
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
    assert result.status_code == 422
    detail = result.json()["detail"]
    assert detail["reason"] == "planning_details_required"
    assert [q["topic"] for q in detail["questions"] if q["required"]] == ["dates", "budget"]
    assert searched == []


async def test_trip_evidence_searches_start_together_before_any_finishes():
    import asyncio

    from roamie_agents.contracts import Specialist

    started = set()
    preferences = []
    first = asyncio.Event()
    finish = asyncio.Event()

    class Source:
        async def search(self, specialist, request):
            started.add(specialist)
            preferences.append(request.preferences)
            first.set()
            await finish.wait()
            return EvidenceBatch(status="unavailable")

    async def sources(token):
        yield Source()

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_signing_key=SecretStr("b" * 32),
            profile_authority=authority,
            clock=lambda: 1000,
        )
        raw = snapshot(
            specialist="trip-planner",
            profile={
                "subject": "user",
                "trip_id": "trip",
                "revision": "1",
                "preferences": ["gardens"],
            },
        )
        headers = {
            "Authorization": "Bearer " + "a" * 32,
            "X-Roamie-Gateway-Token": "fixture",
            "X-Roamie-Profile-Signature": hmac.new(b"b" * 32, raw, hashlib.sha256).hexdigest(),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(
                client.post("/v1/trip-manager", content=raw, headers=headers)
            )
            try:
                await asyncio.wait_for(first.wait(), timeout=1)
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                assert started == {Specialist.TRIP, Specialist.WEATHER, Specialist.ENTRY}
                assert preferences == [("gardens",)] * 3
            finally:
                finish.set()
                response = await task
            assert response.status_code == 503


async def test_failed_trip_evidence_cancels_other_searches_and_returns_503():
    import asyncio

    from roamie_agents.contracts import Specialist
    from roamie_agents.runtime import TravelFailure

    started = set()
    cancelled = set()
    ready = asyncio.Event()

    class Source:
        async def search(self, specialist, request):
            started.add(specialist)
            if len(started) == 3:
                ready.set()
            await ready.wait()
            if specialist == Specialist.TRIP:
                raise TravelFailure("provider_unavailable")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.add(specialist)
                raise

    async def sources(token):
        yield Source()

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_signing_key=SecretStr("b" * 32),
            profile_authority=authority,
            clock=lambda: 1000,
        )
        raw = snapshot(specialist="trip-planner")
        headers = {
            "Authorization": "Bearer " + "a" * 32,
            "X-Roamie-Gateway-Token": "fixture",
            "X-Roamie-Profile-Signature": hmac.new(b"b" * 32, raw, hashlib.sha256).hexdigest(),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            async with asyncio.timeout(1):
                response = await client.post("/v1/trip-manager", content=raw, headers=headers)
    assert response.status_code == 503
    assert cancelled == {Specialist.WEATHER, Specialist.ENTRY}
