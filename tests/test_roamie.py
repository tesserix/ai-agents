from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tesserix_adk.core import ModelCapabilities
from tesserix_adk.runtime import ModelResponse
from tesserix_adk.testing import ScriptedProvider

from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist
from roamie_agents.runtime import TravelFailure, TravelService


def evidence(**changes):
    return Evidence.model_validate(
        {
            "id": "place-1",
            "name": "Museum",
            "source_url": "https://example.org/museum",
            "observed_at": "2026-09-26T00:00:00Z",
            "category": "activities",
            **changes,
        }
    )


def provider(content):
    return ScriptedProvider(
        ModelResponse(content=content),
        capabilities=ModelCapabilities(
            structured_output=True,
            context_window_tokens=32768,
        ),
    )


async def test_runtime_projects_only_selected_provider_facts():
    service = TravelService(
        provider=provider('{"selected_ids":["place-1"]}'),
        clock=lambda: datetime(2026, 9, 26, 1, tzinfo=UTC),
    )
    result = await service.recommend(
        Specialist.ACTIVITIES,
        RecommendationRequest(prompt="Visit a museum"),
        facts=[evidence(cost_minor=1500, currency="AUD", duration_seconds=600)],
    )
    assert result.recommendations[0].cost_minor == 1500
    assert result.recommendations[0].duration_seconds == 600
    assert str(result.recommendations[0].source_url) == "https://example.org/museum"
    assert result.status == "ok"


@pytest.mark.parametrize("kind", [kind for kind in Specialist if kind != Specialist.EXCHANGE])
async def test_every_specialist_runs_on_adk(kind):
    request = RecommendationRequest(prompt="Recommend", photo_ids=("photo-1",), photo_consent=True)
    service = TravelService(
        provider=provider('{"selected_ids":["place-1"]}'),
        clock=lambda: datetime(2026, 9, 26, 1, tzinfo=UTC),
    )
    result = await service.recommend(
        kind, request, facts=[evidence(category=kind, photo_id="photo-1")]
    )
    assert result.specialist == kind
    assert result.recommendations[0].id == "place-1"


async def test_invented_evidence_is_rejected():
    service = TravelService(
        provider=provider('{"selected_ids":["invented"]}'),
        clock=lambda: datetime(2026, 9, 26, 1, tzinfo=UTC),
    )
    with pytest.raises(TravelFailure, match="unsupported_citation"):
        await service.recommend(
            Specialist.ACTIVITIES, RecommendationRequest(prompt="visit"), facts=[evidence()]
        )


@pytest.mark.parametrize(
    "changes,kind,preferences",
    [
        ({"observed_at": "2026-09-24T00:00:00Z"}, Specialist.ACTIVITIES, {}),
        ({"observed_at": "2026-09-27T00:00:00Z"}, Specialist.ACTIVITIES, {}),
        (
            {"discount_percent": 25, "offer_expires_at": "2026-09-25T00:00:00Z"},
            Specialist.SHOPPING,
            {},
        ),
        ({"allergens": ["Peanut"]}, Specialist.FOOD, {"allergies": ["peanut"]}),
        ({"dietary_tags": []}, Specialist.FOOD, {"diets": ["vegan"]}),
        (
            {"cost_minor": 200, "currency": "USD"},
            Specialist.SHOPPING,
            {"budget_minor": 300, "currency": "AUD"},
        ),
        (
            {"photo_id": "other-photo"},
            Specialist.MEMORIES,
            {"photo_ids": ["mine"], "photo_consent": True},
        ),
    ],
)
async def test_unsafe_or_stale_facts_are_not_sent_to_model(changes, kind, preferences):
    model = provider("unused")
    service = TravelService(provider=model, clock=lambda: datetime(2026, 9, 26, 1, tzinfo=UTC))
    result = await service.recommend(
        kind,
        RecommendationRequest(prompt="help", **preferences),
        facts=[evidence(category=kind, **changes)],
    )
    assert result.status == "no_matches"
    assert result.recommendations == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "other"},
        {"photo_ids": ["photo"]},
        {"budget_minor": -1},
        {"start_date": "2026-10-01", "end_date": "2026-09-01"},
        {"origin": {"latitude": 91, "longitude": 0}},
        {"budget_minor": 1.5},
    ],
)
def test_request_rejects_invalid_boundaries(changes):
    with pytest.raises(ValidationError):
        RecommendationRequest(prompt="help", **changes)


def test_discount_requires_expiry_and_amount_requires_currency():
    with pytest.raises(ValidationError):
        evidence(discount_percent=20)
    with pytest.raises(ValidationError):
        evidence(cost_minor=200)


async def test_trip_total_cannot_exceed_budget():
    service = TravelService(
        provider=provider('{"selected_ids":["place-1","place-2"]}'),
        clock=lambda: datetime(2026, 9, 26, 1, tzinfo=UTC),
    )
    with pytest.raises(TravelFailure, match="budget_exceeded"):
        await service.recommend(
            Specialist.TRIP,
            RecommendationRequest(prompt="plan", budget_minor=1000),
            facts=[
                evidence(cost_minor=600, currency="AUD"),
                evidence(id="place-2", cost_minor=600, currency="AUD"),
            ],
        )


async def test_mcp_client_refuses_schema_drift_before_invocation():
    from tesserix_adk.mcp import McpToolDescriptor
    from tesserix_adk.testing.mcp import FaultyMcpServer

    from roamie_agents.evidence import MCPSource

    session = FaultyMcpServer(
        tools=[
            McpToolDescriptor(
                name="travel_search",
                description="Travel",
                input_schema={"type": "object"},
            )
        ]
    )
    source = MCPSource(session=session, tool_name="travel_search", schema_digest="0" * 64)
    with pytest.raises(TravelFailure, match="schema_mismatch"):
        await source.search(Specialist.FOOD, RecommendationRequest(prompt="dinner"))
    assert session.calls == []


async def test_http_edge_exposes_workers_only_through_a2a():
    import httpx

    from roamie_agents.api import create_app
    from roamie_agents.config import Settings

    settings = Settings(api_key="a" * 32, gateway_api_key="b" * 32, mcp_schema_digest="1" * 64)
    app = create_app(settings=settings, service=TravelService(provider=provider("unused")))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer " + "a" * 32}
        assert (
            await client.post(
                "/v1/recommendations/food", json={"prompt": "dinner"}, headers=headers
            )
        ).status_code == 404
        assert (await client.get("/v1/agents")).status_code == 401
        assert len((await client.get("/v1/agents", headers=headers)).json()["agents"]) == 7


@pytest.mark.parametrize(
    "url,path",
    [
        ("http://gateway.example.org", "/mcp/roamie/travel"),
        ("https://user:pass@gateway.example.org", "/mcp/roamie/travel"),
        ("https://gateway.example.org", "/mcp/other/travel"),
        ("https://gateway.example.org", "/mcp/roamie/../other"),
    ],
)
def test_gateway_configuration_cannot_select_direct_or_other_tenant_routes(url, path):
    from roamie_agents.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            api_key="a" * 32,
            gateway_api_key="b" * 32,
            mcp_schema_digest="1" * 64,
            mcp_gateway_origin=url,
            mcp_gateway_path=path,
        )


async def test_worker_rejects_unknown_or_duplicate_citations():
    for content in ('{"selected_ids":["place-1","place-1"]}', "not-json"):
        service = TravelService(
            provider=provider(content), clock=lambda: datetime(2026, 9, 26, 1, tzinfo=UTC)
        )
        with pytest.raises(TravelFailure):
            await service.recommend(
                Specialist.ACTIVITIES, RecommendationRequest(prompt="visit"), facts=[evidence()]
            )
