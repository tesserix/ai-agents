from pathlib import Path

import yaml

from kora_agents.definitions import DEFINITIONS


def test_registry_manifests_are_tenant_scoped_a2a_agents() -> None:
    published = [
        yaml.safe_load(path.read_text()) for path in sorted(Path("registry").glob("*.yaml"))
    ]
    manifests = [each for each in published if each["metadata"]["namespace"] == "kora"]

    assert {manifest["metadata"]["name"] for manifest in manifests} == {
        "meal-planner",
        "nutrition-coach",
        "plan-supervisor",
    }
    for manifest in manifests:
        assert manifest["apiVersion"] == "registry.agentic.dev/v1alpha1"
        assert manifest["kind"] == "Agent"
        assert manifest["metadata"]["namespace"] == "kora"
        assert manifest["metadata"]["tenantId"] == "kora"
        name = manifest["metadata"]["name"]
        assert manifest["metadata"]["tag"] == DEFINITIONS[name].agent.version
        assert manifest["metadata"]["visibility"] == "public"
        assert manifest["spec"]["a2a"]["preferredTransport"] == "JSONRPC"
        assert manifest["spec"]["a2a"]["url"].startswith(
            "http://kora-ai.agentgateway-system.svc.cluster.local:8080/a2a/v1/"
        )
        skills = manifest["spec"]["skills"]
        assert len(skills) == 1
        expected_skill = {
            "meal-planner": "plan-meals",
            "nutrition-coach": "nutrition-guidance",
            "plan-supervisor": "review-meal-plan",
        }
        assert skills[0]["id"] == expected_skill[name]
        assert len(skills[0]["description"]) >= 80
        assert "ground" in skills[0]["description"].lower()
        if name == "meal-planner":
            assert "62 days" in skills[0]["description"]
        if name == "plan-supervisor":
            assert "health" in skills[0]["description"].lower()
            assert "habit" in skills[0]["description"].lower()
        assert len(skills[0]["tags"]) >= 3
