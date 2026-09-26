import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError
from tesserix_adk.core import AgentIdentity, Principal

from roamie_agents.base import Contract
from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist, TravelResponse
from roamie_agents.exchange import ExchangeQuote, ReferenceRate


class DelegationError(Exception):
    pass


class WorkerPayload(Contract):
    reference_rate: ReferenceRate | None = None
    exchange_quotes: Annotated[list[ExchangeQuote], Field(max_length=40)] = Field(
        default_factory=list
    )
    request: RecommendationRequest
    facts: Annotated[list[Evidence], Field(max_length=40)]


class DelegationContext(Contract):
    tenant: Literal["roamie"] = "roamie"
    manager_id: str = Field(pattern=r"^trip-manager-[a-f0-9]{64}$")
    profile_revision: str = Field(min_length=1, max_length=120)
    specialist: Specialist
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    issued_at: int = Field(ge=0, strict=True)
    expires_at: int = Field(ge=0, strict=True)


class DelegatedRequest(Contract):
    context: DelegationContext
    payload: WorkerPayload
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


class DelegatedResponse(Contract):
    context: DelegationContext
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    response: TravelResponse
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


def canonical(value: Contract) -> bytes:
    return json.dumps(
        value.model_dump(mode="json", exclude={"signature"}), sort_keys=True, separators=(",", ":")
    ).encode()


class Delegations:
    def __init__(self, *, key: SecretStr, clock: Callable[[], float] = time.time) -> None:
        if len(key.get_secret_value()) < 32:
            raise ValueError("delegation signing key requires at least 32 characters")
        self._key = key
        self._clock = clock

    def _signature(self, message: Contract, purpose: str) -> str:
        return hmac.new(
            self._key.get_secret_value().encode(),
            purpose.encode() + b"\x00" + canonical(message),
            hashlib.sha256,
        ).hexdigest()

    def _check(self, message: DelegatedRequest | DelegatedResponse, purpose: str) -> None:
        context = message.context
        if not hmac.compare_digest(message.signature, self._signature(message, purpose)):
            raise DelegationError("invalid delegation signature")
        if not context.issued_at <= self._clock() < context.expires_at <= context.issued_at + 120:
            raise DelegationError("expired delegation")

    def request(
        self, *, manager_id: str, revision: str, specialist: Specialist, payload: WorkerPayload
    ) -> DelegatedRequest:
        now = int(self._clock())
        value = DelegatedRequest(
            context=DelegationContext(
                manager_id=manager_id,
                profile_revision=revision,
                specialist=specialist,
                request_id=secrets.token_hex(16),
                issued_at=now,
                expires_at=now + 120,
            ),
            payload=payload,
            signature="0" * 64,
        )
        return value.model_copy(update={"signature": self._signature(value, "request")})

    def verify_request(
        self, raw: str, specialist: Specialist
    ) -> tuple[DelegatedRequest, AgentIdentity]:
        try:
            value = DelegatedRequest.model_validate_json(raw)
        except ValidationError as error:
            raise DelegationError("invalid delegation") from error
        self._check(value, "request")
        if value.context.specialist != specialist:
            raise DelegationError("wrong delegation audience")
        scope = "roamie." + specialist.value
        identity = AgentIdentity.resolve(
            agent="roamie-" + specialist.value,
            declared=(scope,),
            principal=Principal(
                subject=value.context.manager_id,
                tenant="roamie",
                scopes=frozenset({scope}),
                expires_at=float(value.context.expires_at),
            ),
            now=self._clock(),
        )
        return value, identity

    def response(self, request: DelegatedRequest, response: TravelResponse) -> DelegatedResponse:
        self._check(request, "request")
        if response.specialist != request.context.specialist:
            raise DelegationError("wrong response audience")
        value = DelegatedResponse(
            context=request.context,
            request_digest=hashlib.sha256(canonical(request)).hexdigest(),
            response=response,
            signature="0" * 64,
        )
        return value.model_copy(update={"signature": self._signature(value, "response")})

    def verify_response(self, raw: str, request: DelegatedRequest) -> TravelResponse:
        try:
            value = DelegatedResponse.model_validate_json(raw)
        except ValidationError as error:
            raise DelegationError("invalid delegated response") from error
        self._check(value, "response")
        if (
            value.context != request.context
            or value.request_digest != hashlib.sha256(canonical(request)).hexdigest()
            or value.response.specialist != request.context.specialist
        ):
            raise DelegationError("response identity mismatch")
        return value.response
