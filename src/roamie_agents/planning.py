from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import Field

from roamie_agents.base import Contract


class PlanningBudget(Contract):
    accommodation_minor: int = Field(ge=0, le=10**12, strict=True)
    food_minor: int = Field(ge=0, le=10**12, strict=True)
    activities_minor: int = Field(ge=0, le=10**12, strict=True)
    transport_minor: int = Field(ge=0, le=10**12, strict=True)
    contingency_minor: int = Field(ge=0, le=10**12, strict=True)

    @property
    def total_minor(self) -> int:
        return sum(
            (
                self.accommodation_minor,
                self.food_minor,
                self.activities_minor,
                self.transport_minor,
                self.contingency_minor,
            )
        )


class PlanningStop(Contract):
    evidence_id: str = Field(min_length=1, max_length=120)
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    minutes: int = Field(ge=15, le=480, strict=True)
    note: str = Field(min_length=1, max_length=300)


class PlanningStay(Contract):
    destination: str = Field(min_length=1, max_length=200)
    days: int = Field(ge=1, le=14, strict=True)


class PlanningDay(Contract):
    date: date
    destination: str = Field(min_length=1, max_length=200)
    stops: Annotated[tuple[PlanningStop, ...], Field(min_length=1, max_length=6)]


class TripOption(Contract):
    tier: Literal["budget", "balanced", "premium"]
    label: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    accommodation_ids: Annotated[tuple[str, ...], Field(max_length=3)] = ()
    accommodation_guidance: str = Field(min_length=1, max_length=500)
    transport_guidance: str = Field(min_length=1, max_length=500)
    budget: PlanningBudget
    days: Annotated[tuple[PlanningDay, ...], Field(min_length=1, max_length=14)]


class PlanSelection(Contract):
    options: Annotated[tuple[TripOption, ...], Field(min_length=3, max_length=3)]


def validate_options(
    options: tuple[TripOption, ...],
    *,
    start: date | None,
    end: date | None,
    evidence_ids: set[str],
    ceiling: int | None,
    destinations: list[str] | None = None,
    evidence_destinations: dict[str, str | None] | None = None,
) -> None:
    if start is None or end is None or not 0 <= (end - start).days < 14:
        raise ValueError("planning_dates_required")
    if [option.tier for option in options] != ["budget", "balanced", "premium"]:
        raise ValueError("three_distinct_tiers_required")
    dates = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    previous = -1
    for option in options:
        total = option.budget.total_minor
        if total <= previous or (ceiling is not None and total > ceiling):
            raise ValueError("invalid_budget_tiers")
        previous = total
        if [day.date for day in option.days] != dates:
            raise ValueError("planning_dates_mismatch")
        if any(key not in evidence_ids for key in option.accommodation_ids):
            raise ValueError("unknown_accommodation")
        if destinations is not None and [day.destination for day in option.days] != destinations:
            raise ValueError("planning_destinations_mismatch")
        for day in option.days:
            finish = 0
            for stop in day.stops:
                hour, minute = map(int, stop.time.split(":"))
                begin = hour * 60 + minute
                if (
                    stop.evidence_id not in evidence_ids
                    or begin < finish
                    or begin + stop.minutes > 1440
                ):
                    raise ValueError("invalid_planning_stop")
                if evidence_destinations and evidence_destinations.get(stop.evidence_id) not in (
                    None,
                    day.destination,
                ):
                    raise ValueError("place_destination_mismatch")
                finish = begin + stop.minutes + 15
