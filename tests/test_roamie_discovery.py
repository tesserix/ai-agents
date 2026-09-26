import copy

import httpx
import pytest
from tesserix_adk.a2a.discovery import PeerDiscoveryError

from roamie_agents.contracts import Specialist
from roamie_agents.discovery import RegistryWorkers

ORIGIN = "http://agentgateway-mcp.agentgateway-system.svc.cluster.local:8082"


def graph():
    return {
        "agent": {
            "metadata": {"name": "roamie-food-agent", "namespace": "roamie", "tag": "1.0.2"},
            "spec": {"a2a": {"url": ORIGIN + "/a2a/v1/roamie-food"}},
        },
        "resolved": {
            kind: [{"metadata": {"name": name, "namespace": "roamie", "tag": version}}]
            for kind, name, version in (
                ("skills", "roamie-food", "1.0.2"),
                ("tools", "roamie-travel-search", "1.0.1"),
                ("mcpServers", "roamie-travel-mcp", "1.0.1"),
            )
        },
    }


async def test_registry_worker_resolves_pinned_graph_and_caches():
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json=graph())

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        workers = RegistryWorkers(
            http, registry_origin="https://registry.example", gateway_origin=ORIGIN
        )
        first = await workers.find(Specialist.FOOD)
        assert first.name == "roamie-food"
        assert first.url == ORIGIN + "/a2a/v1/roamie-food"
        assert await workers.find(Specialist.FOOD) == first
        assert len(calls) == 1
        assert calls[0].url.path == "/v0/agents/roamie-food-agent/1.0.2/resolved"
        assert calls[0].url.params["namespace"] == "roamie"


@pytest.mark.parametrize(
    "fault", ["unresolved", "host", "path", "tenant", "version", "missing_tool"]
)
async def test_registry_worker_rejects_unsafe_or_incomplete_graph(fault):
    document = copy.deepcopy(graph())
    if fault == "unresolved":
        document["unresolved"] = [{"ref": "missing"}]
    elif fault == "host":
        document["agent"]["spec"]["a2a"]["url"] = "http://169.254.169.254/latest"
    elif fault == "path":
        document["agent"]["spec"]["a2a"]["url"] = ORIGIN + "/a2a/v1/roamie-shopping"
    elif fault == "tenant":
        document["agent"]["metadata"]["namespace"] = "other"
    elif fault == "version":
        document["resolved"]["skills"][0]["metadata"]["tag"] = "2.0.0"
    else:
        document["resolved"]["tools"] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=document))
    ) as http:
        workers = RegistryWorkers(
            http, registry_origin="https://registry.example", gateway_origin=ORIGIN
        )
        with pytest.raises(PeerDiscoveryError):
            await workers.find(Specialist.FOOD)


async def test_manager_rejects_unavailable_registry_before_worker_invocation():
    from pydantic import SecretStr
    from tesserix_adk.testing import ScriptedProvider

    from orchestrator_agent.supervision import SupervisorService
    from orchestrator_agent.workers import A2AWorkerClient
    from roamie_agents.contracts import RecommendationRequest
    from roamie_agents.manager import PersonalTripManager, Profile
    from roamie_agents.runtime import TravelFailure

    calls = []

    def unavailable(request):
        calls.append(request.method)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as http:
        manager = PersonalTripManager(
            workers={},
            discovery=RegistryWorkers(
                http, registry_origin="https://registry.example", gateway_origin=ORIGIN
            ),
            client=A2AWorkerClient(api_key=SecretStr("fixture"), timeout=2, client=http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            identity_key=SecretStr("a" * 32),
            delegation_key=SecretStr("d" * 32),
        )
        with pytest.raises(TravelFailure, match="manager_review_unavailable"):
            await manager.manage(
                profile=Profile(subject="user", trip_id="trip", revision="1"),
                current_revision=lambda: "1",
                specialist=Specialist.FOOD,
                request=RecommendationRequest(prompt="Dinner"),
                facts=[],
            )
        assert calls == ["GET"]


@pytest.mark.parametrize("status,body", [(302, b""), (200, b"x" * 262145), (200, b"not-json")])
async def test_registry_rejects_redirects_oversized_and_malformed_responses(status, body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, content=body, headers={"Location": "http://other"})
        )
    ) as http:
        workers = RegistryWorkers(
            http, registry_origin="https://registry.example", gateway_origin=ORIGIN
        )
        with pytest.raises(PeerDiscoveryError):
            await workers.find(Specialist.FOOD)
