from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Self

from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from roamie_agents.base import Contract, Location


class ReferenceRate(Contract):
    source_currency: str = Field(pattern=r"^[A-Z]{3}$")
    destination_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: Decimal = Field(gt=0, le=1000000000, max_digits=24, decimal_places=12)
    observed_at: AwareDatetime
    source_url: HttpUrl

    @model_validator(mode="after")
    def distinct_pair(self) -> Self:
        if self.source_currency == self.destination_currency:
            raise ValueError("exchange requires different currencies")
        if self.source_url.scheme != "https":
            raise ValueError("quote provenance requires HTTPS")
        return self


class ExchangeQuote(ReferenceRate):
    id: str = Field(min_length=1, max_length=120)
    shop: str = Field(min_length=1, max_length=200)
    expires_at: AwareDatetime
    fees_complete: bool
    fixed_source_fee_minor: int = Field(default=0, ge=0, le=10**12, strict=True)
    fixed_destination_fee_minor: int = Field(default=0, ge=0, le=10**12, strict=True)
    fee_basis_points: int = Field(default=0, ge=0, le=10000, strict=True)
    minimum_amount_minor: int = Field(default=1, ge=1, le=10**12, strict=True)
    maximum_amount_minor: int = Field(default=10**12, ge=1, le=10**12, strict=True)
    cash_increment_minor: int = Field(default=1, ge=1, le=10000, strict=True)
    location: Location | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=86400, strict=True)

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.expires_at <= self.observed_at:
            raise ValueError("quote expiry must follow observation")
        if self.maximum_amount_minor < self.minimum_amount_minor:
            raise ValueError("invalid amount band")
        return self


class ExchangeComparison(Contract):
    quote: ExchangeQuote
    received_minor: int
    reference_minor: int
    effective_rate: Decimal
    difference_basis_points: Decimal


def compare_quotes(
    *,
    amount_minor: int,
    source_exponent: int,
    destination_exponent: int,
    reference: ReferenceRate,
    quotes: list[ExchangeQuote],
    now: datetime,
) -> tuple[ExchangeComparison, ...]:
    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or not 1 <= amount_minor <= 10**12
    ):
        raise ValueError("amount must be positive integer minor units")
    if any(
        type(exponent) is not int or not 0 <= exponent <= 3
        for exponent in (source_exponent, destination_exponent)
    ):
        raise ValueError("currency exponent must be between zero and three")
    if now.tzinfo is None or not timedelta(0) <= now - reference.observed_at <= timedelta(
        minutes=15
    ):
        raise ValueError("fresh timezone-aware reference rate required")
    if len(quotes) > 40 or len({quote.id for quote in quotes}) != len(quotes):
        raise ValueError("quote IDs must be unique and bounded")
    results = []
    with localcontext() as context:
        context.prec = 48
        source_scale = Decimal(10) ** source_exponent
        destination_scale = Decimal(10) ** destination_exponent
        amount = Decimal(amount_minor) / source_scale
        reference_exact = amount * reference.rate * destination_scale
        reference_minor = int(reference_exact.to_integral_value(rounding=ROUND_DOWN))
        for quote in quotes:
            if (quote.source_currency, quote.destination_currency) != (
                reference.source_currency,
                reference.destination_currency,
            ):
                continue
            if not quote.fees_complete or not quote.observed_at <= now < quote.expires_at:
                continue
            if now - quote.observed_at > timedelta(minutes=15):
                continue
            if not quote.minimum_amount_minor <= amount_minor <= quote.maximum_amount_minor:
                continue
            percentage_fee = Decimal(amount_minor) * quote.fee_basis_points / 10000
            net_source = Decimal(amount_minor) - quote.fixed_source_fee_minor - percentage_fee
            net_destination = (
                net_source / source_scale * quote.rate * destination_scale
                - quote.fixed_destination_fee_minor
            )
            if net_source <= 0 or net_destination <= 0:
                continue
            received = (
                int(
                    (net_destination / quote.cash_increment_minor).to_integral_value(
                        rounding=ROUND_DOWN
                    )
                )
                * quote.cash_increment_minor
            )
            if received <= 0:
                continue
            effective = Decimal(received) / destination_scale / amount
            difference = (Decimal(received) / reference_exact - 1) * 10000
            results.append(
                ExchangeComparison(
                    quote=quote,
                    received_minor=received,
                    reference_minor=reference_minor,
                    effective_rate=effective,
                    difference_basis_points=difference,
                )
            )
    return tuple(sorted(results, key=lambda item: (-item.received_minor, item.quote.id)))


# Reviewed currencies supported by the existing Roamie money surface.
CURRENCY_EXPONENTS = {
    "AUD": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "NZD": 2,
    "CAD": 2,
    "SGD": 2,
    "INR": 2,
    "THB": 2,
    "MYR": 2,
    "CHF": 2,
    "CNY": 2,
    "HKD": 2,
    "AED": 2,
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
}
