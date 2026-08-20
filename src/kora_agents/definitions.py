"""Reviewable Kora agent definitions."""

from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field
from tesserix_adk.core import Agent, AgentDefinition, BudgetLimits, Owner


class Meal(BaseModel):
    """One meal in a generated plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)


class DayPlan(BaseModel):
    """A bounded day of meals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    meals: Annotated[list[Meal], Field(min_length=1, max_length=6)]


class MealPlan(BaseModel):
    """A meal plan that fits one API response and one ADK run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1, max_length=500)
    days: Annotated[list[DayPlan], Field(min_length=1, max_length=7)]


_OWNER = Owner(
    team="kora",
    contact="https://github.com/tesserix/ai-agents/issues",
    service="kora-ai-agents",
)
_BUDGET = BudgetLimits(
    max_input_tokens=12_000,
    max_output_tokens=2_000,
    max_model_calls=2,
    max_iterations=2,
    max_seconds=20.0,
)
_GUARDRAILS = ("pii", "injection", "medical_safety")
_SUPERVISOR_GROUNDING = (
    " When a supervisor request contains CONTEXT and QUESTION sections, treat CONTEXT as "
    "trusted application facts and answer the QUESTION only from those facts. Never invent a "
    "number absent from CONTEXT; say when the supplied context does not contain the answer."
)


def _definition(agent: Agent[Any]) -> AgentDefinition[Any]:
    return AgentDefinition.declared(
        agent=agent,
        owner=_OWNER,
        evaluation_suite=f"evals/{agent.name}.yaml",
        known_tools=(),
    )


DEFINITIONS: dict[str, AgentDefinition[Any]] = {
    "meal-planner": _definition(
        Agent(
            name="meal-planner",
            version="1.0.0",
            instructions=(
                "Create a practical meal plan from the user's stated preferences. "
                "Do not diagnose disease, prescribe treatment, or invent allergies. "
                "Keep recommendations varied, affordable, and explicit about uncertainty."
                + _SUPERVISOR_GROUNDING
            ),
            model="kora-auto",
            output_type=MealPlan,
            budget=_BUDGET,
            guardrails=_GUARDRAILS,
            metadata={"capability": "json", "context_kind": "structured"},
        )
    ),
    "nutrition-coach": _definition(
        Agent(
            name="nutrition-coach",
            version="1.0.0",
            instructions=(
                "Give concise, evidence-aware general nutrition guidance. "
                "Never diagnose, prescribe medication or supplements, or replace a clinician. "
                "For pregnancy, eating disorders, severe symptoms, or medication interactions, "
                "recommend an appropriately qualified health professional." + _SUPERVISOR_GROUNDING
            ),
            model="kora-auto",
            free_text=True,
            budget=_BUDGET,
            guardrails=_GUARDRAILS,
            metadata={"capability": "text", "context_kind": "conversation"},
        )
    ),
}
