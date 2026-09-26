from datetime import UTC, datetime
from decimal import Decimal

import pytest

from roamie_agents.exchange import ExchangeQuote, ReferenceRate, compare_quotes

NOW = datetime(2026, 9, 26, 1, tzinfo=UTC)


def quote(**values):
    return ExchangeQuote.model_validate(
        {
            "id": "shop-1",
            "shop": "Exchange shop",
            "source_currency": "AUD",
            "destination_currency": "JPY",
            "rate": "100",
            "observed_at": NOW,
            "expires_at": "2026-09-26T02:00:00Z",
            "source_url": "https://example.org/rates",
            "fees_complete": True,
            **values,
        }
    )


def test_ranks_net_receipts_after_all_fees_against_reference():
    reference = ReferenceRate(
        source_currency="AUD",
        destination_currency="JPY",
        rate=Decimal("101"),
        observed_at=NOW,
        source_url="https://example.org/reference",
    )
    result = compare_quotes(
        amount_minor=10000,
        source_exponent=2,
        destination_exponent=0,
        reference=reference,
        quotes=[
            quote(id="high-fee", rate="102", fixed_source_fee_minor=500),
            quote(id="better-net", rate="100"),
        ],
        now=NOW,
    )
    assert result[0].quote.id == "better-net"
    assert result[0].received_minor == 10000
    assert result[0].reference_minor == 10100
    assert result[1].received_minor == 9690


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": "2026-09-25T23:00:00Z"},
        {"observed_at": "2026-09-26T01:01:00Z"},
        {"fees_complete": False},
        {"source_currency": "USD"},
        {"minimum_amount_minor": 20000},
        {"fixed_source_fee_minor": 10001},
    ],
)
def test_unusable_quotes_never_win(changes):
    reference = ReferenceRate(
        source_currency="AUD",
        destination_currency="JPY",
        rate="101",
        observed_at=NOW,
        source_url="https://example.org/reference",
    )
    assert (
        compare_quotes(
            amount_minor=10000,
            source_exponent=2,
            destination_exponent=0,
            reference=reference,
            quotes=[quote(**changes)],
            now=NOW,
        )
        == ()
    )


def test_three_decimal_currency_and_percentage_fee_round_down():
    reference = ReferenceRate(
        source_currency="AUD",
        destination_currency="KWD",
        rate="0.2",
        observed_at=NOW,
        source_url="https://example.org/reference",
    )
    result = compare_quotes(
        amount_minor=12345,
        source_exponent=2,
        destination_exponent=3,
        reference=reference,
        quotes=[
            quote(
                destination_currency="KWD",
                rate="0.2",
                fee_basis_points=100,
                fixed_destination_fee_minor=25,
                cash_increment_minor=5,
            )
        ],
        now=NOW,
    )
    assert result[0].received_minor == 24415


@pytest.mark.parametrize("amount", [0, -1, 1.5, True])
def test_invalid_amount_rejected(amount):
    reference = ReferenceRate(
        source_currency="AUD",
        destination_currency="JPY",
        rate="101",
        observed_at=NOW,
        source_url="https://example.org/reference",
    )
    with pytest.raises(ValueError):
        compare_quotes(
            amount_minor=amount,
            source_exponent=2,
            destination_exponent=0,
            reference=reference,
            quotes=[],
            now=NOW,
        )


async def test_currency_specialist_uses_deterministic_comparator_without_model_call():
    from tesserix_adk.testing import ScriptedProvider

    from roamie_agents.contracts import RecommendationRequest, Specialist
    from roamie_agents.runtime import TravelService

    service = TravelService(provider=ScriptedProvider(), clock=lambda: NOW)
    result = await service.recommend(
        Specialist.EXCHANGE,
        RecommendationRequest(
            prompt="Best exchange",
            currency="AUD",
            exchange_amount_minor=10000,
            exchange_destination_currency="JPY",
        ),
        facts=[],
        reference_rate=ReferenceRate(
            source_currency="AUD",
            destination_currency="JPY",
            rate="101",
            observed_at=NOW,
            source_url="https://example.org/reference",
        ),
        exchange_quotes=[quote()],
    )
    assert result.exchange_comparisons[0].received_minor == 10000
    assert result.status == "ok"
