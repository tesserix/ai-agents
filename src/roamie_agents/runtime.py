import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from tesserix_adk.core import ModelProvider
from tesserix_adk.guardrails import InjectionGuard
from tesserix_adk.runtime import AgentRunner

from roamie_agents.contracts import (
    Evidence,
    RecommendationRequest,
    Selection,
    Specialist,
    TravelResponse,
    project,
)
from roamie_agents.definitions import DEFINITIONS
from roamie_agents.exchange import CURRENCY_EXPONENTS, ExchangeQuote, ReferenceRate, compare_quotes


class TravelFailure(Exception):
    pass


class TravelService:
    def __init__(
        self,
        *,
        provider: ModelProvider | Mapping[Specialist, ModelProvider],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self._clock = clock

    async def recommend(
        self,
        specialist: Specialist,
        request: RecommendationRequest,
        *,
        facts: Sequence[Evidence],
        reference_rate: ReferenceRate | None = None,
        exchange_quotes: list[ExchangeQuote] | None = None,
    ) -> TravelResponse:
        if specialist == Specialist.EXCHANGE:
            if reference_rate is None or request.exchange_amount_minor is None:
                raise TravelFailure("exchange_quote_required")
            if (request.currency, request.exchange_destination_currency) != (
                reference_rate.source_currency,
                reference_rate.destination_currency,
            ):
                raise TravelFailure("exchange_currency_mismatch")
            try:
                comparisons = compare_quotes(
                    amount_minor=request.exchange_amount_minor,
                    source_exponent=CURRENCY_EXPONENTS[reference_rate.source_currency],
                    destination_exponent=CURRENCY_EXPONENTS[reference_rate.destination_currency],
                    reference=reference_rate,
                    quotes=exchange_quotes or [],
                    now=self._clock(),
                )
            except (ValueError, KeyError) as error:
                raise TravelFailure("exchange_quote_unavailable") from error
            return TravelResponse(
                status="ok" if comparisons else "no_matches",
                specialist=specialist,
                exchange_comparisons=comparisons,
                limitations=("Advertised quotes; confirm fees before exchanging.",),
            )
        if len(facts) > 40 or len({fact.id for fact in facts}) != len(facts):
            raise TravelFailure("invalid_evidence")
        now = self._clock()
        eligible = [fact for fact in facts if self._eligible(fact, specialist, request, now)]
        if not eligible:
            return TravelResponse(
                status="no_matches",
                specialist=specialist,
                limitations=("No current matching provider evidence.",),
            )
        definition = DEFINITIONS[specialist]
        runner = AgentRunner(
            provider=self._provider[specialist]
            if isinstance(self._provider, Mapping)
            else self._provider,
            guardrails={
                "injection": InjectionGuard(instructions=definition.agent.instructions),
            },
        )
        prompt = json.dumps(
            {
                "REQUEST": request.model_dump(mode="json"),
                "EVIDENCE": [fact.model_dump(mode="json") for fact in eligible],
            }
        )
        if len(prompt.encode()) > 48000:
            raise TravelFailure("evidence_too_large")
        run = await runner.run(definition, prompt, tenant="roamie")
        if run.state.value != "completed" or not isinstance(run.output, Selection):
            raise TravelFailure("model_failed")
        ids = run.output.selected_ids
        by_id = {fact.id: fact for fact in eligible}
        if len(ids) != len(set(ids)) or any(key not in by_id for key in ids):
            raise TravelFailure("unsupported_citation")
        if specialist in (Specialist.WEATHER, Specialist.ENTRY):
            ids = list(by_id)
        if specialist == Specialist.TRIP and any(
            by_id[key].category in (Specialist.WEATHER, Specialist.ENTRY) for key in ids
        ):
            raise TravelFailure("planning_check_is_not_a_place")
        selected = [by_id[key] for key in ids]
        if request.budget_minor is not None and specialist == Specialist.TRIP:
            known_total = sum(fact.cost_minor or 0 for fact in selected)
            if known_total > request.budget_minor:
                raise TravelFailure("budget_exceeded")
        return TravelResponse(
            status="ok" if selected else "no_matches",
            specialist=specialist,
            recommendations=tuple(project(fact, request.origin) for fact in selected),
            limitations=(
                "Ranked among returned sources; unknown prices and times remain unknown.",
            ),
            run_id=run.id,
        )

    @staticmethod
    def _eligible(
        fact: Evidence, specialist: Specialist, request: RecommendationRequest, now: datetime
    ) -> bool:
        planning_fact = fact.category in (Specialist.WEATHER, Specialist.ENTRY)
        if not planning_fact and not {v.casefold() for v in request.accessibility_requirements} <= {
            v.casefold() for v in fact.accessibility_tags
        }:
            return False
        if (
            not planning_fact
            and request.start_date
            and request.end_date
            and (
                fact.available_from is None
                or fact.available_until is None
                or fact.available_from > request.start_date
                or fact.available_until < request.end_date
            )
        ):
            return False
        if fact.weather and fact.weather.local_date and request.start_date and request.end_date:
            if not request.start_date <= fact.weather.local_date <= request.end_date:
                return False
        if fact.observed_at > now or now - fact.observed_at > timedelta(
            hours=1 if specialist == Specialist.WEATHER else 24
        ):
            return False
        if specialist != Specialist.TRIP and fact.category != specialist:
            return False
        if specialist == Specialist.TRIP and fact.category == Specialist.MEMORIES:
            return False
        if fact.offer_expires_at is not None and fact.offer_expires_at <= now:
            return False
        if request.budget_minor is not None and fact.cost_minor is not None:
            if fact.currency != request.currency or fact.cost_minor > request.budget_minor:
                return False
        if fact.category == Specialist.FOOD:
            if request.allergies and not fact.allergen_disclosure_complete:
                return False
            if {v.casefold() for v in request.allergies} & {v.casefold() for v in fact.allergens}:
                return False
            if not {v.casefold() for v in request.diets} <= {
                v.casefold() for v in fact.dietary_tags
            }:
                return False
        if specialist == Specialist.MEMORIES:
            return request.photo_consent and fact.photo_id in request.photo_ids
        return True
