import httpx
import pytest
from tesserix_adk.core import ToolArgumentValidationError
from tesserix_adk.core.idempotency import Idempotency
from tesserix_adk.tools import ToolFailure, ToolRefusal

from k8s_fixtures import ReplayTransport, json_body, status, text_body
from sre_agent import tools
from sre_agent.cluster import KubernetesReader

PODS = "/api/v1/namespaces/marketplace/pods"
POD = f"{PODS}/marketplace-order-service-7c9c6bd4f-lm8vt"
DEPLOYMENTS = "/apis/apps/v1/namespaces/marketplace/deployments"


@pytest.fixture(autouse=True)
def _no_ambient_cluster():
    tools.use_cluster(None)
    yield
    tools.use_cluster(None)


def serving(responses: dict, **kwargs) -> ReplayTransport:
    transport = ReplayTransport(responses)
    client = httpx.AsyncClient(transport=transport, base_url="https://kubernetes.test")
    tools.use_cluster(KubernetesReader(client, **kwargs))
    return transport


async def test_every_tool_declares_itself_read_only() -> None:
    assert tools.TOOLS
    for tool in tools.TOOLS:
        assert tool.idempotency is not None
        assert tool.idempotency.kind is Idempotency.READ_ONLY, tool.name
        assert tool.timeout is not None
        assert tool.approval.required is False


async def test_the_registry_offers_exactly_the_documented_tools() -> None:
    assert set(tools.registry().names) == {
        "list_pods",
        "get_pod",
        "get_pod_logs",
        "list_events",
        "list_deployments",
        "get_deployment",
    }


async def test_a_tool_schema_describes_only_arguments_a_model_may_choose() -> None:
    schema = tools.list_pods.parameters_schema

    assert schema["required"] == ["namespace"]
    assert set(schema["properties"]) == {"namespace", "label_selector", "limit"}
    assert schema["properties"]["namespace"]["description"]
    assert schema["properties"]["namespace"]["maxLength"] == 63
    assert schema["properties"]["limit"]["minimum"] == 1
    assert schema["properties"]["limit"]["maximum"] == 50


async def test_list_pods_returns_summaries_a_model_can_read() -> None:
    serving({PODS: json_body("pods")})

    pods = await tools.list_pods.invoke({"namespace": "marketplace", "limit": 2})

    assert [pod.name for pod in pods] == [
        "kora-ai-5f7d9c8b4d-2xkqp",
        "marketplace-order-service-7c9c6bd4f-lm8vt",
    ]


async def test_an_argument_the_schema_never_offered_is_refused_before_the_cluster() -> None:
    transport = serving({PODS: json_body("pods")})

    with pytest.raises(ToolArgumentValidationError):
        await tools.list_pods.invoke({"namespace": "marketplace", "delete": True})

    assert transport.requests == []


async def test_a_resource_name_cannot_traverse_into_another_api_path() -> None:
    transport = serving({})

    with pytest.raises(ToolArgumentValidationError):
        await tools.get_pod.invoke(
            {"namespace": "marketplace", "name": "../secrets/database-credentials"}
        )

    assert transport.requests == []


async def test_get_pod_logs_reads_the_previous_instance_when_asked() -> None:
    transport = serving({f"{POD}/log": text_body("panic: database timeout")})

    logs = await tools.get_pod_logs.invoke(
        {
            "namespace": "marketplace",
            "name": "marketplace-order-service-7c9c6bd4f-lm8vt",
            "previous": True,
        }
    )

    assert logs.lines == ("panic: database timeout",)
    assert transport.requests[0].url.params["previous"] == "true"


async def test_list_events_can_narrow_to_warnings() -> None:
    transport = serving({"/api/v1/namespaces/marketplace/events": json_body("events")})

    events = await tools.list_events.invoke({"namespace": "marketplace", "warnings_only": True})

    assert events[0].reason == "BackOff"
    assert transport.requests[0].url.params["fieldSelector"] == "type=Warning"


async def test_get_deployment_reports_the_rollout() -> None:
    serving({f"{DEPLOYMENTS}/marketplace-order-service": json_body("deployment")})

    deployment = await tools.get_deployment.invoke(
        {"namespace": "marketplace", "name": "marketplace-order-service"}
    )

    assert deployment.ready == 2
    assert deployment.desired == 3


async def test_list_deployments_lists_the_namespace() -> None:
    serving({DEPLOYMENTS: json_body("deployments")})

    deployments = await tools.list_deployments.invoke({"namespace": "marketplace"})

    assert [each.name for each in deployments] == ["marketplace-order-service"]


async def test_get_pod_surfaces_the_container_state() -> None:
    serving({POD: json_body("pod")})

    pod = await tools.get_pod.invoke(
        {"namespace": "marketplace", "name": "marketplace-order-service-7c9c6bd4f-lm8vt"}
    )

    assert pod.containers[0].reason == "CrashLoopBackOff"


async def test_a_tool_called_before_the_cluster_is_wired_fails_rather_than_guesses() -> None:
    with pytest.raises(ToolFailure, match="cluster_not_configured"):
        await tools.list_pods.invoke({"namespace": "marketplace"})


async def test_the_namespace_allowlist_reaches_the_model_as_a_refusal() -> None:
    serving({PODS: json_body("pods")}, namespaces=("kora",))

    with pytest.raises(ToolRefusal, match="namespace_not_permitted"):
        await tools.list_pods.invoke({"namespace": "marketplace"})


async def test_a_forbidden_read_reaches_the_model_as_a_refusal() -> None:
    serving({PODS: status(403, "forbidden")})

    with pytest.raises(ToolRefusal, match="forbidden"):
        await tools.list_pods.invoke({"namespace": "marketplace"})
