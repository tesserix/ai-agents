import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from tesserix_adk.core import ModelCapabilities
from tesserix_adk.runtime import ModelResponse
from tesserix_adk.testing import ScriptedProvider

from orchestrator_agent.config import WorkerEndpoint
from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient
from roamie_agents.contracts import RecommendationRequest, Specialist, TravelResponse
from roamie_agents.definitions import manager_definition
from roamie_agents.delegation import DelegatedRequest, DelegatedResponse, canonical
from roamie_agents.manager import PersonalTripManager, Profile
from roamie_agents.runtime import TravelFailure


def signed_reply(request, output):
    incoming = json.loads(request.content)
    delegation = DelegatedRequest.model_validate_json(
        incoming["params"]["message"]["parts"][0]["text"]
    )
    response = DelegatedResponse(
        context=delegation.context,
        request_digest=hashlib.sha256(canonical(delegation)).hexdigest(),
        response=TravelResponse.model_validate(output),
        signature="0" * 64,
    )
    signature = hmac.new(
        b"d" * 32, b"response\x00" + canonical(response), hashlib.sha256
    ).hexdigest()
    return response.model_copy(update={"signature": signature}).model_dump_json()


def reviewer(decision="approve"):
    response = json.dumps(
        {
            "decision": decision,
            "confidence": 1.0,
            "summary": "Checked profile and evidence",
            "issues": [],
        }
    )
    return SupervisorService(
        provider=ScriptedProvider(
            ModelResponse(content=response),
            ModelResponse(content=response),
            capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32768),
        )
    )


async def test_manager_checks_request_and_response_and_scopes_identity():
    calls = []
    approved = ModelResponse(
        content=json.dumps(
            {"decision": "approve", "confidence": 1.0, "summary": "Verified", "issues": []}
        )
    )
    model = ScriptedProvider(
        approved,
        approved,
        capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32768),
    )
    supervisor = SupervisorService(provider=model, definition=manager_definition())

    def reply(request):
        calls.append(json.loads(request.content))
        output = TravelResponse(status="no_matches", specialist=Specialist.FOOD)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": calls[-1]["id"],
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"parts": [{"kind": "text", "text": signed_reply(request, output)}]}
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        worker = A2AWorkerClient(api_key=SecretStr("fixture-worker-key"), timeout=5, client=client)
        manager = PersonalTripManager(
            workers={
                Specialist.FOOD: WorkerEndpoint(
                    name="roamie-food", url="https://gateway.example.org/a2a/v1/roamie-food"
                )
            },
            client=worker,
            supervisor=supervisor,
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("a" * 32),
        )
        profile = Profile(subject="user-1", trip_id="trip-1", revision="1", allergies=("peanut",))
        result = await manager.manage(
            profile=profile,
            current_revision=lambda: "1",
            specialist=Specialist.FOOD,
            request=RecommendationRequest(prompt="Dinner"),
            facts=[],
        )
        assert result.response.status == "no_matches"
        assert len(result.review_run_ids) == 2
        assert result.manager_id.startswith("trip-manager-")
        prompt = calls[0]["params"]["message"]["parts"][0]["text"]
        assert "peanut" in prompt
        assert "user-1" not in prompt
        preflight = "".join(part.text for part in model.requests[0].messages[-1].content)
        postflight = "".join(part.text for part in model.requests[1].messages[-1].content)
        assert "PROPOSED REQUEST (untrusted" in preflight
        assert "ANSWER (untrusted worker output" not in preflight
        assert "ANSWER (untrusted worker output" in postflight
        assert "peanut" in preflight and "peanut" in postflight


async def test_rejected_preflight_never_calls_worker():
    def refuse_call(request):
        raise AssertionError("worker must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse_call)) as client:
        manager = PersonalTripManager(
            workers={
                Specialist.FOOD: WorkerEndpoint(
                    name="roamie-food", url="https://gateway.example.org/a2a/v1/roamie-food"
                )
            },
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=client),
            supervisor=reviewer("reject"),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("a" * 32),
        )
        with pytest.raises(TravelFailure, match="request_requires_clarification"):
            await manager.manage(
                profile=Profile(subject="user", trip_id="trip", revision="1"),
                current_revision=lambda: "1",
                specialist=Specialist.FOOD,
                request=RecommendationRequest(prompt="Dinner"),
                facts=[],
            )


async def test_changed_profile_never_calls_worker():
    async with httpx.AsyncClient() as client:
        manager = PersonalTripManager(
            workers={
                Specialist.FOOD: WorkerEndpoint(
                    name="roamie-food", url="https://gateway.example.org/a2a/v1/roamie-food"
                )
            },
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=client),
            supervisor=reviewer(),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("a" * 32),
        )
        with pytest.raises(TravelFailure, match="profile_changed"):
            await manager.manage(
                profile=Profile(subject="user", trip_id="trip", revision="1"),
                current_revision=lambda: "2",
                specialist=Specialist.FOOD,
                request=RecommendationRequest(prompt="Dinner"),
                facts=[],
            )


@pytest.mark.parametrize(
    "output,expected",
    [
        ({"status": "no_matches", "specialist": "shopping"}, "manager_review_unavailable"),
        (
            {
                "status": "ok",
                "specialist": "food",
                "recommendations": [
                    {
                        "id": "invented",
                        "name": "Invented",
                        "category": "food",
                        "source_url": "https://example.org/place",
                        "observed_at": "2026-09-26T00:00:00Z",
                    }
                ],
            },
            "unsupported_worker_claim",
        ),
    ],
)
async def test_manager_blocks_worker_claims_missing_from_independent_evidence(output, expected):
    def reply(request):
        return httpx.Response(
            200,
            json={
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"parts": [{"kind": "text", "text": signed_reply(request, output)}]}
                    ],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        manager = PersonalTripManager(
            workers={
                Specialist.FOOD: WorkerEndpoint(
                    name="roamie-food", url="https://gateway.example.org/a2a/v1/roamie-food"
                )
            },
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=client),
            supervisor=reviewer(),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("a" * 32),
        )
        with pytest.raises(TravelFailure, match=expected):
            await manager.manage(
                profile=Profile(subject="user", trip_id="trip", revision="1"),
                current_revision=lambda: "1",
                specialist=Specialist.FOOD,
                request=RecommendationRequest(prompt="Dinner"),
                facts=[],
            )


async def test_manager_calls_real_worker_asgi_over_a2a_and_reviews_result():
    from roamie_agents.api import create_app
    from roamie_agents.config import Settings
    from roamie_agents.contracts import Evidence
    from roamie_agents.runtime import TravelService

    now = datetime(2026, 9, 26, 1, tzinfo=UTC)
    model = ScriptedProvider(
        ModelResponse(content='{"selected_ids":["cafe"]}'),
        capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32768),
    )

    app = create_app(
        settings=Settings(
            delegation_key="d" * 32,
            api_key="a" * 32,
            mcp_schema_digest="1" * 64,
        ),
        service=TravelService(provider=model, clock=lambda: now),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        manager = PersonalTripManager(
            workers={
                Specialist.FOOD: WorkerEndpoint(
                    name="roamie-food", url="https://gateway.example.org/a2a/v1/roamie-food"
                )
            },
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=client),
            supervisor=reviewer(),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
            clock=lambda: now,
        )
        result = await manager.manage(
            profile=Profile(subject="user", trip_id="trip", revision="1", diets=("vegan",)),
            current_revision=lambda: "1",
            specialist=Specialist.FOOD,
            request=RecommendationRequest(prompt="Dinner"),
            facts=[
                Evidence(
                    id="cafe",
                    name="Cafe",
                    category=Specialist.FOOD,
                    source_url="https://example.org/cafe",
                    observed_at=now,
                    dietary_tags=("vegan",),
                    cost_minor=2000,
                    currency="AUD",
                )
            ],
        )
        assert result.response.recommendations[0].cost_minor == 2000
        assert result.response.recommendations[0].warnings == (
            "Confirm dietary and allergy requirements with staff.",
        )
        assert len(result.review_run_ids) == 2


async def test_evidence_expiring_during_review_is_rejected():
    from datetime import timedelta

    from roamie_agents.contracts import Evidence, project

    now = datetime.now(UTC)
    time_values = iter([now, now + timedelta(days=2)])
    facts = [
        Evidence(
            id="a", name="Place", category="food", source_url="https://x.test/a", observed_at=now
        )
    ]
    request = RecommendationRequest(prompt="dinner")
    answer = TravelResponse(
        specialist="food", status="ok", recommendations=[project(facts[0], None)]
    )

    def handler(req):
        incoming = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": incoming["id"],
                "result": {
                    "id": "task",
                    "contextId": "context",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "artifactId": "a",
                            "parts": [{"kind": "text", "text": signed_reply(req, answer)}],
                        }
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        manager = PersonalTripManager(
            workers={
                Specialist.FOOD: WorkerEndpoint(name="food", url="https://gateway.test/a2a/v1/food")
            },
            client=A2AWorkerClient(api_key=SecretStr("key"), timeout=3, client=http),
            supervisor=reviewer(),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("x" * 32),
            clock=lambda: next(time_values),
        )
        with pytest.raises(TravelFailure, match="evidence_expired"):
            await manager.manage(
                specialist=Specialist.FOOD,
                profile=Profile(subject="user", trip_id="trip", revision="1"),
                request=request,
                facts=facts,
                current_revision=lambda: "1",
            )


@pytest.mark.parametrize("tampered", [False, True])
async def test_manager_recomputes_currency_exchange_receipts(tampered):
    from datetime import timedelta

    from roamie_agents.exchange import ExchangeQuote, ReferenceRate, compare_quotes

    now = datetime(2026, 9, 26, 1, tzinfo=UTC)
    reference = ReferenceRate(
        source_currency="AUD",
        destination_currency="JPY",
        rate="101",
        observed_at=now,
        source_url="https://example.org/reference",
    )
    quotes = [
        ExchangeQuote(
            id="shop",
            shop="Exchange shop",
            source_currency="AUD",
            destination_currency="JPY",
            rate="100",
            observed_at=now,
            expires_at=now + timedelta(hours=1),
            source_url="https://example.org/shop",
            fees_complete=True,
        )
    ]
    comparisons = compare_quotes(
        amount_minor=10000,
        source_exponent=2,
        destination_exponent=0,
        reference=reference,
        quotes=quotes,
        now=now,
    )
    if tampered:
        comparisons = (comparisons[0].model_copy(update={"received_minor": 999999}),)
    response = TravelResponse(
        status="ok", specialist=Specialist.EXCHANGE, exchange_comparisons=comparisons
    )

    def reply(request):
        incoming = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": incoming["id"],
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"parts": [{"kind": "text", "text": signed_reply(request, response)}]}
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        manager = PersonalTripManager(
            workers={
                Specialist.EXCHANGE: WorkerEndpoint(
                    name="exchange", url="https://gateway.example.org/a2a/v1/exchange"
                )
            },
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=client),
            supervisor=reviewer(),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("x" * 32),
            clock=lambda: now,
        )
        arguments = dict(
            profile=Profile(subject="user", trip_id="trip", revision="1"),
            current_revision=lambda: "1",
            specialist=Specialist.EXCHANGE,
            request=RecommendationRequest(
                prompt="Exchange money",
                exchange_amount_minor=10000,
                exchange_destination_currency="JPY",
            ),
            facts=[],
            reference_rate=reference,
            exchange_quotes=quotes,
        )
        if tampered:
            with pytest.raises(TravelFailure, match="unsupported_worker_claim"):
                await manager.manage(**arguments)
        else:
            result = await manager.manage(**arguments)
            assert result.response.exchange_comparisons[0].received_minor == 10000
            assert len(result.review_run_ids) == 2


async def test_manager_identity_matches_api_contract_and_is_user_trip_scoped():
    async with httpx.AsyncClient() as client:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=client),
            supervisor=reviewer(),
            identity_key=SecretStr("d" * 32),
            delegation_key=SecretStr("e" * 32),
        )
        profile = Profile(subject="verified", trip_id="東京", revision="1")
        identity = manager.manager_id(profile)
        assert (
            identity
            == "trip-manager-eec1e0a022fe5a46c471ddeb38811901e1bda2af56938a7908b3fb6b0850d7e6"
        )
        assert identity != manager.manager_id(profile.model_copy(update={"subject": "other"}))
        assert identity != manager.manager_id(profile.model_copy(update={"trip_id": "other"}))


@pytest.mark.parametrize("corrupt", [False, True])
async def test_manager_independently_validates_three_plans_before_final_review(corrupt):
    from datetime import date

    from roamie_agents.contracts import Evidence
    from roamie_agents.planning import PlanSelection
    from roamie_agents.runtime import project
    from test_roamie_planning import proposal

    now = datetime(2026, 9, 26, tzinfo=UTC)
    fact = Evidence(
        id="museum",
        name="Museum",
        category="activities",
        source_url="https://maps.google.com/place",
        observed_at=now,
    )
    value = proposal()
    if corrupt:
        value["options"][2]["budget"]["food_minor"] = 10000
    output = TravelResponse(
        status="ok",
        specialist=Specialist.TRIP,
        recommendations=(project(fact, None),),
        trip_options=PlanSelection.model_validate(value).options,
    )

    def reply(request):
        incoming = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": incoming["id"],
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"parts": [{"kind": "text", "text": signed_reply(request, output)}]}
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        manager = PersonalTripManager(
            workers={
                Specialist.TRIP: WorkerEndpoint(
                    name="roamie-trip-planner",
                    url="https://gateway.example.org/a2a/v1/roamie-trip-planner",
                )
            },
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=client),
            supervisor=reviewer(),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("a" * 32),
            clock=lambda: now,
        )

        async def run():
            return await manager.manage(
                profile=Profile(
                    subject="user",
                    trip_id="trip",
                    revision="1",
                    start_date=date(2026, 10, 1),
                    end_date=date(2026, 10, 1),
                    currency="AUD",
                    budget_minor=8000,
                ),
                current_revision=lambda: "1",
                specialist=Specialist.TRIP,
                request=RecommendationRequest(
                    prompt="Three plans", plan_options=True, currency="AUD"
                ),
                facts=[fact],
            )

        if corrupt:
            with pytest.raises(TravelFailure, match="invalid_trip_options"):
                await run()
        else:
            result = await run()
            assert len(result.response.trip_options) == 3
            assert len(set(result.review_run_ids)) == 2


@pytest.mark.parametrize("planning", [False, True])
async def test_trip_consults_weather_and_entry_before_itinerary_review(planning):
    from roamie_agents.contracts import Evidence, project

    now = datetime.now(UTC)
    kinds = (Specialist.WEATHER, Specialist.ENTRY, Specialist.TRIP)
    facts = [
        Evidence(
            id=kind.value,
            name=kind.value,
            category=kind,
            source_url="https://example.org/source",
            observed_at=now,
        )
        for kind in kinds
    ]
    if planning:
        from test_roamie_planning import evidence

        facts[-1] = evidence().model_copy(update={"category": Specialist.TRIP, "observed_at": now})
    called = []

    def reply(request):
        body = json.loads(request.content)
        incoming = DelegatedRequest.model_validate_json(
            body["params"]["message"]["parts"][0]["text"]
        )
        kind = incoming.context.specialist
        called.append(kind)
        assert kind == Specialist.TRIP or not incoming.payload.request.plan_options
        if kind != Specialist.TRIP:
            assert incoming.payload.request.prompt != "Plan"
            assert (
                "weather" if kind == Specialist.WEATHER else "entry"
            ) in incoming.payload.request.prompt.lower()
        output = TravelResponse(
            status="ok",
            specialist=kind,
            recommendations=[project(fact, None) for fact in facts if fact.category == kind],
        )
        if planning and kind == Specialist.TRIP:
            from roamie_agents.planning import PlanSelection
            from test_roamie_planning import proposal

            output = output.model_copy(
                update={"trip_options": PlanSelection.model_validate(proposal()).options}
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [
                        {"parts": [{"kind": "text", "text": signed_reply(request, output)}]}
                    ],
                },
            },
        )

    approved = ModelResponse(
        content=json.dumps(
            {"decision": "approve", "confidence": 1.0, "summary": "Checked", "issues": []}
        )
    )
    model = ScriptedProvider(
        *[approved for _ in range(6)],
        capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32768),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        manager = PersonalTripManager(
            workers={
                kind: WorkerEndpoint(
                    name=f"roamie-{kind.value}",
                    url=f"https://gateway.example.org/a2a/v1/roamie-{kind.value}",
                )
                for kind in kinds
            },
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=5, client=http),
            supervisor=SupervisorService(provider=model, definition=manager_definition()),
            identity_key=SecretStr("a" * 32),
            delegation_key=SecretStr("d" * 32),
        )
        result = await manager.manage(
            profile=Profile(
                subject="user",
                trip_id="trip",
                revision="1",
                start_date="2026-10-01" if planning else None,
                end_date="2026-10-01" if planning else None,
            ),
            current_revision=lambda: "1",
            specialist=Specialist.TRIP,
            request=RecommendationRequest(prompt="Plan", plan_options=planning),
            facts=facts,
        )
    assert called == list(kinds)
    assert [item.specialist for item in result.advisories] == list(kinds[:2])
    assert "planning_checks" in "".join(
        part.text for part in model.requests[-1].messages[-1].content
    )


def test_multi_destination_dates_stay_within_confirmed_trip():
    from roamie_agents.manager import planning_dates

    profile = Profile(
        subject="user", trip_id="trip", revision="1", start_date="2026-10-01", end_date="2026-10-15"
    )
    request = RecommendationRequest(prompt="Tokyo", start_date="2026-10-04", end_date="2026-10-07")
    assert str(planning_dates(profile, request)["start_date"]) == "2026-10-04"
    with pytest.raises(TravelFailure, match="dates_outside_trip"):
        planning_dates(
            profile,
            RecommendationRequest(prompt="Tokyo", start_date="2026-10-04", end_date="2026-10-20"),
        )
