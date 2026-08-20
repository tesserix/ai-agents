from pathlib import Path

from tesserix_adk import __version__ as adk_version

from kora_agents.definitions import DEFINITIONS, MealPlan


def test_agents_are_reviewable_bounded_adk_definitions() -> None:
    assert adk_version == "0.50.0"
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
