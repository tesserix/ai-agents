from pathlib import Path
from typing import Any

import pytest

from kora_agents.definitions import DEFINITIONS, DayPlan, Meal, MealPlan


def test_agents_are_reviewable_bounded_adk_definitions() -> None:
    assert set(DEFINITIONS) == {"meal-planner", "nutrition-coach", "plan-supervisor"}

    for definition in DEFINITIONS.values():
        agent = definition.agent
        assert definition.owner.team == "kora"
        assert definition.evaluation_suite.startswith("evals/")
        assert Path(definition.evaluation_suite).is_file()
        assert agent.tools == ()
        assert agent.budget is not None
        assert agent.budget.max_input_tokens == 12_000
        assert agent.budget.max_output_tokens == 12_000
        assert agent.budget.max_model_calls == 2
        assert agent.guardrails == ("pii", "injection", "medical_safety")
        assert "CONTEXT" in agent.instructions
        assert "Never invent a number absent from CONTEXT" in agent.instructions
        assert "Reviewed nutrition reference facts" in agent.instructions
        assert "per 100g" in agent.instructions
        assert "[cite:fact_id]" in agent.instructions

    assert DEFINITIONS["nutrition-coach"].agent.version == "1.0.2"
    assert DEFINITIONS["meal-planner"].agent.version == "1.0.3"
    assert DEFINITIONS["plan-supervisor"].agent.version == "1.0.3"

    assert DEFINITIONS["meal-planner"].agent.budget.max_seconds == 55.0
    assert DEFINITIONS["nutrition-coach"].agent.budget.max_seconds == 55.0

    assert DEFINITIONS["meal-planner"].agent.output_type is MealPlan
    assert DEFINITIONS["nutrition-coach"].agent.free_text is True
    assert DEFINITIONS["plan-supervisor"].agent.free_text is True

    supervisor = DEFINITIONS["plan-supervisor"].agent
    assert "health" in supervisor.instructions.lower()
    assert "habits" in supervisor.instructions.lower()
    assert "[[KORA_REVIEWED_PLAN]]" in supervisor.instructions
    assert "preparation" in supervisor.instructions.lower()
    assert "1-6 meals" in supervisor.instructions
    assert "arbitrary display labels" in supervisor.instructions


def _longest_meal_plan(days: int) -> dict[str, object]:
    fields = MealPlan.model_fields
    day_fields = DayPlan.model_fields
    meal_fields = Meal.model_fields

    def longest(model_fields: dict[str, Any], name: str) -> str:
        return "x" * _max_length(model_fields[name])

    meal = {name: longest(meal_fields, name) for name in ("name", "description", "preparation")}
    return {
        "summary": longest(fields, "summary"),
        "days": [
            {
                "date": longest(day_fields, "date"),
                "meals": [meal] * _max_length(day_fields["meals"]),
            }
            for _ in range(days)
        ],
    }


def _max_length(field: Any) -> int:
    return next(m.max_length for m in field.metadata if getattr(m, "max_length", None))


def test_the_largest_valid_plan_fits_the_supervisors_output_budget() -> None:
    max_days = _max_length(MealPlan.model_fields["days"])
    plan = MealPlan.model_validate(_longest_meal_plan(max_days))

    # JSON tokenises at roughly three characters per token; the supervisor needs the rest for prose.
    plan_tokens = len(plan.model_dump_json()) / 3
    supervisor_budget = DEFINITIONS["plan-supervisor"].agent.budget
    assert supervisor_budget is not None
    assert plan_tokens <= supervisor_budget.max_output_tokens * 2 / 3


def test_meal_plan_accepts_one_week_and_rejects_more() -> None:
    eight_days = _longest_meal_plan(8)["days"]
    assert isinstance(eight_days, list)

    accepted = MealPlan.model_validate({"summary": "Plan", "days": eight_days[:7]})
    assert len(accepted.days) == 7

    with pytest.raises(ValueError):
        MealPlan.model_validate({"summary": "Plan", "days": eight_days})


def test_meal_plan_accepts_display_day_labels() -> None:
    meal_plan = MealPlan.model_validate(
        {
            "summary": "Plan",
            "days": [
                {
                    "date": "Day 1",
                    "meals": [
                        {
                            "name": "Breakfast",
                            "description": "Oats",
                            "preparation": "Simmer the oats until creamy.",
                        }
                    ],
                }
            ],
        }
    )

    assert meal_plan.days[0].date == "Day 1"


@pytest.mark.parametrize("label", ["", "x" * 41], ids=["empty", "overlong"])
def test_meal_plan_rejects_invalid_display_day_labels(label: str) -> None:
    with pytest.raises(ValueError):
        MealPlan.model_validate(
            {
                "summary": "Plan",
                "days": [
                    {
                        "date": label,
                        "meals": [
                            {
                                "name": "Breakfast",
                                "description": "Oats",
                                "preparation": "Simmer the oats until creamy.",
                            }
                        ],
                    }
                ],
            }
        )


def test_meal_plan_requires_reviewable_preparation_for_every_meal() -> None:
    with pytest.raises(ValueError):
        MealPlan.model_validate(
            {
                "summary": "Plan",
                "days": [
                    {
                        "date": "Any day label",
                        "meals": [{"name": "Breakfast", "description": "Oats"}],
                    }
                ],
            }
        )
