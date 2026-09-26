import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from tesserix_adk.core import ModelCapabilities
from tesserix_adk.runtime import ModelResponse
from tesserix_adk.testing import ScriptedProvider

from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist
from roamie_agents.runtime import TravelService


@pytest.mark.parametrize("suite", sorted(Path("evals/roamie").glob("*.yaml")), ids=lambda p: p.stem)
async def test_reviewed_specialist_evaluations(suite):
    document = yaml.safe_load(suite.read_text())
    for case in document["cases"]:
        service = TravelService(
            provider=ScriptedProvider(
                ModelResponse(
                    content=json.dumps(
                        {
                            "selected_ids": case["selection"],
                        }
                    )
                ),
                capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32768),
            ),
            clock=lambda: datetime.fromisoformat(document["clock"]),
        )
        result = await service.recommend(
            Specialist(document["specialist"]),
            RecommendationRequest.model_validate(case["request"]),
            facts=[Evidence.model_validate(fact) for fact in case["facts"]],
        )
        assert result.status == case["expected_status"], case["id"]
        assert [fact.id for fact in result.recommendations] == case["selection"], case["id"]
