from datetime import UTC, datetime

import pytest
from tesserix_adk.runtime import ModelResponse
from tesserix_adk.testing import ScriptedProvider

from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist
from roamie_agents.runtime import TravelFailure, TravelService

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def proposal():
    return {
        "options": [
            {
                "tier": tier,
                "label": title,
                "summary": "A proposed day around the museum",
                "accommodation_guidance": (
                    "Compare central accommodation; rates and availability need confirmation"
                ),
                "transport_guidance": "Check local transit connections before departure",
                "budget": {
                    "accommodation_minor": 1000 * n,
                    "food_minor": 500 * n,
                    "activities_minor": 200 * n,
                    "transport_minor": 200 * n,
                    "contingency_minor": 100 * n,
                },
                "days": [
                    {
                        "date": "2026-10-01",
                        "destination": "Hanoi",
                        "stops": [
                            {
                                "evidence_id": "museum",
                                "time": "10:00",
                                "minutes": 60,
                                "note": "Suggested visit; confirm opening hours",
                            }
                        ],
                    }
                ],
            }
            for n, (tier, title) in enumerate(
                [("budget", "Budget"), ("balanced", "Balanced"), ("premium", "Premium")], 1
            )
        ]
    }


def request():
    return RecommendationRequest(
        prompt="Three Vietnam plans",
        destination="Hanoi",
        start_date="2026-10-01",
        end_date="2026-10-01",
        budget_minor=8000,
        currency="AUD",
        plan_options=True,
    )


def evidence():
    return Evidence(
        id="museum",
        name="Museum",
        category="activities",
        source_url="https://maps.google.com/place",
        observed_at=NOW,
        destination="Hanoi",
    )


async def test_three_budget_plans_preserve_grounding_and_budget_totals():
    import json

    service = TravelService(
        provider=ScriptedProvider(ModelResponse(content=json.dumps(proposal()))), clock=lambda: NOW
    )
    result = await service.recommend(Specialist.TRIP, request(), facts=[evidence()])
    assert [option.tier for option in result.trip_options] == ["budget", "balanced", "premium"]
    assert [option.budget.total_minor for option in result.trip_options] == [2000, 4000, 6000]
    assert result.trip_options[0].days[0].stops[0].evidence_id == "museum"
    assert result.recommendations[0].name == "Museum"
    assert result.recommendations[0].cost_minor is None


@pytest.mark.parametrize(
    "change",
    [
        "unknown_place",
        "over_budget",
        "wrong_date",
        "overlap",
        "duplicate_tier",
        "wrong_city",
        "unsupported_accommodation",
    ],
)
async def test_invalid_plan_cannot_reach_the_manager(change):
    import json

    value = proposal()
    if change == "unknown_place":
        value["options"][0]["days"][0]["stops"][0]["evidence_id"] = "invented"
    if change == "over_budget":
        value["options"][2]["budget"]["food_minor"] = 10000
    if change == "wrong_date":
        value["options"][0]["days"][0]["date"] = "2026-10-02"
    if change == "overlap":
        value["options"][0]["days"][0]["stops"].append(
            {"evidence_id": "museum", "time": "10:30", "minutes": 30, "note": "visit"}
        )
    if change == "wrong_city":
        value["options"][0]["days"][0]["destination"] = "Ho Chi Minh City"
    if change == "unsupported_accommodation":
        value["options"][0]["accommodation_ids"] = ["museum"]
    if change == "duplicate_tier":
        value["options"][2]["tier"] = "budget"
    service = TravelService(
        provider=ScriptedProvider(ModelResponse(content=json.dumps(value))), clock=lambda: NOW
    )
    with pytest.raises(TravelFailure):
        await service.recommend(Specialist.TRIP, request(), facts=[evidence()])


def test_plan_survives_signed_wire_roundtrip_without_trusting_a_total():
    from roamie_agents.planning import PlanSelection

    plan = PlanSelection.model_validate(proposal())
    assert PlanSelection.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize("kind", [Specialist.WEATHER, Specialist.ENTRY])
async def test_planning_checks_cannot_be_scheduled_as_places(kind):
    import json

    service = TravelService(
        provider=ScriptedProvider(ModelResponse(content=json.dumps(proposal()))), clock=lambda: NOW
    )
    with pytest.raises(TravelFailure):
        await service.recommend(
            Specialist.TRIP, request(), facts=[evidence().model_copy(update={"category": kind})]
        )


async def test_rejected_plan_records_terminal_run_without_model_or_profile_payload():
    import json

    from structlog.testing import capture_logs

    output = proposal()
    output["options"][0]["days"][0]["stops"][0]["evidence_id"] = "private-hallucination"
    service = TravelService(
        provider=ScriptedProvider(ModelResponse(content=json.dumps(output))), clock=lambda: NOW
    )
    with capture_logs() as logs, pytest.raises(TravelFailure, match="invalid_planning_stop"):
        await service.recommend(Specialist.TRIP, request(), facts=[evidence()])
    event = next(item for item in logs if item["event"] == "roamie_model_finished")
    assert event["state"] == "completed"
    assert event["specialist"] == "trip-planner"
    assert event["run_id"]
    assert "private-hallucination" not in json.dumps(logs)
    assert "Three Vietnam plans" not in json.dumps(logs)
