from pathlib import Path

import yaml

from sre_agent.definitions import INVESTIGATOR

MANIFEST = yaml.safe_load(Path("registry/sre-investigator.yaml").read_text())


def test_the_manifest_publishes_the_definition_this_repository_reviewed() -> None:
    assert MANIFEST["apiVersion"] == "registry.agentic.dev/v1alpha1"
    assert MANIFEST["kind"] == "Agent"
    assert MANIFEST["metadata"]["name"] == INVESTIGATOR.agent.name
    assert MANIFEST["metadata"]["tag"] == INVESTIGATOR.agent.version
    assert MANIFEST["spec"]["model"]["name"] == INVESTIGATOR.agent.model


def test_the_agent_is_reachable_over_a2a_through_its_own_gateway_route() -> None:
    a2a = MANIFEST["spec"]["a2a"]

    assert a2a["preferredTransport"] == "JSONRPC"
    assert a2a["url"] == (
        "http://sre-ai.agentgateway-system.svc.cluster.local:8080/a2a/v1/sre-investigator"
    )
    assert a2a["capabilities"]["streaming"] is False


def test_a_cluster_reading_agent_is_not_published_to_the_world() -> None:
    assert MANIFEST["metadata"]["visibility"] == "private"
    assert MANIFEST["metadata"]["tenantId"] == "tesserix"
    assert MANIFEST["metadata"]["labels"]["ai.tesserix.dev/access"] == "read-only"


def test_the_published_skill_says_the_agent_only_reads() -> None:
    skills = MANIFEST["spec"]["skills"]

    assert len(skills) == 1
    description = skills[0]["description"].lower()
    assert len(description) >= 80
    assert "never acts" in description
    assert "untrusted" in description
    assert "read-only" in skills[0]["tags"]
