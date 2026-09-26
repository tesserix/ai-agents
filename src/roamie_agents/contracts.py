from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlencode

from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from roamie_agents.base import Contract, Location
from roamie_agents.exchange import ExchangeComparison


class Specialist(StrEnum):
    WEATHER = "weather"
    ENTRY = "entry-guidance"
    TRIP = "trip-planner"
    FOOD = "food"
    ROUTES = "routes"
    ACTIVITIES = "activities"
    SHOPPING = "shopping"
    MEMORIES = "memories"
    EXCHANGE = "currency-exchange"


class RecommendationRequest(Contract):
    exchange_amount_minor: int | None = Field(default=None, ge=1, le=10**12, strict=True)
    exchange_destination_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    prompt: str = Field(min_length=1, max_length=6000)
    origin: Location | None = None
    destination_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    passport_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    residence_country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    travel_purpose: Literal["tourism", "business", "study", "transit"] = "tourism"
    destination: str = Field(default="", max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    budget_minor: int | None = Field(default=None, ge=0, strict=True)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    accessibility_requirements: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    preferences: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    language: str = Field(default="en", min_length=2, max_length=35)
    categories: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    allergies: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    diets: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    photo_ids: Annotated[tuple[str, ...], Field(max_length=15)] = ()
    photo_consent: bool = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("both trip dates are required")
        if self.start_date and self.end_date:
            if not 0 <= (self.end_date - self.start_date).days <= 30:
                raise ValueError("trip must span 1 to 31 days")
        for items in (
            self.categories,
            self.allergies,
            self.diets,
            self.photo_ids,
            self.accessibility_requirements,
            self.preferences,
        ):
            if any(not value.strip() or len(value) > 120 for value in items):
                raise ValueError("invalid preference or photo reference")
        if self.photo_ids and not self.photo_consent:
            raise ValueError("photo selection requires consent")
        return self


class WeatherDetails(Contract):
    coverage: Literal["forecast", "complete", "partial", "unavailable"]
    local_date: date | None = None
    timezone: str | None = None
    maximum_celsius: float | None = Field(default=None, ge=-100, le=70)
    minimum_celsius: float | None = Field(default=None, ge=-100, le=70)
    precipitation_percent: int | None = Field(default=None, ge=0, le=100)
    uv_index: float | None = Field(default=None, ge=0, le=30)
    missing_dates: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


class EntryDetails(Contract):
    verification: Literal["official_links_only", "verified"] = "official_links_only"
    destination_country: str | None = None
    passport_country: str | None = None
    residence_country: str | None = None
    visa_required: bool | None = None
    visa_fee_minor: int | None = Field(default=None, ge=0)
    visa_fee_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    checklist: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()

    @model_validator(mode="after")
    def verified_fees(self) -> Self:
        if self.verification != "verified" and (
            self.visa_required is not None or self.visa_fee_minor is not None
        ):
            raise ValueError("unverified guidance cannot assert eligibility or fees")
        if (self.visa_fee_minor is None) != (self.visa_fee_currency is None):
            raise ValueError("visa fee requires its original currency")
        return self


class Evidence(Contract):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    weather: WeatherDetails | None = None
    entry: EntryDetails | None = None
    category: Specialist
    source_url: HttpUrl
    observed_at: AwareDatetime
    location: Location | None = None
    cost_minor: int | None = Field(default=None, ge=0, strict=True)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    duration_seconds: int | None = Field(default=None, ge=0, strict=True)
    distance_metres: int | None = Field(default=None, ge=0, strict=True)
    mode: Literal["walking", "driving", "transit", "bicycling"] = "walking"
    price_is_estimate: bool = True
    dietary_tags: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    allergens: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    discount_percent: int | None = Field(default=None, ge=1, le=100, strict=True)
    offer_expires_at: AwareDatetime | None = None
    photo_id: str | None = Field(default=None, max_length=120)
    synthetic: bool = False
    allergen_disclosure_complete: bool = False
    accessibility_tags: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    available_from: date | None = None
    available_until: date | None = None

    @model_validator(mode="after")
    def complete_claims(self) -> Self:
        if (self.available_from is None) != (self.available_until is None):
            raise ValueError("availability requires both dates")
        if (
            self.available_from
            and self.available_until
            and self.available_from > self.available_until
        ):
            raise ValueError("invalid availability interval")
        if (self.cost_minor is None) != (self.currency is None):
            raise ValueError("price requires amount and currency")
        if self.discount_percent is not None and self.offer_expires_at is None:
            raise ValueError("discount requires expiry")
        if self.source_url.scheme != "https":
            raise ValueError("source must use HTTPS")
        return self


class Selection(Contract):
    selected_ids: Annotated[list[str], Field(max_length=20)]


class Recommendation(Evidence):
    maps_url: str | None = None
    warnings: tuple[str, ...] = ()


class TravelResponse(Contract):
    status: Literal["ok", "unavailable", "no_matches"]
    specialist: Specialist
    recommendations: tuple[Recommendation, ...] = ()
    limitations: tuple[str, ...] = ()
    exchange_comparisons: tuple[ExchangeComparison, ...] = ()
    run_id: str | None = None


def project(fact: Evidence, origin: Location | None) -> Recommendation:
    maps_url = None
    if fact.location:
        query = {
            "api": "1",
            "destination": f"{fact.location.latitude},{fact.location.longitude}",
            "travelmode": fact.mode,
        }
        if origin:
            query["origin"] = f"{origin.latitude},{origin.longitude}"
        maps_url = "https://www.google.com/maps/dir/?" + urlencode(query)
    warnings = (
        ("Confirm dietary and allergy requirements with staff.",)
        if (fact.category == Specialist.FOOD)
        else ()
    )
    return Recommendation(**fact.model_dump(), maps_url=maps_url, warnings=warnings)
