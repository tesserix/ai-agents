import httpx
import pytest
from tesserix_adk.tools import ToolFailure, ToolRefusal

from k8s_fixtures import ReplayTransport, json_body, raises, status, text_body
from sre_agent.cluster import MAX_LOG_LINES, KubernetesReader

PODS = "/api/v1/namespaces/marketplace/pods"
POD = f"{PODS}/marketplace-order-service-7c9c6bd4f-lm8vt"
LOGS = f"{POD}/log"
EVENTS = "/api/v1/namespaces/marketplace/events"
DEPLOYMENTS = "/apis/apps/v1/namespaces/marketplace/deployments"


def reader(transport: ReplayTransport, **kwargs) -> KubernetesReader:
    client = httpx.AsyncClient(transport=transport, base_url="https://kubernetes.test")
    return KubernetesReader(client, **kwargs)


async def test_list_pods_summarises_each_pod_and_reads_only() -> None:
    transport = ReplayTransport({PODS: json_body("pods")})

    pods = await reader(transport).list_pods("marketplace")

    crashing = pods[1]
    assert crashing.name == "marketplace-order-service-7c9c6bd4f-lm8vt"
    assert crashing.phase == "Running"
    assert crashing.ready is False
    assert crashing.restarts == 7
    assert crashing.node == "gke-tesseract-prod-in-gke-pool-1-a1b2c3-p7q2"
    assert crashing.containers[0].reason == "CrashLoopBackOff"
    assert crashing.containers[0].exit_code == 2
    assert crashing.containers[0].image == "ghcr.io/tesserix/marketplace-order-service:2.11.0"
    assert transport.requests[0].method == "GET"


async def test_list_pods_bounds_the_payload_the_model_sees() -> None:
    transport = ReplayTransport({PODS: json_body("pods")})

    pods = await reader(transport).list_pods("marketplace", limit=1)

    assert len(pods) == 1
    assert transport.requests[0].url.params["limit"] == "1"


async def test_list_pods_passes_a_label_selector_through() -> None:
    transport = ReplayTransport({PODS: json_body("pods")})

    await reader(transport).list_pods("marketplace", label_selector="app=marketplace-order-service")

    params = transport.requests[0].url.params
    assert params["labelSelector"] == "app=marketplace-order-service"


async def test_a_namespace_outside_the_allowlist_is_refused_before_any_request() -> None:
    transport = ReplayTransport({PODS: json_body("pods")})

    with pytest.raises(ToolRefusal, match="namespace_not_permitted"):
        await reader(transport, namespaces=("kora",)).list_pods("marketplace")

    assert transport.requests == []


async def test_get_pod_reports_why_a_container_is_not_ready() -> None:
    transport = ReplayTransport({POD: json_body("pod")})

    pod = await reader(transport).get_pod(
        "marketplace", "marketplace-order-service-7c9c6bd4f-lm8vt"
    )

    assert pod.ready is False
    assert pod.restarts == 7
    assert pod.containers[0].reason == "CrashLoopBackOff"
    assert "back-off 5m0s" in pod.containers[0].message


async def test_pod_logs_return_the_tail_and_say_when_they_were_cut() -> None:
    lines = "\n".join(f"line {number}" for number in range(MAX_LOG_LINES + 40))
    transport = ReplayTransport({LOGS: text_body(lines)})

    logs = await reader(transport).pod_logs(
        "marketplace", "marketplace-order-service-7c9c6bd4f-lm8vt", container="server"
    )

    assert len(logs.lines) == MAX_LOG_LINES
    assert logs.lines[-1] == f"line {MAX_LOG_LINES + 39}"
    assert logs.truncated is True
    params = transport.requests[0].url.params
    assert params["container"] == "server"
    assert params["tailLines"] == str(MAX_LOG_LINES)


async def test_pod_logs_can_read_the_previous_container_after_a_restart() -> None:
    transport = ReplayTransport({LOGS: text_body("panic: database timeout")})

    logs = await reader(transport).pod_logs(
        "marketplace",
        "marketplace-order-service-7c9c6bd4f-lm8vt",
        previous=True,
        tail_lines=20,
    )

    assert logs.lines == ("panic: database timeout",)
    assert logs.truncated is False
    params = transport.requests[0].url.params
    assert params["previous"] == "true"
    assert params["tailLines"] == "20"


async def test_list_events_summarises_warnings_with_their_object() -> None:
    transport = ReplayTransport({EVENTS: json_body("events")})

    events = await reader(transport).list_events("marketplace")

    assert events[0].type == "Warning"
    assert events[0].reason == "BackOff"
    assert events[0].count == 12
    assert events[0].object == "Pod/marketplace-order-service-7c9c6bd4f-lm8vt"
    assert events[0].last_seen == "2026-08-29T08:15:02Z"
    assert events[1].last_seen == "2026-08-29T07:52:50Z"


async def test_list_events_can_ask_for_warnings_only() -> None:
    transport = ReplayTransport({EVENTS: json_body("events")})

    await reader(transport).list_events("marketplace", warnings_only=True)

    assert transport.requests[0].url.params["fieldSelector"] == "type=Warning"


async def test_list_deployments_reports_rollout_health() -> None:
    transport = ReplayTransport({DEPLOYMENTS: json_body("deployments")})

    deployments = await reader(transport).list_deployments("marketplace")

    deployment = deployments[0]
    assert deployment.name == "marketplace-order-service"
    assert deployment.desired == 3
    assert deployment.ready == 2
    assert deployment.available == 2
    assert deployment.images == ("ghcr.io/tesserix/marketplace-order-service:2.11.0",)
    assert deployment.conditions[0].reason == "MinimumReplicasUnavailable"


async def test_get_deployment_reads_the_named_deployment() -> None:
    transport = ReplayTransport(
        {f"{DEPLOYMENTS}/marketplace-order-service": json_body("deployment")}
    )

    deployment = await reader(transport).get_deployment("marketplace", "marketplace-order-service")

    assert deployment.name == "marketplace-order-service"
    assert deployment.generation == deployment.observed_generation == 14
    assert transport.requests[0].url.path == f"{DEPLOYMENTS}/marketplace-order-service"


async def test_a_missing_object_is_an_answer_rather_than_a_fault() -> None:
    transport = ReplayTransport({POD: status(404, "pods 'x' not found")})

    with pytest.raises(ToolRefusal, match="not_found"):
        await reader(transport).get_pod("marketplace", "marketplace-order-service-7c9c6bd4f-lm8vt")


async def test_a_forbidden_read_is_refused_and_never_retried() -> None:
    transport = ReplayTransport({PODS: status(403, "forbidden")})

    with pytest.raises(ToolRefusal, match="forbidden"):
        await reader(transport).list_pods("marketplace")


async def test_an_api_server_fault_is_transient() -> None:
    transport = ReplayTransport({PODS: status(503, "apiserver unavailable")})

    with pytest.raises(ToolFailure) as raised:
        await reader(transport).list_pods("marketplace")

    assert raised.value.retryable is True


async def test_a_network_timeout_is_transient() -> None:
    transport = ReplayTransport({PODS: raises(httpx.ConnectTimeout("timed out"))})

    with pytest.raises(ToolFailure) as raised:
        await reader(transport).list_pods("marketplace")

    assert raised.value.retryable is True


async def test_an_unauthenticated_read_is_a_wiring_fault_no_retry_fixes() -> None:
    transport = ReplayTransport({PODS: status(401, "unauthorized")})

    with pytest.raises(ToolFailure) as raised:
        await reader(transport).list_pods("marketplace")

    assert raised.value.retryable is False
