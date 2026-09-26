from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from roamie_agents.contracts import EntryDetails, RecommendationRequest, Specialist
from roamie_agents.guidance import EntryGuidanceSource


async def test_official_links_do_not_claim_eligibility_or_invent_fees():
    result = await EntryGuidanceSource().search(
        Specialist.ENTRY,
        RecommendationRequest(
            prompt="Visa", destination_country="AU", passport_country="IN", residence_country="SG"
        ),
    )
    fact = result.facts[0]
    assert str(fact.source_url).startswith("https://immi.homeaffairs.gov.au/")
    assert fact.entry.passport_country == "IN"
    assert fact.entry.visa_required is None
    assert fact.entry.visa_fee_minor is None
    assert fact.entry.verification == "official_links_only"
    assert any("fees" in step for step in fact.entry.checklist)
    assert fact.observed_at <= datetime.now(UTC)


async def test_unknown_destination_remains_explicitly_unverified():
    result = await EntryGuidanceSource().search(
        Specialist.ENTRY, RecommendationRequest(prompt="Trip")
    )
    assert set(result.facts[0].entry.missing_information) >= {
        "destination_country",
        "passport_country",
        "residence_country",
    }
    assert result.facts[0].entry.visa_required is None


@pytest.mark.parametrize(
    "fields",
    [
        {"visa_required": False},
        {"visa_fee_minor": 0, "visa_fee_currency": "AUD"},
        {"verification": "verified", "visa_fee_minor": 100},
    ],
)
def test_unverified_requirements_or_currencyless_fees_are_rejected(fields):
    with pytest.raises(ValidationError):
        EntryDetails(**fields)
