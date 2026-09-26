async def test_unknown_allergen_disclosure_is_not_safe_for_allergic_profile():
    from datetime import UTC, datetime

    from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist
    from roamie_agents.runtime import TravelService

    now = datetime.now(UTC)
    fact = Evidence(
        id="x", name="Cafe", category="food", source_url="https://example.com", observed_at=now
    )
    from tesserix_adk.testing import ScriptedProvider

    service = TravelService(provider=ScriptedProvider(), clock=lambda: now)
    result = await service.recommend(
        Specialist.FOOD, RecommendationRequest(prompt="food", allergies=("peanut",)), facts=[fact]
    )
    assert result.status == "no_matches"
    assert result.recommendations == ()


async def test_accessibility_and_trip_dates_require_matching_evidence():
    from datetime import UTC, datetime

    from tesserix_adk.testing import ScriptedProvider

    from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist
    from roamie_agents.runtime import TravelService

    now = datetime(2026, 9, 26, tzinfo=UTC)
    request = RecommendationRequest(
        prompt="museum",
        accessibility_requirements=("step-free",),
        start_date="2026-09-27",
        end_date="2026-09-28",
    )
    fact = Evidence(
        id="x",
        name="Museum",
        category="activities",
        source_url="https://example.com",
        observed_at=now,
    )
    result = await TravelService(provider=ScriptedProvider(), clock=lambda: now).recommend(
        Specialist.ACTIVITIES, request, facts=[fact]
    )
    assert result.status == "no_matches"
