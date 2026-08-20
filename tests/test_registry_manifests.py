from pathlib import Path

import yaml


def test_registry_manifests_are_tenant_scoped_a2a_agents() -> None:
    manifests = [
        yaml.safe_load(path.read_text()) for path in sorted(Path("registry").glob("*.yaml"))
    ]

    assert {manifest["metadata"]["name"] for manifest in manifests} == {
        "meal-planner",
        "nutrition-coach",
    }
    for manifest in manifests:
        assert manifest["apiVersion"] == "registry.agentic.dev/v1alpha1"
        assert manifest["kind"] == "Agent"
        assert manifest["metadata"]["namespace"] == "kora"
        assert manifest["metadata"]["tenantId"] == "kora"
        assert manifest["metadata"]["tag"] == "1.0.1"
        assert manifest["metadata"]["visibility"] == "public"
        assert manifest["spec"]["a2a"]["preferredTransport"] == "JSONRPC"
        assert manifest["spec"]["a2a"]["url"].startswith(
            "http://kora-ai.agentgateway-system.svc.cluster.local:8080/a2a/v1/"
        )
