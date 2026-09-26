import asyncio
import hashlib
import hmac
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import Field, SecretStr, ValidationError
from tesserix_adk.a2a.discovery import PeerDiscoveryError
from tesserix_adk.core import CredentialExpiredError, Principal, principal_scope

from orchestrator_agent.config import WorkerEndpoint
from orchestrator_agent.supervision import SupervisionFailedError, SupervisorService
from orchestrator_agent.workers import A2AWorkerClient, WorkerCallError
from roamie_agents.base import Contract
from roamie_agents.contracts import (
    Evidence,
    RecommendationRequest,
    Specialist,
    TravelResponse,
    project,
)
from roamie_agents.delegation import DelegationError, Delegations, WorkerPayload
from roamie_agents.discovery import RegistryWorkers
from roamie_agents.exchange import CURRENCY_EXPONENTS, ExchangeQuote, ReferenceRate, compare_quotes
from roamie_agents.runtime import TravelFailure, TravelService


class Profile(Contract):
    subject: str = Field(min_length=1, max_length=256)
    trip_id: str = Field(min_length=1, max_length=120)
    revision: str = Field(min_length=1, max_length=120)
    allergies: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    diets: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    budget_minor: int | None = Field(default=None, ge=0, strict=True)
    currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    accessibility_requirements: Annotated[tuple[str, ...], Field(max_length=12)] = ()
    preferences: Annotated[tuple[str, ...], Field(max_length=20)] = ()
    language: str = Field(default="en", min_length=2, max_length=35)
    start_date: date | None = None
    end_date: date | None = None
    photo_consent: bool = False
    selected_photo_ids: Annotated[tuple[str, ...], Field(max_length=15)] = ()


def planning_dates(profile: Profile, request: RecommendationRequest) -> dict[str, date | None]:
    start, end = request.start_date or profile.start_date, request.end_date or profile.end_date
    if (
        (start and profile.start_date and start < profile.start_date)
        or (end and profile.end_date and end > profile.end_date)
        or (start and end and end < start)
    ):
        raise TravelFailure("dates_outside_trip")
    return {"start_date": start, "end_date": end}


class ManagedResponse(Contract):
    manager_id: str
    profile_revision: str
    response: TravelResponse
    review_run_ids: tuple[str, str]
    advisories: tuple[TravelResponse, ...] = ()


async def revision_value(check: Callable[[], str | Awaitable[str]]) -> str:
    result = check()
    return await result if inspect.isawaitable(result) else result


class PersonalTripManager:
    def __init__(
        self,
        *,
        workers: Mapping[Specialist, WorkerEndpoint],
        client: A2AWorkerClient,
        supervisor: SupervisorService,
        identity_key: SecretStr,
        delegation_key: SecretStr,
        discovery: RegistryWorkers | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if len(identity_key.get_secret_value()) < 32:
            raise ValueError("manager identity key must contain at least 32 characters")
        self._discovery = discovery
        self._workers = dict(workers)
        self._client = client
        self._supervisor = supervisor
        self._identity_key = identity_key
        self._clock = clock
        self._delegations = Delegations(key=delegation_key)

    async def manage(
        self,
        *,
        profile: Profile,
        current_revision: Callable[[], str | Awaitable[str]],
        specialist: Specialist,
        request: RecommendationRequest,
        facts: Sequence[Evidence],
        reference_rate: ReferenceRate | None = None,
        exchange_quotes: list[ExchangeQuote] | None = None,
    ) -> ManagedResponse:
        try:
            async with asyncio.timeout(120):
                with principal_scope(
                    Principal(
                        subject=self.manager_id(profile),
                        tenant="roamie",
                        scopes=frozenset({"roamie.manager"}),
                    )
                ):
                    return await self._manage(
                        profile,
                        current_revision,
                        specialist,
                        request,
                        facts,
                        reference_rate,
                        exchange_quotes,
                    )
        except (
            TimeoutError,
            WorkerCallError,
            SupervisionFailedError,
            ValidationError,
            DelegationError,
            CredentialExpiredError,
            PeerDiscoveryError,
        ) as error:
            raise TravelFailure("manager_review_unavailable") from error

    def manager_id(self, profile: Profile) -> str:
        material = json.dumps(
            ["roamie", profile.subject, profile.trip_id], separators=(",", ":"), ensure_ascii=False
        )
        identity = hmac.new(
            self._identity_key.get_secret_value().encode(), material.encode(), hashlib.sha256
        ).hexdigest()
        return f"trip-manager-{identity}"

    async def _manage(
        self,
        profile: Profile,
        current_revision: Callable[[], str | Awaitable[str]],
        specialist: Specialist,
        request: RecommendationRequest,
        facts: Sequence[Evidence],
        reference_rate: ReferenceRate | None,
        exchange_quotes: list[ExchangeQuote] | None,
    ) -> ManagedResponse:
        endpoint = (
            await self._discovery.find(specialist)
            if self._discovery is not None
            else self._workers.get(specialist)
        )
        if endpoint is None:
            raise TravelFailure("specialist_unavailable")
        if await revision_value(current_revision) != profile.revision:
            raise TravelFailure("profile_changed")
        effective = request.model_dump()
        effective.update(
            allergies=tuple(sorted(set(profile.allergies) | set(request.allergies))),
            diets=tuple(sorted(set(profile.diets) | set(request.diets))),
            currency=profile.currency,
            accessibility_requirements=tuple(
                sorted(
                    set(profile.accessibility_requirements)
                    | set(request.accessibility_requirements)
                )
            ),
            preferences=tuple(dict.fromkeys((*profile.preferences, *request.preferences))),
            language=profile.language,
            **planning_dates(profile, request),
            photo_consent=profile.photo_consent and request.photo_consent,
        )
        if request.currency != profile.currency and request.budget_minor is not None:
            raise TravelFailure("budget_currency_mismatch")
        budgets = [
            value for value in (profile.budget_minor, request.budget_minor) if value is not None
        ]
        effective["budget_minor"] = min(budgets) if budgets else None
        if set(request.photo_ids) - set(profile.selected_photo_ids):
            raise TravelFailure("photo_not_authorized")
        bounded = RecommendationRequest.model_validate(effective)
        advisories: list[TravelResponse] = []
        if specialist == Specialist.TRIP:
            for kind in (Specialist.WEATHER, Specialist.ENTRY):
                supporting = [fact for fact in facts if fact.category == kind]
                if supporting:
                    advisory = await self._manage(
                        profile, current_revision, kind, bounded, supporting, None, None
                    )
                    advisories.append(advisory.response)
                else:
                    advisories.append(
                        TravelResponse(
                            specialist=kind,
                            status="unavailable",
                            limitations=(
                                (
                                    "This planning check could not be verified. "
                                    "Check official sources before booking."
                                ),
                            ),
                        )
                    )
        candidates = [
            fact
            for fact in facts
            if TravelService._eligible(
                fact,
                specialist,
                bounded,
                self._clock(),
            )
        ]
        if len(candidates) > 40 or len({fact.id for fact in candidates}) != len(candidates):
            raise TravelFailure("invalid_evidence")
        context = json.dumps(
            {
                "planning_checks": [item.model_dump(mode="json") for item in advisories],
                "preferences": bounded.model_dump(mode="json", exclude={"prompt"}),
                "evidence": [fact.model_dump(mode="json") for fact in candidates],
                "reference_rate": reference_rate.model_dump(mode="json")
                if reference_rate
                else None,
                "exchange_quotes": [q.model_dump(mode="json") for q in exchange_quotes or []],
            }
        )
        if len(context.encode()) > 40000:
            raise TravelFailure("context_too_large")
        preflight = await self._supervisor.supervise(
            task=(
                "Check whether the proposed travel request is compatible with the confirmed "
                "profile constraints. This is a pre-execution review, not a completed answer."
            ),
            stage="request",
            answer=bounded.prompt,
            context=context,
            tenant="roamie",
        )
        if preflight.verdict.decision != "approve" or preflight.verdict.confidence < 0.8:
            raise TravelFailure("request_requires_clarification")
        delegation = self._delegations.request(
            manager_id=self.manager_id(profile),
            revision=profile.revision,
            specialist=specialist,
            payload=WorkerPayload(
                request=bounded,
                facts=candidates,
                reference_rate=reference_rate,
                exchange_quotes=exchange_quotes or [],
            ),
        )
        reply = await self._client.send(endpoint, delegation.model_dump_json())
        if reply.state != "completed" or len(reply.text.encode()) > 65536:
            raise TravelFailure("invalid_worker_reply")
        response = self._delegations.verify_response(reply.text, delegation)
        if response.specialist != specialist:
            raise TravelFailure("wrong_specialist")
        if specialist == Specialist.EXCHANGE:
            if reference_rate is None or bounded.exchange_amount_minor is None:
                raise TravelFailure("exchange_quote_required")
            if (bounded.currency, bounded.exchange_destination_currency) != (
                reference_rate.source_currency,
                reference_rate.destination_currency,
            ):
                raise TravelFailure("exchange_currency_mismatch")
            expected = compare_quotes(
                amount_minor=bounded.exchange_amount_minor,
                source_exponent=CURRENCY_EXPONENTS[reference_rate.source_currency],
                destination_exponent=CURRENCY_EXPONENTS[reference_rate.destination_currency],
                reference=reference_rate,
                quotes=exchange_quotes or [],
                now=self._clock(),
            )
            if response.exchange_comparisons != expected:
                raise TravelFailure("unsupported_worker_claim")
        elif response.exchange_comparisons:
            raise TravelFailure("unsupported_worker_claim")
        by_id = {fact.id: project(fact, bounded.origin) for fact in candidates}
        seen = set()
        for recommendation in response.recommendations:
            if recommendation.id in seen or by_id.get(recommendation.id) != recommendation:
                raise TravelFailure("unsupported_worker_claim")
            seen.add(recommendation.id)
        if response.status != "ok" and response.recommendations:
            raise TravelFailure("invalid_worker_status")
        if specialist == Specialist.TRIP and bounded.budget_minor is not None:
            if (
                sum(item.cost_minor or 0 for item in response.recommendations)
                > bounded.budget_minor
            ):
                raise TravelFailure("budget_exceeded")
        reviewed = await self._supervisor.supervise(
            task=bounded.prompt,
            answer=response.model_dump_json(),
            context=context,
            tenant="roamie",
        )
        if reviewed.verdict.decision != "approve" or reviewed.verdict.confidence < 0.8:
            raise TravelFailure("response_not_approved")
        if await revision_value(current_revision) != profile.revision:
            raise TravelFailure("profile_changed")
        now = self._clock()
        fresh_ids = {
            fact.id
            for fact in candidates
            if TravelService._eligible(fact, specialist, bounded, now)
        }
        if any(item.id not in fresh_ids for item in response.recommendations):
            raise TravelFailure("evidence_expired")
        if specialist == Specialist.EXCHANGE and reference_rate is not None:
            if bounded.exchange_amount_minor is None:
                raise TravelFailure("exchange_quote_required")
            fresh_comparison = compare_quotes(
                quotes=exchange_quotes or [],
                reference=reference_rate,
                amount_minor=bounded.exchange_amount_minor,
                source_exponent=CURRENCY_EXPONENTS[reference_rate.source_currency],
                destination_exponent=CURRENCY_EXPONENTS[reference_rate.destination_currency],
                now=self._clock(),
            )
            if response.exchange_comparisons != fresh_comparison:
                raise TravelFailure("evidence_expired")
        clean = response.model_copy(
            update={
                "limitations": (
                    "Reviewed against your trip preferences and available provider evidence.",
                    "Prices, availability and travel times may change; confirm before visiting.",
                    *response.limitations,
                )
            }
        )
        return ManagedResponse(
            manager_id=self.manager_id(profile),
            profile_revision=profile.revision,
            response=clean,
            review_run_ids=(preflight.run_id, reviewed.run_id),
            advisories=tuple(advisories),
        )
