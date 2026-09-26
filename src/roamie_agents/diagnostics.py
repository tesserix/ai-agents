import re
import secrets
from typing import Literal

import structlog
from tesserix_adk.core import RunState

from roamie_agents.runtime import TravelFailure

_REASONS = frozenset(
    [
        "budget_currency_mismatch",
        "budget_exceeded",
        "context_too_large",
        "dates_outside_trip",
        "deadline_exceeded",
        "evidence_expired",
        "evidence_too_large",
        "exchange_currency_mismatch",
        "exchange_quote_required",
        "exchange_quote_unavailable",
        "invalid_budget_tiers",
        "invalid_evidence",
        "invalid_planning_stop",
        "invalid_trip_options",
        "invalid_worker_reply",
        "invalid_worker_status",
        "manager_review_unavailable",
        "model_failed",
        "photo_not_authorized",
        "place_destination_mismatch",
        "planning_check_is_not_a_place",
        "planning_dates_mismatch",
        "planning_dates_required",
        "planning_destinations_mismatch",
        "planning_unavailable",
        "profile_authority_unavailable",
        "profile_changed",
        "provider_unavailable",
        "request_requires_clarification",
        "response_not_approved",
        "schema_mismatch",
        "specialist_unavailable",
        "three_distinct_tiers_required",
        "tool_unavailable",
        "tools_unavailable",
        "unclassified",
        "unexpected_trip_options",
        "unknown_accommodation",
        "unsupported_accommodation",
        "unsupported_citation",
        "unsupported_worker_claim",
        "wrong_specialist",
    ]
)


def record_failure(component: Literal["manager", "worker"], error: Exception) -> str:
    request_id = secrets.token_hex(16)
    reason = "deadline_exceeded" if isinstance(error, TimeoutError) else str(error)
    run_id = error.run_id if isinstance(error, TravelFailure) else None
    state = error.state if isinstance(error, TravelFailure) else None
    cause_kind = type(error.__cause__).__name__
    allowed_causes = {
        "WorkerCallError",
        "SupervisionFailedError",
        "ValidationError",
        "DelegationError",
        "CredentialExpiredError",
        "PeerDiscoveryError",
        "TimeoutError",
    }
    if cause_kind == "SupervisionFailedError":
        state = getattr(error.__cause__, "state", None)
    structlog.get_logger(__name__).warning(
        "roamie_request_failed",
        component=component,
        request_id=request_id,
        reason=reason if reason in _REASONS else "unclassified",
        run_id=run_id
        if isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", run_id)
        else None,
        state=state if isinstance(state, str) and state in RunState else None,
        cause=cause_kind if cause_kind in allowed_causes else None,
    )
    return request_id
