import httpx
import pytest
from tesserix_adk.testing import FakeModelProvider, ScriptedTurn

from k8s_fixtures import ReplayTransport, json_body, text_body
from sre_agent import tools
from sre_agent.cluster import KubernetesReader
from sre_agent.runtime import InvestigationFailedError, InvestigationService

POD_NAME = "marketplace-order-service-7c9c6bd4f-lm8vt"
PODS = "/api/v1/namespaces/marketplace/pods"
LOGS = f"{PODS}/{POD_NAME}/log"

FINDINGS = {
    "summary": "marketplace-order-service is crash looping on a database timeout.",
    "incident_suspected": True,
    "symptoms": ["The pod has restarted 7 times."],
    "evidence": [
        {
            "tool": "get_pod_logs",
            "subject": f"marketplace/{POD_NAME}",
            "observation": "panic: database timeout after 30s",
        }
    ],
    "hypothesis": "The service cannot reach its database and exits during start-up.",
    "confidence": "medium",
    "recommended_actions": [
        {
            "action": "Check the CNPG cluster in the marketplace namespace.",
            "reason": "Every replica fails at the same call.",
            "urgency": "now",
        }
    ],
    "affected_apps": ["marketplace-order-service"],
}


@pytest.fixture(autouse=True)
def _cluster():
    transport = ReplayTransport(
        {
            PODS: json_body("pods"),
            LOGS: text_body("panic: database timeout after 30s"),
        }
    )
    client = httpx.AsyncClient(transport=transport, base_url="https://kubernetes.test")
    tools.use_cluster(KubernetesReader(client))
    yield transport
    tools.use_cluster(None)


def service(*turns: ScriptedTurn) -> InvestigationService:
    return InvestigationService(provider=FakeModelProvider(*turns))


async def test_an_investigation_reads_the_cluster_then_answers_in_the_reviewed_shape() -> None:
    run = service(
        ScriptedTurn.calling("list_pods", {"namespace": "marketplace"}),
        ScriptedTurn.calling("get_pod_logs", {"namespace": "marketplace", "name": POD_NAME}),
        ScriptedTurn.returning(FINDINGS),
    )

    result = await run.investigate("marketplace-order-service is unhealthy. Investigate.")

    assert result.findings.incident_suspected is True
    assert result.findings.evidence[0].tool == "get_pod_logs"
    assert result.tools_called == ("list_pods", "get_pod_logs")


async def test_a_call_to_a_tool_outside_the_allowlist_ends_the_run(_cluster) -> None:
    run = service(
        ScriptedTurn.calling("delete_pod", {"namespace": "marketplace", "name": POD_NAME}),
        ScriptedTurn.returning(FINDINGS),
    )

    with pytest.raises(InvestigationFailedError) as raised:
        await run.investigate("Restart the order service.")

    assert "delete_pod" in raised.value.detail
    assert _cluster.requests == []


async def test_an_answer_that_is_not_an_investigation_is_a_failed_run() -> None:
    run = service(ScriptedTurn.saying("it looks bad out there"))

    with pytest.raises(InvestigationFailedError):
        await run.investigate("What is wrong?")
