from pathlib import Path

import pytest

from kora_agents.definitions import DEFINITIONS, MealPlan


def test_agents_are_reviewable_bounded_adk_definitions() -> None:
    assert set(DEFINITIONS) == {"meal-planner", "nutrition-coach"}

    for definition in DEFINITIONS.values():
        agent = definition.agent
        assert definition.owner.team == "kora"
        assert definition.evaluation_suite.startswith("evals/")
        assert Path(definition.evaluation_suite).is_file()
        assert agent.tools == ()
        assert agent.budget is not None
        assert agent.budget.max_input_tokens == 12_000
        assert agent.budget.max_output_tokens == 2_000
        assert agent.budget.max_model_calls == 2
        assert agent.guardrails == ("pii", "injection", "medical_safety")
        assert "CONTEXT" in agent.instructions
        assert "Never invent a number absent from CONTEXT" in agent.instructions

    assert DEFINITIONS["meal-planner"].agent.output_type is MealPlan
    assert DEFINITIONS["nutrition-coach"].agent.free_text is True


def test_meal_plan_rejects_unbounded_days() -> None:
    too_many_days = [
        {
            "date": f"2026-08-{day:02d}",
            "meals": [{"name": "Breakfast", "description": "Oats"}],
        }
        for day in range(1, 9)
    ]

    try:
        MealPlan.model_validate({"summary": "Plan", "days": too_many_days})
    except ValueError:
        pass
    else:
        raise AssertionError("meal plans must be limited to seven days")


def test_meal_plan_accepts_display_day_labels() -> None:
    meal_plan = MealPlan.model_validate(
        {
            "summary": "Plan",
            "days": [
                {
                    "date": "Day 1",
                    "meals": [{"name": "Breakfast", "description": "Oats"}],
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
                        "meals": [{"name": "Breakfast", "description": "Oats"}],
                    }
                ],
            }
        )
