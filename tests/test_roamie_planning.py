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
        "transit_past_midnight",
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
    if change == "transit_past_midnight":
        output_stops = value["options"][0]["days"][0]["stops"]
        output_stops[0].update(time="22:00", minutes=60)
        output_stops.append(
            {"evidence_id": "museum", "time": "23:00", "minutes": 60, "note": "late"}
        )
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
    with capture_logs() as logs, pytest.raises(TravelFailure, match="model_failed"):
        await service.recommend(Specialist.TRIP, request(), facts=[evidence()])
    event = next(item for item in logs if item["event"] == "roamie_model_finished")
    assert event["state"] == "failed"
    assert event["specialist"] == "trip-planner"
    assert event["run_id"]
    assert "private-hallucination" not in json.dumps(logs)
    assert "Three Vietnam plans" not in json.dumps(logs)


async def test_short_model_references_restore_original_sources_and_accommodation():
    import json

    hotel = evidence().model_copy(update={"id": "hotel-original", "place_kind": "accommodation"})
    output = proposal()
    for option in output["options"]:
        option["accommodation_ids"] = ["p1"]
        option["days"][0]["stops"][0]["evidence_id"] = "p0"
    model = ScriptedProvider(ModelResponse(content=json.dumps(output)))
    result = await TravelService(provider=model, clock=lambda: NOW).recommend(
        Specialist.TRIP, request(), facts=[evidence(), hotel]
    )
    assert {item.id for item in result.recommendations} == {"museum", "hotel-original"}
    assert result.trip_options[0].accommodation_ids == ("hotel-original",)
    assert result.trip_options[0].days[0].stops[0].evidence_id == "museum"
    prompt = "".join(part.text for part in model.requests[0].messages[-1].content)
    assert {fact["id"] for fact in json.loads(prompt)["EVIDENCE"]} == {"p0", "p1"}


async def test_adjacent_proposed_visits_receive_transit_buffer_before_review():
    import json

    output = proposal()
    output["options"][0]["days"][0]["stops"].append(
        {"evidence_id": "museum", "time": "11:00", "minutes": 60, "note": "Later visit"}
    )
    output["options"][0]["days"][0]["stops"].append(
        {"evidence_id": "museum", "time": "12:00", "minutes": 30, "note": "Last visit"}
    )
    result = await TravelService(
        provider=ScriptedProvider(ModelResponse(content=json.dumps(output))), clock=lambda: NOW
    ).recommend(Specialist.TRIP, request(), facts=[evidence()])
    assert [stop.time for stop in result.trip_options[0].days[0].stops] == [
        "10:00",
        "11:15",
        "12:30",
    ]


@pytest.mark.parametrize(
    "latitude,expected",
    [(21.0325, "11:15"), (21.1184, "11:40"), (21.9, "12:30")],
    ids=["neighbouring", "across-town", "capped-day-trip"],
)
async def test_transit_buffer_grows_with_distance_between_stops(latitude, expected):
    import json

    from roamie_agents.base import Location

    museum = evidence().model_copy(update={"location": Location(latitude=21.0285, longitude=105.8)})
    lake = museum.model_copy(
        update={"id": "lake", "location": Location(latitude=latitude, longitude=105.8)}
    )
    output = proposal()
    for option in output["options"]:
        option["days"][0]["stops"].append(
            {"evidence_id": "lake", "time": "11:00", "minutes": 30, "note": "Lakeside walk"}
        )
    result = await TravelService(
        provider=ScriptedProvider(ModelResponse(content=json.dumps(output))), clock=lambda: NOW
    ).recommend(Specialist.TRIP, request(), facts=[museum, lake])
    assert [stop.time for stop in result.trip_options[0].days[0].stops] == ["10:00", expected]


def test_validation_rejects_plans_that_skip_distance_transit():
    from roamie_agents.base import Location
    from roamie_agents.planning import PlanSelection, validate_options

    value = proposal()
    value["options"][0]["days"][0]["stops"].append(
        {"evidence_id": "lake", "time": "11:15", "minutes": 30, "note": "Lakeside walk"}
    )
    with pytest.raises(ValueError, match="invalid_planning_stop"):
        validate_options(
            PlanSelection.model_validate(value).options,
            start=NOW.date().replace(day=1, month=10),
            end=NOW.date().replace(day=1, month=10),
            evidence_ids={"museum", "lake"},
            ceiling=None,
            locations={
                "museum": Location(latitude=21.0285, longitude=105.8),
                "lake": Location(latitude=21.1184, longitude=105.8),
            },
        )


async def test_invalid_budget_is_repaired_without_regenerating_the_itinerary():
    import json

    invalid = proposal()
    invalid["options"][2]["budget"]["food_minor"] = 10000
    provider = ScriptedProvider(
        ModelResponse(content=json.dumps(invalid)),
        ModelResponse(
            content=json.dumps({"budgets": [o["budget"] for o in proposal()["options"]]})
        ),
    )
    result = await TravelService(provider=provider, clock=lambda: NOW).recommend(
        Specialist.TRIP, request(), facts=[evidence()]
    )
    assert [option.budget.total_minor for option in result.trip_options] == [2000, 4000, 6000]
    assert len(provider.requests) == 2
    repair = json.loads(provider.requests[1].messages[-1].content[0].text)
    assert repair["ceiling_minor"] == 8000
    assert repair["currency"] == "AUD"
    assert "EVIDENCE" not in repair
    assert "days" not in repair["options"][0]
    assert [option.days for option in result.trip_options] == [result.trip_options[0].days] * 3


async def test_invalid_budget_draft_stops_after_one_failed_repair():
    import json

    invalid = proposal()
    invalid["options"][2]["budget"]["food_minor"] = 10000
    provider = ScriptedProvider(*[ModelResponse(content=json.dumps(invalid)) for _ in range(3)])
    with pytest.raises(TravelFailure):
        await TravelService(provider=provider, clock=lambda: NOW).recommend(
            Specialist.TRIP, request(), facts=[evidence()]
        )
    assert len(provider.requests) == 2


async def test_planning_deadline_cancels_a_stuck_model_call(monkeypatch):
    import asyncio

    import roamie_agents.runtime as runtime

    cancelled = asyncio.Event()

    class StuckProvider(ScriptedProvider):
        async def complete(self, request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    timeout = asyncio.timeout
    monkeypatch.setattr(runtime.asyncio, "timeout", lambda _: timeout(0.02))
    with pytest.raises(TravelFailure, match="planning_timeout"):
        await TravelService(provider=StuckProvider(), clock=lambda: NOW).recommend(
            Specialist.TRIP, request(), facts=[evidence()]
        )
    assert cancelled.is_set()


async def test_planner_evidence_omits_null_fields_without_losing_source_data():
    import json

    provider = ScriptedProvider(ModelResponse(content=json.dumps(proposal())))
    await TravelService(provider=provider, clock=lambda: NOW).recommend(
        Specialist.TRIP, request(), facts=[evidence()]
    )
    payload = json.loads(provider.requests[0].messages[-1].content[0].text)
    sent = payload["EVIDENCE"][0]
    assert "weather" not in sent
    assert "cost_minor" not in sent
    sent["id"] = evidence().id
    assert Evidence.model_validate(sent) == evidence()


async def test_budget_repair_cannot_return_an_over_budget_plan():
    import json

    invalid = proposal()
    invalid["options"][2]["budget"]["food_minor"] = 10000
    provider = ScriptedProvider(
        ModelResponse(content=json.dumps(invalid)),
        ModelResponse(content=json.dumps({"budgets": [o["budget"] for o in invalid["options"]]})),
    )
    with pytest.raises(TravelFailure, match="invalid_budget_tiers"):
        await TravelService(provider=provider, clock=lambda: NOW).recommend(
            Specialist.TRIP, request(), facts=[evidence()]
        )
    assert len(provider.requests) == 2


async def test_budget_repair_does_not_add_a_third_model_call_after_grounding_repair():
    import json

    ungrounded = proposal()
    ungrounded["options"][0]["days"][0]["stops"][0]["evidence_id"] = "invented"
    over_budget = proposal()
    over_budget["options"][2]["budget"]["food_minor"] = 10000
    provider = ScriptedProvider(
        ModelResponse(content=json.dumps(ungrounded)),
        ModelResponse(content=json.dumps(over_budget)),
    )
    with pytest.raises(TravelFailure, match="invalid_budget_tiers"):
        await TravelService(provider=provider, clock=lambda: NOW).recommend(
            Specialist.TRIP, request(), facts=[evidence()]
        )
    assert len(provider.requests) == 2
