from pathlib import Path

import pytest

from kora_agents.definitions import DEFINITIONS, MealPlan


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
        assert agent.version == "1.0.2"

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


def test_meal_plan_accepts_two_calendar_months_and_rejects_more() -> None:
    too_many_days = [
        {
            "date": f"Day {day}",
            "meals": [
                {
                    "name": "Breakfast",
                    "description": "Oats",
                    "preparation": "Simmer the oats until creamy.",
                }
            ],
        }
        for day in range(1, 64)
    ]

    accepted = MealPlan.model_validate({"summary": "Plan", "days": too_many_days[:62]})
    assert len(accepted.days) == 62

    try:
        MealPlan.model_validate({"summary": "Plan", "days": too_many_days})
    except ValueError:
        pass
    else:
        raise AssertionError("meal plans must be limited to 62 days")


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
