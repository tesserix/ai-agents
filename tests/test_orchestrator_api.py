"""The orchestrator HTTP edge, with both runtimes stood in by fakes."""

import json

import httpx
import pytest
from pydantic import SecretStr

from orchestrator_agent.api import create_app
from orchestrator_agent.config import Settings
from orchestrator_agent.definitions import Verdict
from orchestrator_agent.orchestration import (
    OrchestrationReport,
    StepReport,
    UnsupportedTaskError,
)
from orchestrator_agent.supervision import SupervisionFailedError, SupervisionRun

VERDICT = Verdict(decision="approve", confidence=0.9, summary="Fine.")
REPORT = OrchestrationReport(
    run_id="orch_1",
    task="delegate",
    ok=True,
    steps=(StepReport(agent="coach", state="completed", ok=True, note="answer"),),
    output="answer",
)


class FakeOrchestrator:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.tasks: list[str] = []

    async def run(self, raw_task: str, *, tenant: str) -> OrchestrationReport:
        if self._fail:
            raise UnsupportedTaskError("the orchestrator takes a JSON task object")
        self.tasks.append(raw_task)
        return REPORT

    def cards(self) -> tuple[dict[str, object], ...]:
        return (
            {"name": "orchestrator", "version": "1.0.0", "workers": ["coach"]},
            {"name": "supervisor", "version": "1.0.0", "workers": ["coach"]},
        )


class FakeSupervisor:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.asked: list[tuple[str, str, str]] = []

    async def supervise(
        self, *, task: str, answer: str, context: str = "", tenant: str = "tesserix"
    ) -> SupervisionRun:
        if self._fail:
            raise SupervisionFailedError("failed")
        self.asked.append((task, answer, context))
        return SupervisionRun(run_id="sup_1", verdict=VERDICT)


def client(
    orchestrator: FakeOrchestrator | None = None,
    supervisor: FakeSupervisor | None = None,
) -> httpx.AsyncClient:
    app = create_app(
        settings=Settings(
            api_key=SecretStr("edge-key"),
            worker_api_key=SecretStr("worker-key"),
            gateway_api_key=SecretStr("gateway-key"),
        ),
        orchestrator=orchestrator if orchestrator is not None else FakeOrchestrator(),
        supervisor=supervisor if supervisor is not None else FakeSupervisor(),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://edge",
        headers={"Authorization": "Bearer edge-key"},
    )


def a2a_body(text: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
    }


async def test_health_probes_need_no_credential() -> None:
    async with client() as edge:
        edge.headers.pop("Authorization")
        assert (await edge.get("/healthz")).status_code == 200
        assert (await edge.get("/readyz")).status_code == 200


async def test_everything_else_requires_the_bearer_key() -> None:
    async with client() as edge:
        edge.headers["Authorization"] = "Bearer wrong"
        for call in (
            edge.get("/v1/agents"),
            edge.post("/v1/orchestrations", json={"task": "{}"}),
            edge.post("/v1/supervisions", json={"task": "t", "answer": "a"}),
            edge.post("/a2a/v1/orchestrator", json=a2a_body("{}")),
        ):
            response = await call
            assert response.status_code == 401
            assert response.json()["code"] == "unauthorized"


async def test_orchestration_endpoint_returns_the_report() -> None:
    orchestrator = FakeOrchestrator()
    async with client(orchestrator) as edge:
        response = await edge.post("/v1/orchestrations", json={"task": '{"task": "status"}'})

    assert response.status_code == 200
    assert response.json()["run_id"] == "orch_1"
    assert orchestrator.tasks == ['{"task": "status"}']


async def test_unsupported_task_maps_to_422() -> None:
    async with client(FakeOrchestrator(fail=True)) as edge:
        response = await edge.post("/v1/orchestrations", json={"task": "free text"})

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_task"


async def test_supervision_endpoint_returns_the_verdict() -> None:
    supervisor = FakeSupervisor()
    async with client(supervisor=supervisor) as edge:
        response = await edge.post(
            "/v1/supervisions",
            json={"task": "t", "answer": "a", "context": "c"},
        )

    assert response.status_code == 200
    assert response.json()["verdict"]["decision"] == "approve"
    assert supervisor.asked == [("t", "a", "c")]


async def test_supervision_failure_maps_to_502_without_internals() -> None:
    async with client(supervisor=FakeSupervisor(fail=True)) as edge:
        response = await edge.post("/v1/supervisions", json={"task": "t", "answer": "a"})

    assert response.status_code == 502
    assert response.json()["code"] == "supervision_failed"


async def test_a2a_serves_the_orchestrator() -> None:
    async with client() as edge:
        response = await edge.post("/a2a/v1/orchestrator", json=a2a_body('{"task": "status"}'))

    body = response.json()
    assert response.status_code == 200
    assert body["result"]["id"] == "orch_1"
    text = body["result"]["artifacts"][0]["parts"][0]["text"]
    assert json.loads(text)["ok"] is True


async def test_a2a_serves_the_supervisor_with_a_structured_request() -> None:
    supervisor = FakeSupervisor()
    async with client(supervisor=supervisor) as edge:
        structured = json.dumps({"task": "t", "answer": "a", "context": "c"})
        response = await edge.post("/a2a/v1/supervisor", json=a2a_body(structured))

    assert response.status_code == 200
    assert supervisor.asked == [("t", "a", "c")]
    verdict = json.loads(response.json()["result"]["artifacts"][0]["parts"][0]["text"])
    assert verdict["decision"] == "approve"


async def test_a2a_judges_bare_text_as_an_answer() -> None:
    supervisor = FakeSupervisor()
    async with client(supervisor=supervisor) as edge:
        response = await edge.post("/a2a/v1/supervisor", json=a2a_body("just an answer"))

    assert response.status_code == 200
    (asked,) = supervisor.asked
    assert asked[1] == "just an answer"


async def test_a2a_rejects_unknown_agents_and_oversized_prompts() -> None:
    async with client() as edge:
        missing = await edge.post("/a2a/v1/mystery", json=a2a_body("{}"))
        oversized = await edge.post("/a2a/v1/orchestrator", json=a2a_body("x" * 13_000))

    assert missing.status_code == 404
    assert oversized.status_code == 422


async def test_cards_are_served_per_agent_and_in_the_listing() -> None:
    async with client() as edge:
        listing = await edge.get("/v1/agents")
        card = await edge.get("/a2a/v1/supervisor/card")
        missing = await edge.get("/a2a/v1/mystery/card")

    assert [each["name"] for each in listing.json()["agents"]] == [
        "orchestrator",
        "supervisor",
    ]
    assert card.json()["name"] == "supervisor"
    assert missing.status_code == 404


async def test_responses_carry_a_request_id_for_tracing() -> None:
    async with client() as edge:
        response = await edge.get("/healthz")

    assert len(response.headers["X-Request-ID"]) == 32


@pytest.mark.parametrize("namespace_field", ["namespace", "tenantId"])
def test_registry_manifests_publish_the_global_tenant(namespace_field: str) -> None:
    from pathlib import Path

    import yaml

    for name in ("supervisor", "orchestrator"):
        manifest = yaml.safe_load(Path(f"registry/{name}.yaml").read_text())
        assert manifest["metadata"][namespace_field] == "tesserix"
        assert manifest["metadata"]["tag"] == "1.0.0"
        assert manifest["spec"]["a2a"]["url"].endswith(f"/a2a/v1/{name}")
        for skill in manifest["spec"]["skills"]:
            assert len(skill["description"]) >= 80
            assert len(skill["tags"]) >= 3
