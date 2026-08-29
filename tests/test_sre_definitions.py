import pytest
from pydantic import ValidationError
from tesserix_adk.core import ConfigurationError

from sre_agent import tools
from sre_agent.definitions import INVESTIGATOR, Investigation, investigator


def investigation(**overrides) -> Investigation:
    fields = {
        "summary": "marketplace-order-service is crash looping on a database timeout.",
        "incident_suspected": True,
        "symptoms": ("marketplace-order-service has restarted 7 times in 20 minutes.",),
        "evidence": (
            {
                "tool": "get_pod_logs",
                "subject": "marketplace/marketplace-order-service-7c9c6bd4f-lm8vt",
                "observation": "panic: database timeout after 30s",
            },
        ),
        "hypothesis": "The service cannot reach its database and exits on start-up.",
        "confidence": "medium",
        "recommended_actions": (
            {
                "action": "Check the CNPG cluster in the marketplace namespace.",
                "reason": "Every replica fails at the same database call.",
                "urgency": "now",
            },
        ),
        "affected_apps": ("marketplace-order-service",),
    }
    return Investigation.model_validate(fields | overrides)


def test_the_investigator_answers_in_the_reviewed_shape() -> None:
    assert INVESTIGATOR.agent.output_type is Investigation
    assert INVESTIGATOR.agent.free_text is False
    assert INVESTIGATOR.output_schema is not None


def test_the_investigator_may_call_every_read_only_tool_and_nothing_else() -> None:
    assert set(INVESTIGATOR.agent.tools) == set(tools.registry().names)
    assert set(INVESTIGATOR.agent.idempotent_tools) == set(INVESTIGATOR.agent.tools)
    assert INVESTIGATOR.agent.approval_required_tools == ()


def test_the_investigator_is_bounded_in_tool_calls_as_well_as_tokens() -> None:
    budget = INVESTIGATOR.agent.budget

    assert budget is not None
    assert budget.max_tool_calls == 15
    assert budget.max_iterations == 8
    assert budget.max_seconds == 90.0


def test_the_investigator_runs_behind_the_injection_and_pii_guardrails() -> None:
    assert set(INVESTIGATOR.agent.guardrails) == {"injection", "pii"}


def test_the_instructions_forbid_acting_and_forbid_unevidenced_claims() -> None:
    instructions = INVESTIGATOR.agent.instructions.lower()

    assert "read-only" in instructions
    assert "never" in instructions
    assert "evidence" in instructions


def test_the_definition_is_checked_against_the_tools_that_exist() -> None:
    with pytest.raises(ConfigurationError):
        investigator(known_tools=("list_pods",))


def test_an_answer_must_cite_a_tool_for_each_piece_of_evidence() -> None:
    with pytest.raises(ValidationError):
        investigation(evidence=({"subject": "marketplace", "observation": "it is down"},))


def test_an_answer_claiming_an_incident_must_carry_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        investigation(evidence=())


def test_a_healthy_sweep_needs_no_evidence_and_no_hypothesis() -> None:
    quiet = investigation(
        summary="No anomalies in the marketplace namespace.",
        incident_suspected=False,
        symptoms=(),
        evidence=(),
        hypothesis="",
        confidence="low",
        recommended_actions=(),
        affected_apps=(),
    )

    assert quiet.incident_suspected is False


def test_evidence_may_only_cite_a_tool_the_agent_actually_has() -> None:
    with pytest.raises(ValidationError, match="kubectl_delete"):
        investigation(
            evidence=(
                {
                    "tool": "kubectl_delete",
                    "subject": "marketplace/pod",
                    "observation": "removed the pod",
                },
            )
        )
